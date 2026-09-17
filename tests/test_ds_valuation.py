import pandas as pd
import numpy as np
from stock_strategies import datasources as ds


def test_valuation_normalizes(monkeypatch):
    raw = pd.DataFrame({
        "date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
        "PER": [15.0, -3.0],          # 負本益比 → NaN
        "PBR": [2.0, 2.1],
        "dividend_yield": [3.5, 3.4],
    })
    monkeypatch.setattr(ds, "fetch_finmind_cached", lambda *a, **k: raw.copy())
    out = ds.get_valuation("2330", "2024-01-01")
    assert {"date", "per", "pbr", "dividend_yield"}.issubset(out.columns)
    assert np.isnan(out.iloc[1]["per"])   # 負值轉 NaN
    assert out.iloc[0]["per"] == 15.0


def test_valuation_missing_col_resilient(monkeypatch):
    raw = pd.DataFrame({"date": pd.to_datetime(["2024-01-02"]), "PBR": [2.0]})  # 缺 PER
    monkeypatch.setattr(ds, "fetch_finmind_cached", lambda *a, **k: raw.copy())
    out = ds.get_valuation("2330", "2024-01-01")
    assert out.empty   # 缺關鍵欄 → 回空，不 KeyError
