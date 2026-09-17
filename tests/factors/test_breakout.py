"""突破派因子測試（spec §7 §7 測試點 18-19）。"""
import pandas as pd

import stock_strategies.factors.breakout  # noqa: F401  觸發因子註冊
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
        "open": [10.0] * n, "high": [10.0] * n, "low": [9.5] * n,
        "close": [9.8] * n, "volume": [1000.0] * n,
    })


def test_box_break_just_broke():
    df = _base()
    df.loc[69, "close"] = 10.1            # box_top=10（前20日high），ratio=1.01 → ≈0.8
    v = compute_factor("breakout.box_break", _ctx(df), {})
    assert 0.75 <= v <= 0.85


def test_box_top_excludes_current_day():
    df = _base()
    df.loc[69, "high"] = 100.0            # 當日 high 超大，但箱頂不含當日 → 不受影響
    df.loc[69, "close"] = 10.1
    v = compute_factor("breakout.box_break", _ctx(df), {})
    assert 0.75 <= v <= 0.85             # 若誤用當日 high=100 當箱頂，ratio<1 會給低分


def test_box_break_inside_box_below_06():
    df = _base()
    df.loc[69, "close"] = 9.8            # 未突破，箱內
    assert compute_factor("breakout.box_break", _ctx(df), {}) < 0.6


def test_vol_confirm_break_with_volume():
    df = _base()
    df.loc[69, "close"] = 10.1
    df.loc[69, "volume"] = 2500.0        # 量 2.5x 20ma(1000) → 1.0
    assert compute_factor("breakout.vol_confirm_break", _ctx(df), {}) == 1.0


def test_vol_confirm_break_no_volume():
    df = _base()
    df.loc[69, "close"] = 10.1
    df.loc[69, "volume"] = 500.0         # 量 <1x → 假突破 0.3
    assert compute_factor("breakout.vol_confirm_break", _ctx(df), {}) == 0.3
