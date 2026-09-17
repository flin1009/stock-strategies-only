"""技術反轉派因子測試（spec §7 §7 測試點 16-17）。"""
import pandas as pd

import stock_strategies.factors.reversal  # noqa: F401  觸發因子註冊
from stock_strategies.factors.registry import compute_factor
from stock_strategies.context import FactorContext


def _ctx(price_df):
    e = pd.DataFrame()
    return FactorContext(stock_id="x", as_of=pd.Timestamp("2024-06-30"),
                         price_df=price_df, index_df=e, inst=e, revenue=e,
                         valuation=e, margin=e, shareholding=e, fundamentals={})


def _base(n=70):
    return pd.DataFrame({
        "date": pd.bdate_range("2024-01-01", periods=n),
        "open": [10.0] * n, "high": [10.0] * n, "low": [10.0] * n,
        "close": [10.0] * n, "volume": [1000.0] * n,
    })


def test_kd_oversold_bounce_high():
    df = _base()
    df["k"] = [50.0] * 68 + [15.0, 20.0]   # k_prev=15, k_t=20（<30 翹頭）
    df["d"] = [50.0] * 69 + [18.0]         # k_t(20) > d(18) 金叉
    assert compute_factor("reversal.kd_oversold", _ctx(df), {}) == 1.0


def test_kd_overbought_below_neutral():
    df = _base()
    df["k"] = [50.0] * 68 + [80.0, 85.0]   # k_t=85（高檔）
    df["d"] = [50.0] * 70
    v = compute_factor("reversal.kd_oversold", _ctx(df), {})
    assert v < 0.5


def test_bb_lower_bounce():
    df = _base()
    df["bb_lower"] = [10.0] * 70
    df["bb_mid"] = [11.0] * 70
    df.loc[68, "close"] = 10.0
    df.loc[69, "close"] = 10.2            # dist=(10.2-10)/10=0.02 ∈(0,0.03) 且向上
    assert compute_factor("reversal.bb_lower_bounce", _ctx(df), {}) == 0.85
