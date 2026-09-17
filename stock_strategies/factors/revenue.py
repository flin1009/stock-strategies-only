"""營收動能派因子（§7 §3.5）——「MoM/YoY 轉強 / 連續創高」。

讀 ctx.revenue（P1 已按 avail_date ≤ as_of 切片，欄位 period/revenue/mom/yoy）。
缺料判定由 registry（required_data=["revenue"]）回 None；本體樣本不足 → NEUTRAL(0.5)。
"""
from __future__ import annotations

import pandas as pd

from .base import NEUTRAL, clip01, zscore_clip
from .registry import register


@register("revenue.yoy_accel", "revenue", ["revenue"],
          "月營收 YoY 連續轉強（近 3 月遞增）", lookback_min=1)
def yoy_accel(ctx, params):
    rev = ctx.revenue
    if "yoy" not in rev.columns or "period" not in rev.columns:
        return NEUTRAL
    rev = rev.sort_values("period")
    yoy = pd.to_numeric(rev["yoy"], errors="coerce").dropna()
    if len(yoy) < 2:                       # 不足以形成 YoY 序列 → 中性
        return NEUTRAL
    last3 = yoy.iloc[-3:].tolist()
    if len(last3) == 3 and last3[0] < last3[1] < last3[2]:
        return 1.0                         # 近 3 月 YoY 遞增
    if len(last3) >= 2 and last3[-2] < last3[-1]:
        return 0.7                         # 近 2 月遞增
    if len(yoy) >= 2 and yoy.iloc[-2] <= 0 < yoy.iloc[-1]:
        return 0.55                        # 由負轉正
    s = yoy.iloc[-12:]
    return clip01(zscore_clip(float(yoy.iloc[-1]), s.mean(), s.std()))


@register("revenue.mom_turn", "revenue", ["revenue"],
          "月營收 MoM 季節調整後轉強（vs 去年同月 MoM）", lookback_min=1)
def mom_turn(ctx, params):
    rev = ctx.revenue
    if "mom" not in rev.columns or "period" not in rev.columns:
        return NEUTRAL
    rev = rev.sort_values("period").reset_index(drop=True)
    mom = pd.to_numeric(rev["mom"], errors="coerce")
    valid = mom.dropna()
    if len(valid) < 2:
        return NEUTRAL
    mom_by_period = dict(zip(rev["period"], mom))
    # 季節調整：每月 MoM 與去年同月 MoM 的差，正值＝相對去年同期轉強
    diffs = []
    for p, m in zip(rev["period"], mom):
        prev = mom_by_period.get(p - pd.DateOffset(years=1))
        if pd.notna(m) and prev is not None and pd.notna(prev):
            diffs.append(m - prev)
    if len(diffs) < 2:                     # 無足夠去年同期 → 退回 MoM 自身 z-score
        s = valid.iloc[-12:]
        return clip01(zscore_clip(float(valid.iloc[-1]), s.mean(), s.std()))
    ds = pd.Series(diffs)
    return clip01(zscore_clip(diffs[-1], ds.mean(), ds.std()))


@register("revenue.new_high_streak", "revenue", ["revenue"],
          "月營收連續創 12 月新高次數（cap=3）", lookback_min=1)
def new_high_streak(ctx, params):
    rev = ctx.revenue
    if "revenue" not in rev.columns or "period" not in rev.columns:
        return NEUTRAL
    r = pd.to_numeric(rev.sort_values("period")["revenue"], errors="coerce").dropna().reset_index(drop=True)
    if len(r) < 12:
        return NEUTRAL
    k = 0
    for i in range(len(r) - 1, 10, -1):    # 從最新往回，需前 12 月可比
        window_max = r.iloc[max(0, i - 12):i].max()   # 前 12 月（不含當月）
        if pd.notna(window_max) and r.iloc[i] > window_max:   # 嚴格創高才算動能
            k += 1
        else:
            break
    return clip01(k / 3.0)                  # 連 3 月新高 → 1.0
