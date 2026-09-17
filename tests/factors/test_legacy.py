"""legacy 向後相容因子測試（spec §7 §7 測試點 20-21）。"""
import pandas as pd

import stock_strategies.factors.legacy  # noqa: F401  觸發註冊
from stock_strategies.indicators import add_indicators, tech_score_at
from stock_strategies.factors.legacy import legacy_params_to_factors
from stock_strategies.factors.registry import compute_factor
from stock_strategies.context import FactorContext


def _ctx(n=80):
    px = pd.DataFrame({
        "date": pd.bdate_range("2023-01-02", periods=n),
        "open": [10.0 + i * 0.1 for i in range(n)],
        "high": [10.2 + i * 0.1 for i in range(n)],
        "low": [9.8 + i * 0.1 for i in range(n)],
        "close": [10.0 + i * 0.1 for i in range(n)],
        "volume": [1000 + i for i in range(n)],
    })
    px = add_indicators(px)
    e = pd.DataFrame()
    return FactorContext(stock_id="x", as_of=pd.Timestamp("2024-01-01"),
                         price_df=px, index_df=e, inst=e, revenue=e,
                         valuation=e, margin=e, shareholding=e, fundamentals={})


def test_legacy_tech_matches_old():
    ctx = _ctx()
    params = {"use_ma_alignment": True, "use_bollinger_bounce": True,
              "use_kd_golden_cross": True, "use_macd_bullish": True}
    factor_val = compute_factor("legacy.tech_score", ctx, params)
    old = tech_score_at(ctx.price_df.iloc[-1], params)["score"] / 100.0
    assert abs(factor_val - old) < 0.011


def test_legacy_params_to_factors():
    allon = {"use_ma_alignment": True, "use_bollinger_bounce": True,
             "use_kd_golden_cross": True, "use_macd_bullish": True, "use_volume_patterns": True}
    fl = legacy_params_to_factors(allon)
    assert len(fl) == 5 and all("legacy." in f["name"] for f in fl)
    assert legacy_params_to_factors({k: False for k in allon}) == []
