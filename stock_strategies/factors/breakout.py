"""突破派因子（§7 §3.8）——「帶量突破箱頂 / 創波段高」。

箱頂嚴格不含當日（iloc[-n-1:-1]）避免自我參照。
缺料由 registry 回 None；本體樣本不足/NaN → NEUTRAL(0.5)。
"""
from __future__ import annotations

import pandas as pd

from .base import NEUTRAL, clip01, rank_pct
from .registry import register


@register("breakout.box_break", "breakout", ["price_df"],
          "突破近 20 日整理箱頂（箱頂不含當日）", lookback_min=21)
def box_break(ctx, params):
    n = params.get("box_n", 20)
    p = ctx.price_df
    if "high" not in p.columns or "close" not in p.columns or len(p) < n + 1:
        return NEUTRAL
    h = pd.to_numeric(p["high"], errors="coerce")
    c = pd.to_numeric(p["close"], errors="coerce")
    box_top = h.iloc[-n - 1:-1].max()      # 嚴格不含當日
    if pd.isna(box_top) or box_top <= 0:
        return NEUTRAL
    ratio = float(c.iloc[-1]) / float(box_top)
    if ratio > 1.0:
        return clip01(0.7 + (ratio - 1.0) / 0.03 * 0.3)   # 剛突破 0~3% → 0.7~1.0
    return clip01(ratio * 0.6)             # 接近箱頂給部分分


@register("breakout.vol_confirm_break", "breakout", ["price_df"],
          "帶量確認突破（突破前提下看量能放大）", lookback_min=21)
def vol_confirm_break(ctx, params):
    n = params.get("box_n", 20)
    p = ctx.price_df
    cols = ["high", "close", "volume"]
    if any(col not in p.columns for col in cols) or len(p) < n + 1:
        return NEUTRAL
    h = pd.to_numeric(p["high"], errors="coerce")
    c = pd.to_numeric(p["close"], errors="coerce")
    vol = pd.to_numeric(p["volume"], errors="coerce")
    box_top = h.iloc[-n - 1:-1].max()
    if pd.isna(box_top) or box_top <= 0:
        return NEUTRAL
    if c.iloc[-1] <= box_top:              # 未突破 → 中性
        return NEUTRAL
    vol_20ma = vol.iloc[-20:].mean()
    if pd.isna(vol_20ma) or vol_20ma <= 0:
        return NEUTRAL
    r = float(vol.iloc[-1]) / float(vol_20ma)
    if r >= 2.0:
        return 1.0
    if r >= 1.5:
        return 0.8
    if r < 1.0:
        return 0.3                         # 假突破無量
    return 0.6                             # 1.0~1.5


@register("breakout.swing_new_high", "breakout", ["price_df"],
          "創波段（60 日）新高", lookback_min=60)
def swing_new_high(ctx, params):
    n = params.get("swing_n", 60)
    p = ctx.price_df
    if "high" not in p.columns or "close" not in p.columns or len(p) < 2:
        return NEUTRAL
    h = pd.to_numeric(p["high"], errors="coerce")
    c = pd.to_numeric(p["close"], errors="coerce")
    m = min(n, len(h))
    swing_high = h.iloc[-m:].max()
    if pd.isna(swing_high) or swing_high <= 0:
        return NEUTRAL
    if h.iloc[-1] >= swing_high:
        return 1.0
    if float(c.iloc[-1]) / float(swing_high) >= 0.98:
        return 0.75                        # 距波段高 <2%
    return clip01(rank_pct(c.iloc[-m:], float(c.iloc[-1])))
