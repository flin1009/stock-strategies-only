"""三大法人連續買超與連續賣超分析模組

支援證交所 (TWSE) 與 櫃買中心 (TPEx) 全市場 2,200+ 檔標的多空雙向初篩，
並結合自選股 (Watchlist) 交叉比對與近幾日價格漲跌幅統計。
"""
from __future__ import annotations

import time
from typing import Optional

import pandas as pd

from .exchange_data import get_market_streak_candidates
from .data import get_price_history


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


def enrich_candidate_price(candidate: dict) -> dict:
    """為篩選出的連買/連賣個股補上最新收盤價、今日漲跌幅、連買/賣期間累計漲幅與近日漲幅序列。"""
    sid = str(candidate["stock_id"])
    try:
        price_df = get_price_history(sid, years=1)
    except Exception:
        price_df = pd.DataFrame()

    if price_df.empty or "close" not in price_df.columns or len(price_df) < 2:
        candidate.update({
            "today_close": 0.0,
            "today_pct": 0.0,
            "streak_pct": 0.0,
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

    recent_slice = price_df.tail(max(3, min(streak_len, 5)))
    recent_daily_pcts = [
        f"{r['daily_pct']:+.1f}%" if pd.notna(r["daily_pct"]) else "0.0%"
        for _, r in recent_slice.iterrows()
    ]

    candidate.update({
        "today_close": today_close,
        "today_pct": today_pct,
        "streak_pct": streak_pct,
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

    enriched = []
    for item in combined:
        enriched.append(enrich_candidate_price(item))
        if delay_sec > 0:
            time.sleep(delay_sec)

    if is_sell:
        # 自選股優先置頂，其次連賣天數降冪，其次賣超張數 (負越多越前)
        enriched.sort(
            key=lambda x: (
                1 if x.get("is_watchlist") else 0,
                x.get("main_streak", 0),
                -x.get("streak_total_net_lots", 0),
                -x.get("streak_pct", 0),
            ),
            reverse=True,
        )
    else:
        # 自選股優先置頂，其次連買天數降冪，其次買超張數
        enriched.sort(
            key=lambda x: (
                1 if x.get("is_watchlist") else 0,
                x.get("main_streak", 0),
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

    enriched_buys = _process_pool(
        buy_candidates, watchlist_sids, watchlist_names, latest_date,
        max_market_candidates, is_sell=False, delay_sec=delay_sec,
    )
    enriched_sells = _process_pool(
        sell_candidates, watchlist_sids, watchlist_names, latest_date,
        max_market_candidates, is_sell=True, delay_sec=delay_sec,
    )

    return ChipsScanResult({
        "trading_days": trading_days,
        "buy_records": enriched_buys,
        "sell_records": enriched_sells,
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
