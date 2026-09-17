"""§7 §7 測試點 11-12：籌碼派。"""
import pandas as pd

import stock_strategies.factors.chips  # noqa: F401  觸發註冊
from stock_strategies.context import FactorContext
from stock_strategies.factors.registry import compute_factor


def _ctx(*, inst=None, margin=None, shareholding=None, price_df=None) -> FactorContext:
    px = price_df if price_df is not None else pd.DataFrame(
        {"date": pd.bdate_range("2023-01-02", periods=80), "close": 10.0,
         "volume": [1_000_000] * 80})
    return FactorContext(
        stock_id="x", as_of=pd.Timestamp("2024-01-01"),
        price_df=px, index_df=pd.DataFrame(),
        inst=inst if inst is not None else pd.DataFrame(),
        revenue=pd.DataFrame(), valuation=pd.DataFrame(),
        margin=margin if margin is not None else pd.DataFrame(),
        shareholding=shareholding if shareholding is not None else pd.DataFrame(),
        fundamentals={},
    )


def _inst(foreign_nets):
    n = len(foreign_nets)
    dates = pd.bdate_range("2023-09-01", periods=n)
    return pd.DataFrame({
        "date": dates,
        "foreign_net": foreign_nets,
        "trust_net": [0] * n,
        "dealer_net": [0] * n,
        "total_net": foreign_nets,
    })


# 測試點 11：chips.foreign_buy_streak
def test_foreign_buy_streak_five_positive_max():
    # 末 5 日 foreign_net 全正 → 1.0
    nets = [-100] * 60 + [500, 600, 700, 800, 900]
    ctx = _ctx(inst=_inst(nets))
    assert compute_factor("chips.foreign_buy_streak", ctx, {}) == 1.0


def test_foreign_buy_streak_last_negative_zero():
    # 末日為負 → streak=0 → 0.0
    nets = [500] * 64 + [-100]
    ctx = _ctx(inst=_inst(nets))
    assert compute_factor("chips.foreign_buy_streak", ctx, {}) == 0.0


def test_foreign_buy_streak_missing_returns_none():
    ctx = _ctx(inst=pd.DataFrame())
    assert compute_factor("chips.foreign_buy_streak", ctx, {}) is None


# chips.inst_net_strength
def test_inst_net_strength_strong_buy_bullish():
    # 近 5 日淨買大增（後段法人猛買）→ ratio 為近 60 日最高 → >0.5
    base = [1000] * 60 + [50_000] * 5
    inst = _inst(base)
    n = len(base)
    px = pd.DataFrame({"date": pd.bdate_range("2023-09-01", periods=n),
                       "close": 10.0, "volume": [1_000_000] * n})
    ctx = _ctx(inst=inst, price_df=px)
    assert compute_factor("chips.inst_net_strength", ctx, {}) > 0.5


def test_inst_net_strength_strong_sell_bearish():
    base = [1000] * 60 + [-50_000] * 5
    inst = _inst(base)
    n = len(base)
    px = pd.DataFrame({"date": pd.bdate_range("2023-09-01", periods=n),
                       "close": 10.0, "volume": [1_000_000] * n})
    ctx = _ctx(inst=inst, price_df=px)
    assert compute_factor("chips.inst_net_strength", ctx, {}) < 0.5


# chips.foreign_holding_up
def _shareholding(ratios):
    n = len(ratios)
    return pd.DataFrame({"date": pd.bdate_range("2023-06-01", periods=n),
                         "foreign_ratio": ratios})


def test_foreign_holding_up_rising_bullish():
    # 近 20 日外資持股比率明顯上升（後段加碼）→ >0.5
    ratios = [30.0] * 130 + [30.0 + i * 0.3 for i in range(20)]
    ctx = _ctx(shareholding=_shareholding(ratios))
    assert compute_factor("chips.foreign_holding_up", ctx, {}) > 0.5


def test_foreign_holding_up_falling_bearish():
    ratios = [30.0] * 130 + [30.0 - i * 0.3 for i in range(20)]
    ctx = _ctx(shareholding=_shareholding(ratios))
    assert compute_factor("chips.foreign_holding_up", ctx, {}) < 0.5


# 測試點 12：chips.margin_retreat
def _margin(balances):
    n = len(balances)
    return pd.DataFrame({"date": pd.bdate_range("2023-06-01", periods=n),
                         "margin_balance": balances,
                         "short_balance": [1000] * n})


def test_margin_retreat_falling_bullish():
    # 融資餘額近 20 日大幅下降 → 散戶退場 → 看多 >0.5
    bals = [10000.0] * 130 + [10000.0 - i * 200 for i in range(20)]
    ctx = _ctx(margin=_margin(bals))
    assert compute_factor("chips.margin_retreat", ctx, {}) > 0.5


def test_margin_retreat_rising_bearish():
    # 融資餘額大幅上升 → 散戶進場 → 看空 <0.5
    bals = [10000.0] * 130 + [10000.0 + i * 200 for i in range(20)]
    ctx = _ctx(margin=_margin(bals))
    assert compute_factor("chips.margin_retreat", ctx, {}) < 0.5


def test_margin_retreat_missing_returns_none():
    ctx = _ctx(margin=pd.DataFrame())
    assert compute_factor("chips.margin_retreat", ctx, {}) is None
