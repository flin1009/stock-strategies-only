import pandas as pd
from stock_strategies import datasources as ds


def test_institutional_pivot_and_net(monkeypatch):
    raw = pd.DataFrame({
        "date": pd.to_datetime(["2024-01-02"] * 4),
        "name": ["Foreign_Investor", "Investment_Trust", "Dealer_self", "Dealer_Hedging"],
        "buy": [5000, 2000, 1000, 500],
        "sell": [1000, 500, 800, 200],
    })
    monkeypatch.setattr(ds, "fetch_finmind_cached", lambda *a, **k: raw.copy())
    out = ds.get_institutional("2330", "2024-01-01")
    row = out.iloc[0]
    assert row["foreign_net"] == 4000      # 5000-1000
    assert row["trust_net"] == 1500        # 2000-500
    assert row["dealer_net"] == 500        # (1000-800)+(500-200)=200+300
    assert row["total_net"] == 4000 + 1500 + 500


def test_institutional_empty(monkeypatch):
    monkeypatch.setattr(ds, "fetch_finmind_cached", lambda *a, **k: pd.DataFrame())
    out = ds.get_institutional("2330", "2024-01-01")
    assert out.empty
