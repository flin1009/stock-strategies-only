"""成長派因子（§7 §3.2 + §3.10）——「EPS / 營收 YoY 加速」。

EPS 用「單季」(fundamentals.eps_q，鍵 (year,quarter))，不是年度累計，
避免舊季度混進來鈍化加速度訊號。

§3.10 EPS 公布日對齊（no look-ahead）：FinMind 回的 date 是「所屬期末日」非公布日，
故用保守 deadline 推公布日過濾——只放「公布日 ≤ as_of」的季：
  Q1→5/15、Q2→8/14、Q3→11/14、Q4(年報)→隔年 3/31。

缺料判定由 registry（required_data=["fundamentals"]/["revenue"]）負責回 None；
本體有料但樣本不足 → 回 NEUTRAL(0.5)。
"""
from __future__ import annotations

import pandas as pd

from .base import NEUTRAL, clip01, rank_pct, zscore_clip
from .registry import register

# 季別 → (公布月, 公布日, 是否隔年)
_DEADLINE = {1: (5, 15, 0), 2: (8, 14, 0), 3: (11, 14, 0), 4: (3, 31, 1)}


def _eps_publish_date(year: int, quarter: int) -> pd.Timestamp:
    month, day, next_year = _DEADLINE[quarter]
    return pd.Timestamp(year=year + next_year, month=month, day=day)


def _available_quarters(eps_q: dict, as_of: pd.Timestamp) -> list[tuple[int, int]]:
    """回「公布日 ≤ as_of」的季鍵，依時間遞增排序。"""
    avail = [
        (y, q) for (y, q), v in eps_q.items()
        if v is not None and not pd.isna(v) and _eps_publish_date(y, q) <= as_of
    ]
    return sorted(avail)


def _yoy_series(eps_q: dict, quarters: list[tuple[int, int]]) -> list[float]:
    """對每個已公布季算 YoY（與去年同季比），缺去年同季則略過。"""
    yoys = []
    for (y, q) in quarters:
        prev = eps_q.get((y - 1, q))
        cur = eps_q.get((y, q))
        if prev is None or cur is None or pd.isna(prev) or pd.isna(cur) or prev == 0:
            continue
        yoys.append((cur - prev) / abs(prev))
    return yoys


@register("growth.eps_yoy", "growth", ["fundamentals"],
          "單季 EPS YoY（最新已公布季 vs 去年同季）", lookback_min=1)
def eps_yoy(ctx, params):
    eps_q = ctx.fundamentals.get("eps_q") or {}
    quarters = _available_quarters(eps_q, ctx.as_of)
    yoys = _yoy_series(eps_q, quarters)
    if len(yoys) < 2:                     # 不足 2 年資料（無法形成 YoY 序列）→ 中性
        return NEUTRAL
    s = pd.Series(yoys[-8:])              # 近 8 季 yoy 的 mean/std
    return clip01(zscore_clip(yoys[-1], s.mean(), s.std()))


@register("growth.eps_accel", "growth", ["fundamentals"],
          "單季 EPS YoY 加速度（近 2 季 yoy 連續放大）", lookback_min=1)
def eps_accel(ctx, params):
    eps_q = ctx.fundamentals.get("eps_q") or {}
    quarters = _available_quarters(eps_q, ctx.as_of)
    yoys = _yoy_series(eps_q, quarters)
    if len(yoys) < 3:                     # accel 需至少 3 個 yoy（兩個差分）
        return NEUTRAL
    accels = [yoys[i] - yoys[i - 1] for i in range(1, len(yoys))]
    s = pd.Series(accels[-6:])           # 近 6 季 accel 的 mean/std
    return clip01(zscore_clip(accels[-1], s.mean(), s.std()))


@register("growth.rev_yoy", "growth", ["revenue"],
          "月營收 YoY 相對自身近 36 月百分位", lookback_min=1)
def rev_yoy(ctx, params):
    rev = ctx.revenue
    if "yoy" not in rev.columns:
        return NEUTRAL
    s = pd.to_numeric(rev["yoy"], errors="coerce").dropna().iloc[-36:]
    if len(s) < 2:
        return NEUTRAL
    return clip01(rank_pct(s, float(s.iloc[-1])))
