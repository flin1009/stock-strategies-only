import pandas as pd
from stock_strategies import datasources as ds


def test_margin_ratio_and_chg(monkeypatch):
    raw = pd.DataFrame({
        "date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
        "MarginPurchaseTodayBalance": [1000, 1100],
        "ShortSaleTodayBalance": [200, 260],
    })
    monkeypatch.setattr(ds, "fetch_finmind_cached", lambda *a, **k: raw.copy())
    out = ds.get_margin("2330", "2024-01-01")
    assert out.iloc[1]["margin_balance"] == 1100
    assert out.iloc[1]["margin_chg"] == 100
    assert out.iloc[1]["short_chg"] == 60
    assert abs(out.iloc[1]["short_margin_ratio"] - 260 / 1100) < 1e-9
