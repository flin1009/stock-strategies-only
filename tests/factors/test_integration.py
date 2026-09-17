"""全因子註冊整合測試。"""
import stock_strategies.factors  # noqa: F401  觸發全部註冊
from stock_strategies.factors.registry import FACTOR_REGISTRY, list_factors


def test_all_factors_registered():
    expected = {
        "value.cheap_pb", "value.cheap_pe", "value.high_yield",
        "growth.eps_yoy", "growth.eps_accel", "growth.rev_yoy",
        "momentum.rs_self", "momentum.dist_52w_high", "momentum.ma_slope", "momentum.above_mas",
        "chips.foreign_buy_streak", "chips.inst_net_strength", "chips.foreign_holding_up", "chips.margin_retreat",
        "revenue.yoy_accel", "revenue.mom_turn", "revenue.new_high_streak",
        "reversal.kd_oversold", "reversal.bb_lower_bounce", "reversal.washout_low_vol",
        "breakout.box_break", "breakout.vol_confirm_break", "breakout.swing_new_high",
        "legacy.tech_score", "legacy.ma_alignment", "legacy.bollinger_bounce",
        "legacy.kd_golden_cross", "legacy.macd_bullish", "legacy.volume_bonus",
    }
    missing = expected - set(FACTOR_REGISTRY)
    assert not missing, f"缺少: {missing}"


def test_list_factors_counts():
    assert len(list_factors("value")) == 3
    assert len(list_factors("momentum")) == 4
    assert len(list_factors("legacy")) == 6
    assert len(list_factors()) >= 29
