"""籌碼派因子（§7 §3.4）——「法人連買 / 外資加碼 / 融資退場」。

籌碼資料對齊用「資料日」(date 欄)；build_context 已切 ≤ as_of，逐日切片自然安全。
缺料判定由 registry 負責回 None；本體有料但樣本不足 → NEUTRAL(0.5)。
"""
from __future__ import annotations

import pandas as pd

from .base import NEUTRAL, clip01, zscore_clip
from .registry import register


@register("chips.foreign_buy_streak", "chips", ["inst"],
          "外資連續買超天數（cap=5）", lookback_min=1)
def foreign_buy_streak(ctx, params):
    if "foreign_net" not in ctx.inst.columns:
        return NEUTRAL
    cap = params.get("streak_cap", 5)
    net = pd.to_numeric(ctx.inst["foreign_net"], errors="coerce").dropna()
    if net.empty:
        return NEUTRAL
    k = 0
    for v in reversed(net.tolist()):      # 從 t 往回數連續買超
        if v > 0:
            k += 1
        else:
            break
    return clip01(k / cap)


@register("chips.inst_net_strength", "chips", ["inst", "price_df"],
          "三大法人近 5 日淨買 / 成交量，相對近 60 日 z-score", lookback_min=1)
def inst_net_strength(ctx, params):
    n = params.get("net_window", 5)
    inst = ctx.inst
    price = ctx.price_df
    if "total_net" not in inst.columns or "volume" not in price.columns:
        return NEUTRAL
    merged = pd.merge(
        inst[["date", "total_net"]], price[["date", "volume"]], on="date", how="inner"
    )
    merged["total_net"] = pd.to_numeric(merged["total_net"], errors="coerce")
    merged["volume"] = pd.to_numeric(merged["volume"], errors="coerce")
    merged = merged.dropna(subset=["total_net", "volume"])
    if len(merged) < n + 2:
        return NEUTRAL
    net_roll = merged["total_net"].rolling(n).sum()
    vol_roll = merged["volume"].rolling(n).sum().replace(0, pd.NA)
    ratio = (net_roll / vol_roll).dropna()
    if len(ratio) < 2:
        return NEUTRAL
    s = ratio.iloc[-60:]
    return clip01(zscore_clip(float(ratio.iloc[-1]), s.mean(), s.std()))


@register("chips.foreign_holding_up", "chips", ["shareholding"],
          "外資持股比率近 20 日變化，相對近 120 日 z-score", lookback_min=1)
def foreign_holding_up(ctx, params):
    if "foreign_ratio" not in ctx.shareholding.columns:
        return NEUTRAL
    lag = params.get("holding_lag", 20)
    fr = pd.to_numeric(ctx.shareholding["foreign_ratio"], errors="coerce").dropna()
    d = (fr - fr.shift(lag)).dropna()
    if len(d) < 2 or pd.isna(d.iloc[-1]):
        return NEUTRAL
    s = d.iloc[-120:]
    return clip01(zscore_clip(float(d.iloc[-1]), s.mean(), s.std()))


@register("chips.margin_retreat", "chips", ["margin"],
          "融資餘額近 20 日變化；融資減（散戶退場）→ 看多", lookback_min=1)
def margin_retreat(ctx, params):
    if "margin_balance" not in ctx.margin.columns:
        return NEUTRAL
    lag = params.get("margin_lag", 20)
    mb = pd.to_numeric(ctx.margin["margin_balance"], errors="coerce").dropna()
    chg = (mb / mb.shift(lag) - 1.0).dropna()
    if len(chg) < 2 or pd.isna(chg.iloc[-1]):
        return NEUTRAL
    s = chg.iloc[-120:]
    # 融資減（chg<0）→ 看多：1 - zscore_clip
    return clip01(1.0 - zscore_clip(float(chg.iloc[-1]), s.mean(), s.std()))
