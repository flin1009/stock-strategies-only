import pandas as pd
from stock_strategies.factors.registry import (
    register, compute_factor, compute_all_factors, list_factors,
)
from stock_strategies.context import FactorContext


def _ctx(price_rows=80):
    px = pd.DataFrame({"date": pd.bdate_range("2023-01-02", periods=price_rows), "close": 10.0})
    return FactorContext(stock_id="x", as_of=pd.Timestamp("2024-01-01"),
                         price_df=px, index_df=pd.DataFrame(), inst=pd.DataFrame(),
                         revenue=pd.DataFrame(), valuation=pd.DataFrame(),
                         margin=pd.DataFrame(), shareholding=pd.DataFrame(), fundamentals={})


def test_missing_required_data_returns_none():
    @register("t.needs_inst", "test", ["inst"], "", lookback_min=1)
    def _f(ctx, params):
        return 1.0
    assert compute_factor("t.needs_inst", _ctx(), {}) is None


def test_internal_neutral_is_half():
    @register("t.internal_neutral", "test", ["price_df"], "", lookback_min=1)
    def _f(ctx, params):
        return 0.5
    assert compute_factor("t.internal_neutral", _ctx(), {}) == 0.5


def test_unknown_factor_returns_none():
    assert compute_factor("nope.nope", _ctx(), {}) is None


def test_exception_returns_neutral():
    @register("t.boom", "test", ["price_df"], "", lookback_min=1)
    def _f(ctx, params):
        raise ValueError("boom")
    assert compute_factor("t.boom", _ctx(), {}) == 0.5


def test_compute_all_excludes_none():
    @register("t.a", "test", ["price_df"], "", lookback_min=1)
    def _a(ctx, params):
        return 1.0
    @register("t.b", "test", ["inst"], "", lookback_min=1)
    def _b(ctx, params):
        return 0.0
    out = compute_all_factors(_ctx(), [{"name": "t.a", "weight": 1},
                                       {"name": "t.b", "weight": 1}], {})
    assert out["composite"] == 1.0
    assert "t.b" in out["missing"]


def test_list_factors_filter():
    assert any(f["name"] == "t.a" for f in list_factors("test"))
