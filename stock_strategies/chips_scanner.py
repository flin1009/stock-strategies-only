"""三大法人連續買超與股價漲幅分析模組

提供法人籌碼連買（外資、投信、三大法人合計）統計與對應交易日股價漲跌幅計算。
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

from .datasources import get_institutional
from .data import get_price_history


def calculate_streak(series: list[float] | pd.Series) -> int:
    """計算由最新交易日往前倒數的連續買超天數 (值 > 0)。

    若最新一日 <= 0 或無資料，則連買天數為 0。
    """
    if series is None:
        return 0
    vals = series.tolist() if isinstance(series, pd.Series) else list(series)
    streak = 0
    for v in reversed(vals):
        if pd.isna(v) or v <= 0:
            break
        streak += 1
    return streak


def analyze_stock_chips(
    stock_id: str,
    name: str = "",
    lookback_days: int = 45,
) -> Optional[dict]:
    """分析單檔股票的三大法人連買狀況與每日漲跌幅。

    lookback_days: 往前查詢天數（預設約 45 天，包含約 30 個交易日）。
    """
    start_date = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")

    # 1. 取得三大法人買賣超 (股)
    inst_df = get_institutional(stock_id, start=start_date)
    if inst_df.empty or "total_net" not in inst_df.columns or len(inst_df) < 3:
        return None

    # 2. 取得日 K 線 (收盤價)
    price_df = get_price_history(stock_id, years=1)
    if price_df.empty or "close" not in price_df.columns or len(price_df) < 3:
        return None

    # 確保 date 格式一致並合併
    inst_df["date"] = pd.to_datetime(inst_df["date"]).dt.strftime("%Y-%m-%d")
    price_df["date"] = pd.to_datetime(price_df["date"]).dt.strftime("%Y-%m-%d")

    merged = pd.merge(
        inst_df[["date", "foreign_net", "trust_net", "dealer_net", "total_net"]],
        price_df[["date", "close", "volume"]],
        on="date",
        how="inner",
    ).sort_values("date").reset_index(drop=True)

    if len(merged) < 3:
        return None

    # 計算各維度連買天數
    total_streak = calculate_streak(merged["total_net"])
    foreign_streak = calculate_streak(merged["foreign_net"])
    trust_streak = calculate_streak(merged["trust_net"])

    # 最新一日資料
    latest_row = merged.iloc[-1]
    today_close = float(latest_row["close"])
    today_total_net = float(latest_row["total_net"])
    today_foreign_net = float(latest_row["foreign_net"])
    today_trust_net = float(latest_row["trust_net"])

    # 計算每日漲跌幅 %
    merged["daily_pct"] = (merged["close"].pct_change() * 100).round(2)
    today_pct = float(merged["daily_pct"].iloc[-1]) if pd.notna(merged["daily_pct"].iloc[-1]) else 0.0

    # 判定主要連買天數 (以合計為主，若合計無連買則取外資或投信最大連買天數)
    main_streak = total_streak if total_streak >= 3 else max(foreign_streak, trust_streak)

    # 計算連買期間累積漲幅與各日漲跌
    streak_len = max(1, main_streak)
    if len(merged) > streak_len:
        # 連買前一日的收盤價作為基準
        base_close = float(merged.iloc[-streak_len - 1]["close"])
    else:
        base_close = float(merged.iloc[0]["close"])

    streak_pct = round(((today_close - base_close) / base_close) * 100, 2) if base_close > 0 else 0.0

    # 近期（最多 5 天）每日漲跌幅字串列表
    recent_slice = merged.tail(max(3, min(streak_len, 5)))
    recent_daily_pcts = [
        f"{r['daily_pct']:+.1f}%" if pd.notna(r["daily_pct"]) else "0.0%"
        for _, r in recent_slice.iterrows()
    ]

    # 連買期間累計買超張數 (1 張 = 1000 股)
    streak_records = merged.tail(streak_len)
    streak_total_net_lots = int(streak_records["total_net"].sum() // 1000)
    streak_foreign_net_lots = int(streak_records["foreign_net"].sum() // 1000)
    streak_trust_net_lots = int(streak_records["trust_net"].sum() // 1000)

    # 特徵標籤
    tags = []
    if foreign_streak >= 3 and trust_streak >= 3:
        tags.append("土洋同買")
    elif trust_streak >= 3:
        tags.append("投信認養")
    elif foreign_streak >= 3:
        tags.append("外資買進")
    elif total_streak >= 3:
        tags.append("法人合買")

    return {
        "date": str(latest_row["date"]),
        "stock_id": str(stock_id),
        "name": str(name),
        "total_streak": total_streak,
        "foreign_streak": foreign_streak,
        "trust_streak": trust_streak,
        "main_streak": main_streak,
        "today_close": today_close,
        "today_pct": today_pct,
        "streak_pct": streak_pct,
        "today_total_net_lots": int(today_total_net // 1000),
        "today_foreign_net_lots": int(today_foreign_net // 1000),
        "today_trust_net_lots": int(today_trust_net // 1000),
        "streak_total_net_lots": streak_total_net_lots,
        "streak_foreign_net_lots": streak_foreign_net_lots,
        "streak_trust_net_lots": streak_trust_net_lots,
        "recent_daily_pcts": recent_daily_pcts,
        "tags": tags,
    }


def scan_watchlist_chips(
    watchlist: list[dict],
    min_streak: int = 3,
    delay_sec: float = 0.15,
) -> list[dict]:
    """掃描觀察清單中三大法人連續買超 >= min_streak 天的標的。"""
    matched = []
    for row in watchlist:
        sid = str(row.get("stock_id", "")).strip()
        name = str(row.get("name", "")).strip()
        if not sid:
            continue

        res = analyze_stock_chips(sid, name)
        if res and (
            res["total_streak"] >= min_streak
            or res["foreign_streak"] >= min_streak
            or res["trust_streak"] >= min_streak
        ):
            matched.append(res)

        if delay_sec > 0:
            time.sleep(delay_sec)

    # 排序：優先以主要連買天數降冪，其次以連買期間累計買超張數降冪
    matched.sort(
        key=lambda x: (
            x["main_streak"],
            x["streak_total_net_lots"],
            x["streak_pct"],
        ),
        reverse=True,
    )
    return matched
