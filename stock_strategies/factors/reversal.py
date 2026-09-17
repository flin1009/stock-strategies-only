"""技術反轉派因子（§7 §3.7）——「超賣 KD 翹頭 / 布林下軌反彈 / 跌深量縮」。

price_df 已含 add_indicators 的 k/d/bb_lower/bb_mid。
bb_lower_bounce 刻意對齊舊 tech_score_at 的布林門檻（0<dist<0.03），可交叉驗證。
缺料由 registry 回 None；本體樣本不足/NaN → NEUTRAL(0.5)。
"""
from __future__ import annotations

import pandas as pd

from .base import NEUTRAL, clip01, rank_pct
from .registry import register


@register("reversal.kd_oversold", "reversal", ["price_df"],
          "KD 超賣回升（k<30 翹頭，金叉加分）", lookback_min=60)
def kd_oversold(ctx, params):
    p = ctx.price_df
    if "k" not in p.columns or "d" not in p.columns or len(p) < 2:
        return NEUTRAL
    k = pd.to_numeric(p["k"], errors="coerce")
    d = pd.to_numeric(p["d"], errors="coerce")
    k_t, k_prev, d_t = k.iloc[-1], k.iloc[-2], d.iloc[-1]
    if pd.isna(k_t) or pd.isna(k_prev) or pd.isna(d_t):
        return NEUTRAL
    if k_t < 30 and k_t > k_prev:          # 超賣自低檔翹頭
        return clip01(0.8 + (0.2 if k_t > d_t else 0.0))
    # 非超賣翹頭：k 越高越偏空，k=30→0.5、k=100→0.2
    return clip01(0.5 - (k_t - 30) / 70 * 0.3)


@register("reversal.bb_lower_bounce", "reversal", ["price_df"],
          "布林下軌反彈（對齊舊門檻 0<dist<0.03）", lookback_min=60)
def bb_lower_bounce(ctx, params):
    p = ctx.price_df
    need = ["close", "bb_lower", "bb_mid"]
    if any(col not in p.columns for col in need) or len(p) < 2:
        return NEUTRAL
    close = float(p["close"].iloc[-1])
    close_prev = float(p["close"].iloc[-2])
    bb_lower = p["bb_lower"].iloc[-1]
    bb_mid = p["bb_mid"].iloc[-1]
    if pd.isna(bb_lower) or pd.isna(bb_mid) or bb_lower <= 0:
        return NEUTRAL
    dist = (close - bb_lower) / bb_lower
    if 0 < dist < 0.03 and close > close_prev:
        return 0.85                        # 剛離下軌且向上
    if close < bb_lower:
        return 0.6                         # 仍在軌下醞釀
    if close > bb_mid:
        return 0.35                        # 已回到中軌上方，反轉題材淡
    return NEUTRAL


@register("reversal.washout_low_vol", "reversal", ["price_df"],
          "跌深反彈量縮（近 20 日跌 >10% 且量縮）", lookback_min=60)
def washout_low_vol(ctx, params):
    p = ctx.price_df
    if "close" not in p.columns or "volume" not in p.columns or len(p) < 20:
        return NEUTRAL
    close = pd.to_numeric(p["close"], errors="coerce")
    vol = pd.to_numeric(p["volume"], errors="coerce")
    max20 = close.iloc[-20:].max()
    if pd.isna(max20) or max20 <= 0:
        return NEUTRAL
    dd = close.iloc[-1] / max20 - 1.0
    vol_20ma = vol.iloc[-20:].mean()
    deep = dd < -0.1
    low_vol = bool(pd.notna(vol_20ma) and vol_20ma > 0 and vol.iloc[-1] < 0.7 * vol_20ma)
    if deep and low_vol:
        return 0.8
    if deep or low_vol:
        return 0.6
    return 0.4
