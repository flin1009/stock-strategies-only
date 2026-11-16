"""台股實戰版 VCP (Volatility Contraction Pattern) 偵測模組

經近 5 年台股歷史回測驗證最有效之核心條件（避免固定 25/25/25 切分漏訊號）：
1. Minervini 趨勢樣板：Close > MA50 > MA150 > MA200、MA200 上揚、
   收盤 >= 52 週低點 * 1.30、且距離 52 週高點 25% 以內
2. 相對大盤強度 (RS)：近 250 日漲幅優於加權指數 (TAIEX)
3. 彈性兩段波動收縮：前段 (40日) 振幅 >= 10%，末段 (14日) 收斂至 <= 12%
   且收縮幅度 <= 前段 65%（不含當日）
4. 訊號觸發：
   - VCP突破 (+12分)：收盤價剛穿越末段樞紐高點 + 量 > 50日均量 1.5x + 實體強勢陽線 + 成交額門檻
   - VCP蓄勢 (+5分)：已符合 1~3 且股價距離樞紐高點 <= 3% 蓄勢待突破
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd


def _empty_result() -> dict:
    return {
        "signal": None,
        "bonus": 0,
        "trend_ok": False,
        "rs_ok": True,
        "contract_ok": False,
        "pivot": None,
        "base_depth": None,
        "tight_depth": None,
        "vol_ratio": None,
        "detail": "",
    }


def _calc_index_rs_ok(
    df: pd.DataFrame,
    idx: int,
    index_df: Optional[pd.DataFrame],
    lookback: int,
) -> bool:
    if (
        index_df is None
        or index_df.empty
        or "close" not in index_df.columns
        or "date" not in index_df.columns
        or "date" not in df.columns
        or idx < lookback
    ):
        return True

    end_date = pd.to_datetime(df.iloc[idx]["date"])
    start_date = pd.to_datetime(df.iloc[idx - lookback]["date"])
    c_now = float(df.iloc[idx]["close"])
    c_prev = float(df.iloc[idx - lookback]["close"])
    if c_prev <= 0 or pd.isna(c_now) or pd.isna(c_prev):
        return True
    stk_ret = c_now / c_prev - 1.0

    idx_dates = pd.to_datetime(index_df["date"])
    sub = index_df[(idx_dates >= start_date) & (idx_dates <= end_date)]
    if len(sub) < 2:
        return True
    idx_start = float(sub.iloc[0]["close"])
    idx_end = float(sub.iloc[-1]["close"])
    if idx_start <= 0 or pd.isna(idx_start) or pd.isna(idx_end):
        return True
    idx_ret = idx_end / idx_start - 1.0
    return stk_ret >= idx_ret


def detect_vcp(
    df: pd.DataFrame,
    idx: int = -1,
    index_df: Optional[pd.DataFrame] = None,
    *,
    base_len: int = 40,
    tight_len: int = 14,
    min_turnover: float = 30_000_000.0,
) -> dict:
    """檢查指定 K 棒位置是否符合 VCP突破 或 VCP蓄勢。"""
    if df is None or df.empty or len(df) < 220:
        return _empty_result()

    if idx < 0:
        idx = len(df) + idx
    if idx < 219 or idx >= len(df):
        return _empty_result()

    c_ser = pd.to_numeric(df["close"], errors="coerce")
    h_ser = pd.to_numeric(df["high"], errors="coerce")
    l_ser = pd.to_numeric(df["low"], errors="coerce")
    o_ser = pd.to_numeric(df["open"], errors="coerce") if "open" in df.columns else c_ser
    v_ser = pd.to_numeric(df["volume"], errors="coerce")

    close = float(c_ser.iloc[idx])
    prev_close = float(c_ser.iloc[idx - 1])
    high_today = float(h_ser.iloc[idx])
    low_today = float(l_ser.iloc[idx])
    open_today = float(o_ser.iloc[idx])
    vol_today = float(v_ser.iloc[idx])

    if any(pd.isna(x) for x in (close, prev_close, high_today, low_today, open_today, vol_today)) or close <= 0:
        return _empty_result()

    ma50 = float(df["ma50"].iloc[idx]) if "ma50" in df.columns else float(c_ser.iloc[idx - 49 : idx + 1].mean())
    ma150 = float(df["ma150"].iloc[idx]) if "ma150" in df.columns else float(c_ser.iloc[idx - 149 : idx + 1].mean())
    ma200 = float(df["ma200"].iloc[idx]) if "ma200" in df.columns else float(c_ser.iloc[idx - 199 : idx + 1].mean())
    ma200_prev = float(df["ma200"].iloc[idx - 20]) if "ma200" in df.columns else float(c_ser.iloc[idx - 219 : idx - 19].mean())

    if any(pd.isna(x) for x in (ma50, ma150, ma200, ma200_prev)):
        return _empty_result()

    w_start = max(0, idx - 249)
    low_52w = float(l_ser.iloc[w_start : idx + 1].min())
    high_52w = float(h_ser.iloc[w_start : idx + 1].max())

    trend_ok = (
        close > ma50 > ma150 > ma200
        and ma200 > ma200_prev
        and low_52w > 0 and close >= low_52w * 1.30
        and high_52w > 0 and close >= high_52w * 0.75
    )

    rs_lb = min(250, idx)
    rs_ok = _calc_index_rs_ok(df, idx, index_df, rs_lb)

    # 彈性兩段收縮：前段 (base_len) + 末段 (tight_len)，嚴格不含當日
    base_slice_h = h_ser.iloc[idx - base_len - tight_len : idx - tight_len]
    base_slice_l = l_ser.iloc[idx - base_len - tight_len : idx - tight_len]
    tight_slice_h = h_ser.iloc[idx - tight_len : idx]
    tight_slice_l = l_ser.iloc[idx - tight_len : idx]

    hb, lb = float(base_slice_h.max()), float(base_slice_l.min())
    ht, lt = float(tight_slice_h.max()), float(tight_slice_l.min())
    base_depth = round((hb - lb) / hb * 100, 2) if hb > 0 else 999.0
    tight_depth = round((ht - lt) / ht * 100, 2) if ht > 0 else 999.0
    pivot = round(ht, 2) if ht > 0 else None

    contract_ok = (
        base_depth >= 10.0
        and tight_depth <= 12.0
        and tight_depth <= base_depth * 0.65
    )

    vol_50_prev = float(v_ser.iloc[max(0, idx - 50) : idx].mean())
    vol_ratio = round(vol_today / vol_50_prev, 2) if vol_50_prev > 0 else None

    if "Trading_money" in df.columns and pd.notna(df["Trading_money"].iloc[idx]):
        turnover = float(df["Trading_money"].iloc[idx])
    else:
        turnover = close * vol_today

    rng = high_today - low_today
    k_pos = (close - low_today) / rng if rng > 0 else 0.5
    k_solid = (close >= open_today) and (k_pos >= 0.50)

    signal = None
    bonus = 0
    detail = ""

    if trend_ok and rs_ok and contract_ok and pivot and pivot > 0:
        if (
            prev_close <= ht
            and close > ht
            and vol_50_prev > 0
            and vol_today > vol_50_prev * 1.5
            and turnover >= min_turnover
            and k_solid
        ):
            signal = "VCP突破"
            bonus = 12
            detail = f"突破樞紐 {pivot}，收縮 {base_depth:.1f}%→{tight_depth:.1f}%，量比 {vol_ratio:.1f}x"
        elif ht * 0.97 <= close <= ht:
            signal = "VCP蓄勢"
            bonus = 5
            dist_pct = (ht - close) / ht * 100
            detail = f"距樞紐 {pivot} 僅 {dist_pct:.1f}%，末段收縮 {tight_depth:.1f}%"

    return {
        "signal": signal,
        "bonus": bonus,
        "trend_ok": bool(trend_ok),
        "rs_ok": bool(rs_ok),
        "contract_ok": bool(contract_ok),
        "pivot": pivot,
        "base_depth": base_depth,
        "tight_depth": tight_depth,
        "vol_ratio": vol_ratio,
        "detail": detail,
    }


def vcp_breakout_indices(
    df: pd.DataFrame,
    start_idx: int = 220,
    end_idx: Optional[int] = None,
    index_df: Optional[pd.DataFrame] = None,
    *,
    base_len: int = 40,
    tight_len: int = 14,
    min_turnover: float = 30_000_000.0,
) -> list[int]:
    """回傳整段歷史中觸發 VCP突破 的索引清單（供回測使用）。"""
    if df is None or df.empty or len(df) < 220:
        return []

    n = len(df)
    stop_i = n if end_idx is None else min(n, end_idx)
    start_i = max(220, start_idx)
    if start_i >= stop_i:
        return []

    c = pd.to_numeric(df["close"], errors="coerce").values
    o = (pd.to_numeric(df["open"], errors="coerce") if "open" in df.columns else pd.to_numeric(df["close"], errors="coerce")).values
    h = pd.to_numeric(df["high"], errors="coerce").values
    l = pd.to_numeric(df["low"], errors="coerce").values
    v = pd.to_numeric(df["volume"], errors="coerce").values
    tmoney = (
        pd.to_numeric(df["Trading_money"], errors="coerce").values
        if "Trading_money" in df.columns
        else c * v
    )

    c_ser = pd.Series(c)
    ma50 = df["ma50"].values if "ma50" in df.columns else c_ser.rolling(50).mean().values
    ma150 = df["ma150"].values if "ma150" in df.columns else c_ser.rolling(150).mean().values
    ma200 = df["ma200"].values if "ma200" in df.columns else c_ser.rolling(200).mean().values
    low250 = pd.Series(l).rolling(250, min_periods=200).min().values
    high250 = pd.Series(h).rolling(250, min_periods=200).max().values
    v50_prev = pd.Series(v).shift(1).rolling(50, min_periods=20).mean().values

    # 對齊大盤 250 日報酬
    taiex_roc = np.full(n, np.nan)
    if (
        index_df is not None
        and not index_df.empty
        and "date" in df.columns
        and "date" in index_df.columns
        and "close" in index_df.columns
    ):
        idx_df = index_df.copy()
        idx_df["date"] = pd.to_datetime(idx_df["date"])
        idx_df = idx_df.sort_values("date").drop_duplicates("date")
        idx_df["roc250"] = pd.to_numeric(idx_df["close"], errors="coerce").pct_change(250)
        roc_map = dict(zip(idx_df["date"], idx_df["roc250"]))
        taiex_roc = pd.to_datetime(df["date"]).map(roc_map).values

    stk_roc250 = c_ser.pct_change(250).values

    hits: list[int] = []
    for i in range(start_i, stop_i):
        if np.isnan(ma200[i]) or np.isnan(ma200[i - 20]) or np.isnan(v50_prev[i]) or v50_prev[i] <= 0:
            continue
        if not (c[i] > ma50[i] > ma150[i] > ma200[i] and ma200[i] > ma200[i - 20]):
            continue
        if low250[i] <= 0 or high250[i] <= 0 or c[i] < low250[i] * 1.30 or c[i] < high250[i] * 0.75:
            continue
        if not np.isnan(stk_roc250[i]) and not np.isnan(taiex_roc[i]) and stk_roc250[i] < taiex_roc[i]:
            continue

        hb = h[i - base_len - tight_len : i - tight_len].max()
        lb = l[i - base_len - tight_len : i - tight_len].min()
        ht = h[i - tight_len : i].max()
        lt = l[i - tight_len : i].min()
        if hb <= 0 or ht <= 0:
            continue
        db = (hb - lb) / hb * 100
        dt = (ht - lt) / ht * 100
        if not (db >= 10.0 and dt <= 12.0 and dt <= db * 0.65):
            continue

        rng = h[i] - l[i]
        k_pos = (c[i] - l[i]) / rng if rng > 0 else 0.5
        if not (c[i] >= o[i] and k_pos >= 0.50):
            continue

        if c[i - 1] <= ht < c[i] and v[i] > v50_prev[i] * 1.5 and tmoney[i] >= min_turnover:
            hits.append(i)

    return hits
