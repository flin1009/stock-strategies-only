"""legacy 包裝因子（§7 §3.9）——把舊 tech_score_at 四訊號 + 量價型態包成因子。

讓既有策略不改 JSON 也能跑、新策略也能混用。price_df 已含 add_indicators，
tech_score_at 直接吃 ctx.price_df.iloc[-1]。required_data=["price_df"]。
"""
from __future__ import annotations

from .base import NEUTRAL, clip01
from .registry import register
from ..indicators import tech_score_at
from ..volume import detect_patterns

_ALL_OFF = {
    "use_ma_alignment": False, "use_bollinger_bounce": False,
    "use_kd_golden_cross": False, "use_macd_bullish": False,
}


def _single(ctx, use_key: str) -> float:
    flags = dict(_ALL_OFF)
    flags[use_key] = True
    return clip01(tech_score_at(ctx.price_df.iloc[-1], flags)["score"] / 100.0)


@register("legacy.tech_score", "legacy", ["price_df"],
          "舊四訊號技術分(0-100)→0..1（params 透傳 use_* 開關）", lookback_min=60)
def legacy_tech(ctx, params):
    return clip01(tech_score_at(ctx.price_df.iloc[-1], params)["score"] / 100.0)


@register("legacy.ma_alignment", "legacy", ["price_df"], "舊均線多頭", lookback_min=60)
def legacy_ma(ctx, params):
    return _single(ctx, "use_ma_alignment")


@register("legacy.bollinger_bounce", "legacy", ["price_df"], "舊布林下軌反彈", lookback_min=60)
def legacy_bb(ctx, params):
    return _single(ctx, "use_bollinger_bounce")


@register("legacy.kd_golden_cross", "legacy", ["price_df"], "舊 KD 黃金交叉", lookback_min=60)
def legacy_kd(ctx, params):
    return _single(ctx, "use_kd_golden_cross")


@register("legacy.macd_bullish", "legacy", ["price_df"], "舊 MACD 多頭", lookback_min=60)
def legacy_macd(ctx, params):
    return _single(ctx, "use_macd_bullish")


@register("legacy.volume_bonus", "legacy", ["price_df"],
          "舊量價型態 bonus(-20..+18)→0..1", lookback_min=21)
def legacy_volume(ctx, params):
    vp = detect_patterns(ctx.price_df, idx=-1)
    return clip01((vp["bonus"] + 20) / 38.0)   # bonus∈[-20,18] 線性映 0..1


def legacy_params_to_factors(params: dict) -> list[dict]:
    """舊 use_* 開關 → legacy 因子等權清單。沒 factors 欄的舊策略退化用。"""
    mapping = [
        ("use_ma_alignment", "legacy.ma_alignment"),
        ("use_bollinger_bounce", "legacy.bollinger_bounce"),
        ("use_kd_golden_cross", "legacy.kd_golden_cross"),
        ("use_macd_bullish", "legacy.macd_bullish"),
        ("use_volume_patterns", "legacy.volume_bonus"),
    ]
    return [{"name": fn, "weight": 1} for key, fn in mapping if params.get(key)]
