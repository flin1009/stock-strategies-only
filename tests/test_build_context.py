import pandas as pd
from stock_strategies import context as ctxmod


def _install_fakes(monkeypatch):
    dates = pd.bdate_range("2022-01-03", periods=400)
    price = pd.DataFrame({"date": dates, "open": 1.0, "high": 1.0, "low": 1.0,
                          "close": [10.0 + i * 0.05 for i in range(len(dates))], "volume": 1000})
    idx = pd.DataFrame({"date": dates, "open": 17000.0, "high": 17000.0,
                        "low": 17000.0, "close": 17000.0})
    monkeypatch.setattr(ctxmod, "get_price_history_cached", lambda *a, **k: price.copy())
    monkeypatch.setattr(ctxmod.ds, "get_index_history", lambda *a, **k: idx.copy())
    monkeypatch.setattr(ctxmod.ds, "get_institutional", lambda *a, **k: pd.DataFrame())
    monkeypatch.setattr(ctxmod.ds, "get_month_revenue", lambda *a, **k: pd.DataFrame())
    monkeypatch.setattr(ctxmod.ds, "get_valuation", lambda *a, **k: pd.DataFrame())
    monkeypatch.setattr(ctxmod.ds, "get_margin", lambda *a, **k: pd.DataFrame())
    monkeypatch.setattr(ctxmod.ds, "get_shareholding", lambda *a, **k: pd.DataFrame())
    monkeypatch.setattr(ctxmod, "_get_fundamentals_raw", lambda sid: {"eps": {2022: 30.0}, "roe": {2022: 25.0}})
    monkeypatch.setattr(ctxmod.ds, "get_capital_and_industry",
                        lambda *a, **k: {"industry": "Semiconductor", "shares_outstanding": None, "market_cap": None})


def test_build_context_point_in_time_monotonic(monkeypatch):
    _install_fakes(monkeypatch)
    c1 = ctxmod.build_context("2330", "2022-06-01")
    c2 = ctxmod.build_context("2330", "2023-06-01")
    assert c1.price_df["date"].max() <= pd.Timestamp("2022-06-01")
    assert c2.price_df["date"].max() <= pd.Timestamp("2023-06-01")
    # t1 的價格是 t2 的子集（單調，無未來資訊）
    assert c1.price_df["date"].max() < c2.price_df["date"].max()
    assert c1.as_of == pd.Timestamp("2022-06-01")


def test_build_context_strict_false_survives_missing(monkeypatch):
    _install_fakes(monkeypatch)
    # 法人等回空 → meta.missing 有記錄，但不 raise
    c = ctxmod.build_context("2330", "2022-06-01")
    assert "inst" in c.meta["missing"]
