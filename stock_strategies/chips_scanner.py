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
)
from .data import get_price_history
from .datasources import get_index_history
from .vcp import detect_vcp


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
    finmind_state: Optional[dict] = None,
    use_finmind: bool = True,
) -> dict:
    """為篩選出的連買/連賣個股補上最新收盤價、今日漲跌幅、連買/賣期間累計漲幅與 VCP 偵測。"""
    sid = str(candidate["stock_id"])
    price_df = pd.DataFrame()
    if use_finmind and not (finmind_state and finmind_state.get("rate_limited")):
        try:
            price_df = get_price_history(sid, years=1)
        except FinMindRateLimitError:
            if finmind_state is not None and not finmind_state.get("rate_limited"):
                finmind_state["rate_limited"] = True
                print("⚠️ FinMind 達請求上限，後續標的改用證交所/櫃買免費當日快照補齊")
            price_df = pd.DataFrame()
        except Exception:
            price_df = pd.DataFrame()

    # 融入融資增減資訊（優先使用預先載入的快照）
    margin_diff = None
    margin_today = None
    try:
        m_map = margin_stocks if margin_stocks is not None else get_market_margin_map().get("stocks", {})
        if sid in m_map:
            margin_diff = m_map[sid].get("margin_diff")
            margin_today = m_map[sid].get("margin_today")
    except Exception:
        pass

    lots = candidate.get("streak_total_net_lots", 0)

    if price_df.empty or "close" not in price_df.columns or len(price_df) < 2:
        # Fallback：使用證交所/櫃買免費當日收盤與漲跌幅快照（0 FinMind 額度）
        snap = (daily_price_map or {}).get(sid)
        if snap and snap.get("close", 0) > 0:
            today_close = float(snap["close"])
            today_pct = float(snap.get("pct", 0.0))
            streak_amount = round((lots * today_close * 1000) / 1e8, 2)
            candidate.update({
                "today_close": today_close,
                "today_pct": today_pct,
                "streak_pct": today_pct,
                "streak_amount": streak_amount,
                "margin_diff": margin_diff,
                "margin_today": margin_today,
                "vcp_signal": None,
                "vcp_detail": "",
                "vcp_pivot": None,
                "recent_daily_pcts": [f"{today_pct:+.1f}%"],
            })
            return candidate

        candidate.update({
            "today_close": 0.0,
            "today_pct": 0.0,
            "streak_pct": 0.0,
            "streak_amount": 0.0,
            "margin_diff": margin_diff,
            "margin_today": margin_today,
            "vcp_signal": None,
            "vcp_detail": "",
            "vcp_pivot": None,
            "recent_daily_pcts": [],
        })
        return candidate

    price_df = price_df.sort_values("date").reset_index(drop=True)
    price_df["daily_pct"] = (price_df["close"].pct_change() * 100).round(2)

    latest = price_df.iloc[-1]
    today_close = float(latest["close"])
    today_pct = float(latest["daily_pct"]) if pd.notna(latest["daily_pct"]) else 0.0

    streak_len = max(1, candidate.get("main_streak", 3))
    if len(price_df) > streak_len:
        base_close = float(price_df.iloc[-streak_len - 1]["close"])
    else:
        base_close = float(price_df.iloc[0]["close"])

    streak_pct = round(((today_close - base_close) / base_close) * 100, 2) if base_close > 0 else 0.0

    # 累計買/賣超金額（億元）= 張數 * 1000股 * 今日收盤價 / 1億
    streak_amount = round((lots * today_close * 1000) / 1e8, 2) if today_close > 0 else 0.0

    recent_slice = price_df.tail(max(3, min(streak_len, 5)))
    recent_daily_pcts = [
        f"{r['daily_pct']:+.1f}%" if pd.notna(r["daily_pct"]) else "0.0%"
        for _, r in recent_slice.iterrows()
    ]

    vcp_signal = None
    vcp_detail = ""
    vcp_pivot = None
    if check_vcp and len(price_df) >= 220:
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
        "recent_daily_pcts": recent_daily_pcts,
    })
    return candidate


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
        # 連賣：賣超張數越多 (負最多) 排越前
        mkt_pool.sort(
            key=lambda x: (x["main_streak"], -x["streak_total_net_lots"]),
            reverse=True,
        )
    else:
        # 連買：買超張數越多排越前
        mkt_pool.sort(
            key=lambda x: (x["main_streak"], x["streak_total_net_lots"]),
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

    # 買方自選+前30強才拉 FinMind 完整日K（算 VCP+5日），賣方自選+前10強拉 FinMind；其餘改用免費當日快照省額度
    finmind_mkt_cap = 10 if is_sell else 30

    enriched = []
    mkt_seen = 0
    for item in combined:
        is_wl = bool(item.get("is_watchlist"))
        if not is_wl:
            mkt_seen += 1
        allow_finmind = is_wl or (mkt_seen <= finmind_mkt_cap)
        try:
            enriched.append(
                enrich_candidate_price(
                    item,
                    index_df=index_df,
                    check_vcp=not is_sell,
                    margin_stocks=margin_stocks,
                    daily_price_map=daily_price_map,
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
        daily_price_map = get_market_daily_price_map()
    except Exception:
        daily_price_map = {}

    finmind_state = {"rate_limited": False}

    enriched_buys = _process_pool(
        buy_candidates, watchlist_sids, watchlist_names, latest_date,
        max_market_candidates, is_sell=False, delay_sec=delay_sec,
        margin_stocks=margin_stocks, daily_price_map=daily_price_map, finmind_state=finmind_state,
    )
    enriched_sells = _process_pool(
        sell_candidates, watchlist_sids, watchlist_names, latest_date,
        max_market_candidates, is_sell=True, delay_sec=delay_sec,
        margin_stocks=margin_stocks, daily_price_map=daily_price_map, finmind_state=finmind_state,
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
