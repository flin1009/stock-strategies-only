"""三大法人連續買超與股價漲幅分析模組

支援證交所 (TWSE) 與 櫃買中心 (TPEx) 全市場 2,200+ 檔標的連續買超初篩，
並結合自選股 (Watchlist) 交叉比對與近幾日價格漲跌幅統計。
"""
from __future__ import annotations

import time
from typing import Optional

import pandas as pd

from .exchange_data import get_market_streak_candidates
from .data import get_price_history


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
    """為篩選出的連買個股補上最新收盤價、今日漲跌幅、連買期間累計漲幅與近日漲幅序列。"""
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


def scan_market_chips_streak(
    watchlist: list[dict] = None,
    min_streak: int = 3,
    max_market_candidates: int = 60,
    delay_sec: float = 0.05,
) -> list[dict]:
    """執行證交所/櫃買中心全市場三大法人連買掃描，並交叉比對自選股。

    1. 全市場掃描 2,200+ 檔標的連續買超 >= min_streak 天的候選名單。
    2. 比對 Watchlist，標註 is_watchlist = True/False。
    3. 自選股全數保留；非自選股取前 max_market_candidates 檔最熱門/累計買超最多標的。
    4. 補充每日股價與累積漲跌幅。
    5. 排序：自選股置頂，其後依連買天數與累計買超張數降冪排序。
    """
    res = get_market_streak_candidates(min_streak=min_streak, trading_days_count=5)
    candidates = res.get("candidates", [])
    trading_days = res.get("trading_days", [])
    latest_date = trading_days[-1] if trading_days else ""

    watchlist_sids = set()
    watchlist_names = {}
    if watchlist:
        for w in watchlist:
            sid = str(w.get("stock_id", "")).strip()
            if sid:
                watchlist_sids.add(sid)
                if w.get("name"):
                    watchlist_names[sid] = str(w.get("name"))

    watchlist_pool = []
    market_pool = []

    for c in candidates:
        c["date"] = latest_date
        sid = c["stock_id"]
        if sid in watchlist_sids:
            c["is_watchlist"] = True
            if not c.get("name") and sid in watchlist_names:
                c["name"] = watchlist_names[sid]
            watchlist_pool.append(c)
        else:
            c["is_watchlist"] = False
            market_pool.append(c)

    # 非自選股按 main_streak 降冪、累計買超張數降冪排序，取前 max_market_candidates 檔
    market_pool.sort(
        key=lambda x: (x["main_streak"], x["streak_total_net_lots"]),
        reverse=True,
    )
    selected_market = market_pool[:max_market_candidates]

    # 合併名單 (自選股必定納入)
    combined = watchlist_pool + selected_market

    # 補充價格資訊 (收盤價、漲跌幅)
    enriched = []
    for item in combined:
        enriched.append(enrich_candidate_price(item))
        if delay_sec > 0:
            time.sleep(delay_sec)

    # 最終排序：自選股優先置頂，其次連買天數，其次買超張數
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


def scan_watchlist_chips(
    watchlist: list[dict],
    min_streak: int = 3,
    delay_sec: float = 0.05,
) -> list[dict]:
    """向後相容：呼叫全市場掃描並傳入 watchlist。"""
    return scan_market_chips_streak(
        watchlist=watchlist,
        min_streak=min_streak,
        delay_sec=delay_sec,
    )
