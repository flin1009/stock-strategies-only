"""三大法人連續買超與連續賣超分析模組

支援證交所 (TWSE) 與 櫃買中心 (TPEx) 全市場 2,200+ 檔標的多空雙向初篩，
並結合自選股 (Watchlist) 交叉比對與近幾日價格漲跌幅統計。
"""
from __future__ import annotations

import time
from typing import Optional

import pandas as pd

from .cache import FinMindRateLimitError
from .exchange_data import (
    get_market_streak_candidates,
    get_market_margin_map,
    get_market_daily_price_map,
    get_market_recent_prices_map,
)
from .data import get_price_history
from .datasources import get_index_history
from .vcp import detect_vcp
from .bb_trend import detect_bb_trend


class ChipsScanResult(dict):
    """掃描結果容器：支援字典取值 (['buy_records'], ['sell_records']) 與向後相容的列表迭代。"""

    def __iter__(self):
        return iter(self.get("buy_records", []))

    def __getitem__(self, key):
        if isinstance(key, int):
            return self.get("buy_records", [])[key]
        return super().__getitem__(key)

    def __len__(self):
        return len(self.get("buy_records", []))


def calculate_streak(series: list[float] | pd.Series) -> int:
    """計算由最新交易日往前倒數的連續買超天數 (值 > 0)。"""
    if series is None:
        return 0
    vals = series.tolist() if isinstance(series, pd.Series) else list(series)
    streak = 0
    for v in reversed(vals):
        if pd.isna(v) or v <= 0:
            break
        streak += 1
    return streak


def enrich_candidate_price(
    candidate: dict,
    index_df: Optional[pd.DataFrame] = None,
    check_vcp: bool = True,
    margin_stocks: Optional[dict] = None,
    daily_price_map: Optional[dict] = None,
    recent_prices_map: Optional[dict] = None,
    finmind_state: Optional[dict] = None,
    use_finmind: bool = True,
) -> dict:
    """為篩選出的連買/連賣個股補上價格、漲跌幅、金額、融資與 VCP 偵測。

    以證交所/櫃買中心免費官方行情（近 5 日收盤/漲跌幅 + 融資）為主，
    FinMind 歷史日 K 僅作為輔助（用於買方主力標的 VCP 長週期型態偵測）。
    """
    sid = str(candidate["stock_id"])
    lots = candidate.get("streak_total_net_lots", 0)
    streak_len = max(1, candidate.get("main_streak", 3))

    # 1. 融入融資增減資訊（優先使用預先載入的快照）
    margin_diff = None
    margin_today = None
    try:
        m_map = margin_stocks if margin_stocks is not None else get_market_margin_map().get("stocks", {})
        if sid in m_map:
            margin_diff = m_map[sid].get("margin_diff")
            margin_today = m_map[sid].get("margin_today")
    except Exception:
        pass

    # 2. 【主資料源】優先使用證交所/櫃買免費近數日行情序列（0 FinMind 額度）
    recent_recs = (recent_prices_map or {}).get(sid) or []
    today_close: Optional[float] = None
    today_pct: float = 0.0
    streak_pct: float = 0.0
    streak_amount: float = 0.0
    recent_daily_pcts: list[str] = []

    if recent_recs:
        today_close = float(recent_recs[-1]["close"])
        today_pct = float(recent_recs[-1]["pct"])
        base_rec = recent_recs[-min(streak_len, len(recent_recs))]
        base_close = float(base_rec.get("prev_close") or base_rec["close"])
        streak_pct = round(((today_close - base_close) / base_close) * 100, 2) if base_close > 0 else 0.0
        streak_amount = round((lots * today_close * 1000) / 1e8, 2) if today_close > 0 else 0.0
        recent_slice = recent_recs[-max(3, min(streak_len, 5)):]
        recent_daily_pcts = [f"{r['pct']:+.1f}%" for r in recent_slice]

    # 3. 【輔助資料源】FinMind：僅在需要 VCP 檢測（或主資料缺漏）時才呼叫，遇限流即熔斷
    price_df = pd.DataFrame()
    need_finmind = use_finmind and (check_vcp or today_close is None)
    if need_finmind and not (finmind_state and finmind_state.get("rate_limited")):
        try:
            price_df = get_price_history(sid, years=1)
        except FinMindRateLimitError:
            if finmind_state is not None and not finmind_state.get("rate_limited"):
                finmind_state["rate_limited"] = True
                print("⚠️ FinMind 達請求上限，已自動切換為純證交所/櫃買免費行情模式")
            price_df = pd.DataFrame()
        except Exception:
            price_df = pd.DataFrame()

    # 若主資料缺漏但 FinMind 有抓到歷史資料，從 FinMind 補齊價格與漲跌幅
    if today_close is None and not price_df.empty and "close" in price_df.columns and len(price_df) >= 2:
        price_df = price_df.sort_values("date").reset_index(drop=True)
        price_df["daily_pct"] = (price_df["close"].pct_change() * 100).round(2)
        latest = price_df.iloc[-1]
        today_close = float(latest["close"])
        today_pct = float(latest["daily_pct"]) if pd.notna(latest["daily_pct"]) else 0.0
        if len(price_df) > streak_len:
            base_close = float(price_df.iloc[-streak_len - 1]["close"])
        else:
            base_close = float(price_df.iloc[0]["close"])
        streak_pct = round(((today_close - base_close) / base_close) * 100, 2) if base_close > 0 else 0.0
        streak_amount = round((lots * today_close * 1000) / 1e8, 2) if today_close > 0 else 0.0
        recent_slice = price_df.tail(max(3, min(streak_len, 5)))
        recent_daily_pcts = [
            f"{r['daily_pct']:+.1f}%" if pd.notna(r["daily_pct"]) else "0.0%"
            for _, r in recent_slice.iterrows()
        ]

    # 若兩者皆無，再退回單日免費收盤快照
    if today_close is None:
        snap = (daily_price_map or {}).get(sid)
        if snap and snap.get("close", 0) > 0:
            today_close = float(snap["close"])
            today_pct = float(snap.get("pct", 0.0))
            streak_pct = today_pct
            streak_amount = round((lots * today_close * 1000) / 1e8, 2)
            recent_daily_pcts = [f"{today_pct:+.1f}%"]
        else:
            today_close = 0.0

    vcp_signal = None
    vcp_detail = ""
    vcp_pivot = None
    if check_vcp and not price_df.empty and len(price_df) >= 220:
        try:
            vcp_res = detect_vcp(price_df, index_df=index_df)
            vcp_signal = vcp_res.get("signal")
            vcp_detail = vcp_res.get("detail", "")
            vcp_pivot = vcp_res.get("pivot")
            if vcp_signal:
                tags = list(candidate.get("tags") or [])
                if vcp_signal not in tags:
                    tags.append(vcp_signal)
                candidate["tags"] = tags
        except Exception:
            pass

    bb_signal = None
    bb_detail = ""
    if check_vcp and not price_df.empty and len(price_df) >= 40:
        try:
            bb_res = detect_bb_trend(price_df)
            bb_signal = bb_res.get("signal")
            bb_detail = bb_res.get("detail", "")
            if bb_signal:
                tags = list(candidate.get("tags") or [])
                if bb_signal not in tags:
                    tags.append(bb_signal)
                candidate["tags"] = tags
        except Exception:
            pass

    candidate.update({
        "today_close": today_close,
        "today_pct": today_pct,
        "streak_pct": streak_pct,
        "streak_amount": streak_amount,
        "margin_diff": margin_diff,
        "margin_today": margin_today,
        "vcp_signal": vcp_signal,
        "vcp_detail": vcp_detail,
        "vcp_pivot": vcp_pivot,
        "bb_signal": bb_signal,
        "bb_detail": bb_detail,
        "recent_daily_pcts": recent_daily_pcts,
    })
    return candidate


def _est_amt_yi(
    item: dict,
    recent_prices_map: Optional[dict],
    daily_price_map: Optional[dict],
) -> float:
    """估算累計買/賣超金額（億元），供雙軌挑選高流動性主流股與濾除冷門股。"""
    sid = str(item.get("stock_id", "")).strip()
    close_px = 0.0
    recs = (recent_prices_map or {}).get(sid)
    if recs:
        close_px = float(recs[-1].get("close", 0.0))
    elif daily_price_map and sid in daily_price_map:
        close_px = float(daily_price_map[sid].get("close", 0.0))
    return abs(item.get("streak_total_net_lots", 0)) * close_px * 1000 / 1e8


def _net_cap_ratio(item: dict) -> float:
    """計算累計買超張數佔已發行股數比例（識別小型股籌碼鎖定力道）。"""
    shares = item.get("shares_issued", 0) or 0
    if shares <= 0:
        return 0.0
    return abs(item.get("streak_total_net_lots", 0)) * 1000 / shares


def _select_vcp_market_sids(
    mkt_pool: list[dict],
    recent_prices_map: Optional[dict],
    daily_price_map: Optional[dict],
    top_amt: int = 8,
    top_cap: int = 7,
    min_amt_yi_for_small: float = 0.3,
) -> set[str]:
    """雙軌選秀挑選 VCP 掃描對象（排除 00 開頭 ETF）：
    - 軌道 A（大資金主流）：依累計買超金額取前 top_amt 名
    - 軌道 B（小型鎖碼黑馬）：在買超 >= 0.3 億前提下，依買超佔股本比取前 top_cap 名
    """
    non_etf = [c for c in mkt_pool if not str(c.get("stock_id", "")).startswith("00")]
    if not non_etf:
        return set()

    by_amt = sorted(
        non_etf,
        key=lambda x: (
            _est_amt_yi(x, recent_prices_map, daily_price_map),
            x["main_streak"],
            x["streak_total_net_lots"],
        ),
        reverse=True,
    )
    selected: list[str] = [str(x["stock_id"]) for x in by_amt[:top_amt]]
    selected_set = set(selected)

    has_price_data = bool(recent_prices_map or daily_price_map)
    small_pool = [
        x for x in non_etf
        if _net_cap_ratio(x) > 0
        and (not has_price_data or _est_amt_yi(x, recent_prices_map, daily_price_map) >= min_amt_yi_for_small)
    ]
    by_cap = sorted(
        small_pool,
        key=lambda x: (
            _net_cap_ratio(x),
            x["main_streak"],
            _est_amt_yi(x, recent_prices_map, daily_price_map),
        ),
        reverse=True,
    )
    added = 0
    for x in by_cap:
        sid = str(x["stock_id"])
        if sid not in selected_set:
            selected_set.add(sid)
            added += 1
            if added >= top_cap:
                break

    return selected_set


def _process_pool(
    raw_candidates: list[dict],
    watchlist_sids: set[str],
    watchlist_names: dict[str, str],
    latest_date: str,
    max_market_candidates: int,
    is_sell: bool = False,
    delay_sec: float = 0.03,
    margin_stocks: Optional[dict] = None,
    daily_price_map: Optional[dict] = None,
    recent_prices_map: Optional[dict] = None,
    finmind_state: Optional[dict] = None,
) -> list[dict]:
    """處理單一方向 (買或賣) 的候選清單：交叉比對自選股、排序與補充股價。"""
    wl_pool = []
    mkt_pool = []

    for c in raw_candidates:
        c["date"] = latest_date
        sid = c["stock_id"]
        if sid in watchlist_sids:
            c["is_watchlist"] = True
            if not c.get("name") and sid in watchlist_names:
                c["name"] = watchlist_names[sid]
            wl_pool.append(c)
        else:
            c["is_watchlist"] = False
            mkt_pool.append(c)

    if is_sell:
        # 連賣：優先依連賣天數，其次預估賣超金額（億元），其次賣超張數
        mkt_pool.sort(
            key=lambda x: (
                x["main_streak"],
                _est_amt_yi(x, recent_prices_map, daily_price_map),
                -x["streak_total_net_lots"],
            ),
            reverse=True,
        )
        vcp_target_sids: set[str] = set()
        selected_market = mkt_pool[:max_market_candidates]
    else:
        # 連買：雙軌選秀挑出大資金(TOP 8) + 小型鎖碼黑馬(TOP 7) 確保兩者都進 VCP 雷達與候選清單
        vcp_target_sids = _select_vcp_market_sids(
            mkt_pool, recent_prices_map, daily_price_map, top_amt=8, top_cap=7
        )
        mkt_pool.sort(
            key=lambda x: (
                1 if str(x.get("stock_id", "")) in vcp_target_sids else 0,
                x["main_streak"],
                _est_amt_yi(x, recent_prices_map, daily_price_map),
                x["streak_total_net_lots"],
            ),
            reverse=True,
        )
        selected_market = mkt_pool[:max_market_candidates]

    combined = wl_pool + selected_market

    index_df = None
    if not is_sell and not (finmind_state and finmind_state.get("rate_limited")):
        try:
            idx_df = get_index_history("TAIEX")
            if not idx_df.empty:
                index_df = idx_df
        except Exception:
            index_df = None

    # 價格/漲跌幅已全面由證交所/櫃買免費行情供給；FinMind 僅輔助買方自選+雙軌 TOP 15 做 VCP 掃描
    enriched = []
    for item in combined:
        sid = str(item.get("stock_id", "")).strip()
        is_wl = bool(item.get("is_watchlist"))
        allow_finmind = (
            (not is_sell)
            and (not sid.startswith("00"))
            and (is_wl or sid in vcp_target_sids)
        )
        try:
            enriched.append(
                enrich_candidate_price(
                    item,
                    index_df=index_df,
                    check_vcp=not is_sell,
                    margin_stocks=margin_stocks,
                    daily_price_map=daily_price_map,
                    recent_prices_map=recent_prices_map,
                    finmind_state=finmind_state,
                    use_finmind=allow_finmind,
                )
            )
        except TypeError:
            enriched.append(enrich_candidate_price(item))
        if delay_sec > 0 and allow_finmind and not (finmind_state and finmind_state.get("rate_limited")):
            time.sleep(delay_sec)

    if is_sell:
        # 自選股優先置頂，其次連賣天數降冪，其次累計賣超金額（負最多/絕對值最大），其次賣超張數
        enriched.sort(
            key=lambda x: (
                1 if x.get("is_watchlist") else 0,
                x.get("main_streak", 0),
                abs(x.get("streak_amount", 0.0)),
                -x.get("streak_total_net_lots", 0),
                -x.get("streak_pct", 0),
            ),
            reverse=True,
        )
    else:
        # 自選股優先置頂，其次連買天數降冪，其次累計買超金額（億元），其次買超張數
        enriched.sort(
            key=lambda x: (
                1 if x.get("is_watchlist") else 0,
                x.get("main_streak", 0),
                x.get("streak_amount", 0.0),
                x.get("streak_total_net_lots", 0),
                x.get("streak_pct", 0),
            ),
            reverse=True,
        )

    return enriched


def scan_market_chips_streak(
    watchlist: list[dict] = None,
    min_streak: int = 3,
    max_market_candidates: int = 60,
    delay_sec: float = 0.03,
) -> ChipsScanResult:
    """執行全市場三大法人連買與連賣雙向掃描。

    回傳 ChipsScanResult 物件，包含 'buy_records' 與 'sell_records'。
    """
    res = get_market_streak_candidates(min_streak=min_streak, trading_days_count=5)
    trading_days = res.get("trading_days", [])
    latest_date = trading_days[-1] if trading_days else ""

    buy_candidates = res.get("buy_candidates", [])
    sell_candidates = res.get("sell_candidates", [])

    watchlist_sids = set()
    watchlist_names = {}
    if watchlist:
        for w in watchlist:
            sid = str(w.get("stock_id", "")).strip()
            if sid:
                watchlist_sids.add(sid)
                if w.get("name"):
                    watchlist_names[sid] = str(w.get("name"))

    try:
        margin_res = get_market_margin_map()
    except Exception:
        margin_res = {}
    margin_stocks = margin_res.get("stocks", {})
    mkt_margin_diff = margin_res.get("market_total_margin_diff_lots")

    try:
        recent_prices_map = get_market_recent_prices_map(trading_days)
    except Exception:
        recent_prices_map = {}

    try:
        daily_price_map = get_market_daily_price_map()
    except Exception:
        daily_price_map = {}

    finmind_state = {"rate_limited": False}

    enriched_buys = _process_pool(
        buy_candidates, watchlist_sids, watchlist_names, latest_date,
        max_market_candidates, is_sell=False, delay_sec=delay_sec,
        margin_stocks=margin_stocks, daily_price_map=daily_price_map,
        recent_prices_map=recent_prices_map, finmind_state=finmind_state,
    )
    enriched_sells = _process_pool(
        sell_candidates, watchlist_sids, watchlist_names, latest_date,
        max_market_candidates, is_sell=True, delay_sec=delay_sec,
        margin_stocks=margin_stocks, daily_price_map=daily_price_map,
        recent_prices_map=recent_prices_map, finmind_state=finmind_state,
    )

    return ChipsScanResult({
        "trading_days": trading_days,
        "buy_records": enriched_buys,
        "sell_records": enriched_sells,
        "market_total_margin_diff_lots": mkt_margin_diff,
    })


def scan_watchlist_chips(
    watchlist: list[dict],
    min_streak: int = 3,
    delay_sec: float = 0.03,
) -> ChipsScanResult:
    """向後相容接口。"""
    return scan_market_chips_streak(
        watchlist=watchlist,
        min_streak=min_streak,
        delay_sec=delay_sec,
    )
