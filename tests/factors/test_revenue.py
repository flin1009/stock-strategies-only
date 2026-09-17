"""營收動能派因子測試（spec §7 §7 測試點 13-14）。"""
import pandas as pd

import stock_strategies.factors.revenue  # noqa: F401  觸發因子註冊
from stock_strategies.factors.registry import compute_factor
from stock_strategies.context import FactorContext


def _ctx(revenue):
    e = pd.DataFrame()
    return FactorContext(stock_id="x", as_of=pd.Timestamp("2024-06-30"),
                         price_df=e, index_df=e, inst=e, revenue=revenue,
                         valuation=e, margin=e, shareholding=e, fundamentals={})


def _rev(yoy=None, mom=None, revenue=None, n=18):
    periods = pd.date_range("2023-01-01", periods=n, freq="MS")
    return pd.DataFrame({
        "period": periods,
        "revenue": revenue if revenue is not None else [100.0] * n,
        "mom": mom if mom is not None else [0.0] * n,
        "yoy": yoy if yoy is not None else [0.0] * n,
    })


def test_yoy_accel_rising_returns_one():
    yoy = [0.05] * 15 + [0.10, 0.20, 0.35]
    assert compute_factor("revenue.yoy_accel", _ctx(_rev(yoy=yoy)), {}) == 1.0


def test_yoy_accel_falling_below_055():
    yoy = [0.30] * 15 + [0.35, 0.20, 0.05]
    assert compute_factor("revenue.yoy_accel", _ctx(_rev(yoy=yoy)), {}) < 0.55


def test_revenue_missing_returns_none():
    # revenue df 空 → required_data 缺 → None
    assert compute_factor("revenue.yoy_accel", _ctx(pd.DataFrame()), {}) is None


def test_new_high_streak_three_months():
    revenue = [100.0] * 12 + [110.0, 120.0, 130.0]
    assert compute_factor("revenue.new_high_streak", _ctx(_rev(revenue=revenue, n=15)), {}) == 1.0


def test_new_high_streak_flat_is_zero():
    revenue = [100.0] * 15
    assert compute_factor("revenue.new_high_streak", _ctx(_rev(revenue=revenue, n=15)), {}) == 0.0
