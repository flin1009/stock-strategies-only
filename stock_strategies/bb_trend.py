"""台股實戰版布林開口趨勢波段偵測模組

經近 5 年 95 檔台股實證改良之三條件起漲訊號（動能 × 波動 × 趨勢）：
1. 動能：採用加權收盤價 (H+L+2C)/4 計算 MACD(12,26,9)，柱狀體今日剛由負轉正 (osc > 0 且 osc[t-1] <= 0)
2. 波動：昨日布林帶寬 <= 15%（先壓縮），今日帶寬擴大且上軌上升、下軌下降（雙向開口）
3. 趨勢＋流動性：收盤站上 20 日線、MA20 至少較 3 日前上揚、且成交金額 >= 3,000 萬台幣
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd


def _empty_bb_result() -> dict:
    return {
        "signal": None,
        "bonus": 0,
        "osc": None,
        "bw": None,
        "bw_prev": None,
        "ma20": None,
        "detail": "",
    }


def _compute_bb_macd_arrays(df: pd.DataFrame) -> dict[str, np.ndarray]:
    c = pd.to_numeric(df["close"], errors="coerce")
    h = pd.to_numeric(df["high"], errors="coerce") if "high" in df.columns else c
    l = pd.to_numeric(df["low"], errors="coerce") if "low" in df.columns else c
    v = pd.to_numeric(df["volume"], errors="coerce") if "volume" in df.columns else pd.Series(0.0, index=df.index)

    wc = (h + l + 2.0 * c) / 4.0
    ema12 = wc.ewm(span=12, adjust=False).mean()
    ema26 = wc.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    osc = dif - dea

    ma20 = c.rolling(20).mean()
    std20 = c.rolling(20).std(ddof=0)
    bb_up = ma20 + 2.0 * std20
    bb_dn = ma20 - 2.0 * std20
    bw = ((bb_up - bb_dn) / ma20) * 100.0

    tmoney = (
        pd.to_numeric(df["Trading_money"], errors="coerce")
        if "Trading_money" in df.columns
        else (c * v)
    )

    return {
        "close": c.values,
        "ma20": ma20.values,
        "bb_up": bb_up.values,
        "bb_dn": bb_dn.values,
        "bw": bw.values,
        "osc": osc.values,
        "tmoney": tmoney.values,
    }


def detect_bb_trend(
    df: pd.DataFrame,
    idx: int = -1,
    *,
    max_prev_bw: float = 15.0,
    ma_up_bars: int = 3,
    min_turnover: float = 30_000_000.0,
) -> dict:
    """檢查指定 K 棒位置是否符合「布林開口起漲」三條件。"""
    if df is None or df.empty or len(df) < 40:
        return _empty_bb_result()

    if idx < 0:
        idx = len(df) + idx
    if idx < max(35, 20 + ma_up_bars) or idx >= len(df):
        return _empty_bb_result()

    # 只取到 idx+1 並保留足夠 warmup，兼顧效率與不洩漏未來
    arr = _compute_bb_macd_arrays(df.iloc[: idx + 1])
    i = len(arr["close"]) - 1

    c_now = float(arr["close"][i])
    ma_now = float(arr["ma20"][i])
    ma_prev_k = float(arr["ma20"][i - ma_up_bars])
    up_now, up_prev = float(arr["bb_up"][i]), float(arr["bb_up"][i - 1])
    dn_now, dn_prev = float(arr["bb_dn"][i]), float(arr["bb_dn"][i - 1])
    bw_now, bw_prev = float(arr["bw"][i]), float(arr["bw"][i - 1])
    osc_now, osc_prev = float(arr["osc"][i]), float(arr["osc"][i - 1])
    turnover = float(arr["tmoney"][i]) if not np.isnan(arr["tmoney"][i]) else 0.0

    vals = (c_now, ma_now, ma_prev_k, up_now, up_prev, dn_now, dn_prev, bw_now, bw_prev, osc_now, osc_prev)
    if any(np.isnan(x) for x in vals) or c_now <= 0 or ma_now <= 0:
        return _empty_bb_result()

    cond_macd = osc_now > 0.0 and osc_prev <= 0.0
    cond_bb = (
        bw_prev <= max_prev_bw
        and bw_now > bw_prev
        and up_now > up_prev
        and dn_now < dn_prev
    )
    cond_trend = c_now > ma_now and ma_now > ma_prev_k
    cond_liq = turnover >= min_turnover

    signal = None
    bonus = 0
    detail = ""
    if cond_macd and cond_bb and cond_trend and cond_liq:
        signal = "布林開口起漲"
        bonus = 10
        detail = f"MACD翻正 · 帶寬 {bw_prev:.1f}%→{bw_now:.1f}% · 站上揚MA20 {ma_now:.1f}"

    return {
        "signal": signal,
        "bonus": bonus,
        "osc": round(osc_now, 3),
        "bw": round(bw_now, 2),
        "bw_prev": round(bw_prev, 2),
        "ma20": round(ma_now, 2),
        "detail": detail,
    }


def bb_trend_indices(
    df: pd.DataFrame,
    start_idx: int = 40,
    end_idx: Optional[int] = None,
    *,
    max_prev_bw: float = 15.0,
    ma_up_bars: int = 3,
    min_turnover: float = 30_000_000.0,
) -> list[int]:
    """回傳整段歷史中觸發「布林開口起漲」的索引清單（供回測使用）。"""
    if df is None or df.empty or len(df) < 40:
        return []

    n = len(df)
    stop_i = n if end_idx is None else min(n, end_idx)
    start_i = max(max(40, 20 + ma_up_bars), start_idx)
    if start_i >= stop_i:
        return []

    arr = _compute_bb_macd_arrays(df)
    c = arr["close"]
    ma20 = arr["ma20"]
    up, dn, bw = arr["bb_up"], arr["bb_dn"], arr["bw"]
    osc = arr["osc"]
    tm = arr["tmoney"]

    hits: list[int] = []
    for i in range(start_i, stop_i):
        if (
            np.isnan(ma20[i])
            or np.isnan(ma20[i - ma_up_bars])
            or np.isnan(bw[i - 1])
            or np.isnan(osc[i - 1])
        ):
            continue
        if (
            osc[i] > 0.0
            and osc[i - 1] <= 0.0
            and bw[i - 1] <= max_prev_bw
            and bw[i] > bw[i - 1]
            and up[i] > up[i - 1]
            and dn[i] < dn[i - 1]
            and c[i] > ma20[i]
            and ma20[i] > ma20[i - ma_up_bars]
            and not np.isnan(tm[i])
            and tm[i] >= min_turnover
        ):
            hits.append(i)
    return hits
