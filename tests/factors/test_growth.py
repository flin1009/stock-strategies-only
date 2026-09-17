"""§7 §7 測試點 6-7：成長派。"""
import pandas as pd

import stock_strategies.factors.growth  # noqa: F401  觸發註冊
from stock_strategies.context import FactorContext
from stock_strategies.factors.registry import compute_factor


def _ctx(*, eps_q=None, revenue=None, as_of="2024-04-01") -> FactorContext:
    px = pd.DataFrame({"date": pd.bdate_range("2023-01-02", periods=80), "close": 10.0})
    fundamentals = {"eps": {}, "roe": {}, "eps_q": eps_q or {}}
    return FactorContext(
        stock_id="x", as_of=pd.Timestamp(as_of),
        price_df=px, index_df=pd.DataFrame(), inst=pd.DataFrame(),
        revenue=revenue if revenue is not None else pd.DataFrame(),
        valuation=pd.DataFrame(), margin=pd.DataFrame(),
        shareholding=pd.DataFrame(), fundamentals=fundamentals,
    )


def _eps_q_flat_then(last_q, last_val):
    """8 季平穩 EPS=1.0，再加一季 (last_q) = last_val（含去年同季供 YoY）。"""
    q = {}
    # 2021 全年 + 2022 全年 + 2023 全年 平穩 1.0
    for y in (2021, 2022, 2023):
        for qq in (1, 2, 3, 4):
            q[(y, qq)] = 1.0
    # 最新季 2024Q1
    q[(2024, 1)] = last_val
    return q


# 測試點 6：growth.eps_yoy
def test_eps_yoy_double_bullish():
    # 2024Q1 = 2.0，去年同季 2023Q1 = 1.0 → YoY = +100% → 明顯看多
    eps_q = _eps_q_flat_then((2024, 1), 2.0)
    ctx = _ctx(eps_q=eps_q, as_of="2024-05-20")  # 過 Q1 deadline 5/15
    assert compute_factor("growth.eps_yoy", ctx, {}) > 0.5


def test_eps_yoy_halved_bearish():
    eps_q = _eps_q_flat_then((2024, 1), 0.5)  # 腰斬 → 看空
    ctx = _ctx(eps_q=eps_q, as_of="2024-05-20")
    assert compute_factor("growth.eps_yoy", ctx, {}) < 0.5


def test_eps_yoy_one_year_only_neutral():
    eps_q = {(2023, 1): 1.0, (2024, 1): 2.0}  # 只有 1 年，不足 2 年 → 0.5
    ctx = _ctx(eps_q=eps_q, as_of="2024-05-20")
    assert compute_factor("growth.eps_yoy", ctx, {}) == 0.5


def test_eps_yoy_deadline_filters_unpublished():
    # as_of 2024-04-01 < Q1 deadline 5/15 → 2024Q1 還沒公布，最新可用為 2023Q4
    eps_q = _eps_q_flat_then((2024, 1), 5.0)  # 2024Q1 暴增但不該被看到
    ctx = _ctx(eps_q=eps_q, as_of="2024-04-01")
    # 最新可用 2023Q4=1.0 vs 2022Q4=1.0 → YoY 0 → 中性附近，不應是暴漲
    val = compute_factor("growth.eps_yoy", ctx, {})
    assert abs(val - 0.5) < 0.2


def test_eps_yoy_missing_returns_none():
    ctx = FactorContext(
        stock_id="x", as_of=pd.Timestamp("2024-05-20"),
        price_df=pd.DataFrame({"date": pd.bdate_range("2023-01-02", periods=80), "close": 10.0}),
        index_df=pd.DataFrame(), inst=pd.DataFrame(), revenue=pd.DataFrame(),
        valuation=pd.DataFrame(), margin=pd.DataFrame(),
        shareholding=pd.DataFrame(), fundamentals={},  # fundamentals 空 → None
    )
    assert compute_factor("growth.eps_yoy", ctx, {}) is None


# 測試點 7：growth.eps_accel
def test_eps_accel_widening_bullish():
    # YoY 連 2 季放大：2023Q4 yoy 小、2024Q1 yoy 大
    q = {}
    for y in (2021, 2022):
        for qq in (1, 2, 3, 4):
            q[(y, qq)] = 1.0
    # 2023 全年溫和成長
    q[(2023, 1)] = 1.05
    q[(2023, 2)] = 1.05
    q[(2023, 3)] = 1.05
    q[(2023, 4)] = 1.1   # yoy vs 2022Q4=1.0 → +10%
    q[(2024, 1)] = 1.5   # yoy vs 2023Q1=1.05 → +43%（加速）
    ctx = _ctx(eps_q=q, as_of="2024-05-20")
    assert compute_factor("growth.eps_accel", ctx, {}) > 0.5


def test_eps_accel_shrinking_bearish():
    q = {}
    for y in (2021, 2022):
        for qq in (1, 2, 3, 4):
            q[(y, qq)] = 1.0
    q[(2023, 1)] = 1.5
    q[(2023, 2)] = 1.5
    q[(2023, 3)] = 1.5
    q[(2023, 4)] = 2.0   # yoy vs 2022Q4=1.0 → +100%
    q[(2024, 1)] = 1.6   # yoy vs 2023Q1=1.5 → +7%（減速）
    ctx = _ctx(eps_q=q, as_of="2024-05-20")
    assert compute_factor("growth.eps_accel", ctx, {}) < 0.5


def test_eps_accel_insufficient_neutral():
    q = {(2023, 1): 1.0, (2024, 1): 2.0}
    ctx = _ctx(eps_q=q, as_of="2024-05-20")
    assert compute_factor("growth.eps_accel", ctx, {}) == 0.5


# growth.rev_yoy
def _rev(yoys, n=None):
    n = n or len(yoys)
    periods = pd.date_range("2021-01-01", periods=n, freq="MS")
    return pd.DataFrame({
        "avail_date": periods + pd.Timedelta(days=40),
        "period": periods,
        "revenue": [100.0] * n,
        "yoy": yoys,
        "mom": [0.0] * n,
    })


def test_rev_yoy_high_today_bullish():
    yoys = [5.0] * 35 + [80.0]  # 最新月 YoY 在近 36 月最高
    ctx = _ctx(revenue=_rev(yoys))
    assert compute_factor("growth.rev_yoy", ctx, {}) > 0.9


def test_rev_yoy_low_today_bearish():
    yoys = [50.0] * 35 + [-30.0]  # 最新月 YoY 最低
    ctx = _ctx(revenue=_rev(yoys))
    assert compute_factor("growth.rev_yoy", ctx, {}) < 0.1


def test_rev_yoy_missing_returns_none():
    ctx = _ctx(revenue=pd.DataFrame())
    assert compute_factor("growth.rev_yoy", ctx, {}) is None
