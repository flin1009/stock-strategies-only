import pandas as pd
from stock_strategies import context as ctxmod
from stock_strategies.context import build_context_from_bundle


def _bundle(price_rows=120):
    dates = pd.bdate_range("2022-01-03", periods=price_rows)
    price = pd.DataFrame({"date": dates, "open": 1.0, "high": 1.0, "low": 1.0,
                          "close": [10.0 + i * 0.1 for i in range(price_rows)], "volume": 1000})
    return {
        "price": price,
        "index": pd.DataFrame({"date": dates, "close": 17000.0}),
        "inst": pd.DataFrame(),
        "revenue": pd.DataFrame(),
        "valuation": pd.DataFrame(),
        "margin": pd.DataFrame(),
        "shareholding": pd.DataFrame(),
        "fundamentals_raw": {"eps": {2022: 30.0, 2023: 32.0}, "roe": {2022: 25.0, 2023: 26.0}},
        "capital": {"industry": "Semiconductor", "shares_outstanding": None, "market_cap": None},
    }


def test_fundamentals_asof_publish_date():
    b = _bundle()
    # 2023 年度 EPS 發布日 = 2024-03-31
    ctx = build_context_from_bundle("2330", pd.Timestamp("2024-03-30"), b)
    assert 2023 not in ctx.fundamentals["eps"]   # 還沒發布
    assert 2022 in ctx.fundamentals["eps"]
    ctx2 = build_context_from_bundle("2330", pd.Timestamp("2024-03-31"), b)
    assert 2023 in ctx2.fundamentals["eps"]      # 發布日當天可用


def test_new_stock_protection_flag():
    b = _bundle(price_rows=30)   # 少於 MIN_PRICE_ROWS(60)
    ctx = build_context_from_bundle("9999", pd.Timestamp("2022-03-01"), b)
    assert "price_history_insufficient" in ctx.meta.get("missing", [])
    # 不 raise，仍回 context


def test_price_sliced_to_asof():
    b = _bundle()
    ctx = build_context_from_bundle("2330", pd.Timestamp("2022-02-01"), b)
    assert ctx.price_df["date"].max() <= pd.Timestamp("2022-02-01")


def test_market_cap_recomputed_from_asof_close():
    """market_cap 應以 as_of 切片後最後收盤重算，不可用最新收盤或 bundle 預存值（review issue #3）。"""
    import pandas as pd
    from stock_strategies.context import build_context_from_bundle
    dates = pd.bdate_range("2022-01-03", periods=400)
    price = pd.DataFrame({
        "date": dates, "open": 1.0, "high": 1.0, "low": 1.0,
        "close": [100.0 + i for i in range(len(dates))], "volume": 1000,
    })
    bundle = {
        "price": price, "index": pd.DataFrame(), "inst": pd.DataFrame(),
        "revenue": pd.DataFrame(), "valuation": pd.DataFrame(), "margin": pd.DataFrame(),
        "shareholding": pd.DataFrame(), "fundamentals_raw": {"eps": {}, "roe": {}},
        "capital": {"industry": "X", "shares_outstanding": 1000.0, "market_cap": 999999.0},
    }
    as_of = pd.Timestamp("2022-02-01")
    ctx = build_context_from_bundle("2330", as_of, bundle)
    asof_close = float(price[price["date"] <= as_of]["close"].iloc[-1])
    assert ctx.market_cap == 1000.0 / 10 * asof_close
    assert ctx.market_cap != 999999.0                              # 不用 bundle 預存值
    assert ctx.market_cap != 1000.0 / 10 * float(price["close"].iloc[-1])  # 不用最新收盤
