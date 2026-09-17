import pandas as pd
from stock_strategies import datasources as ds


def test_index_fallback_taiex_to_twii(monkeypatch):
    twii = pd.DataFrame({
        "date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
        "open": [17000, 17100], "max": [17050, 17150],
        "min": [16950, 17050], "close": [17020, 17120],
    })

    def fake_fetch(dataset, data_id, start, *a, **k):
        if data_id == "TAIEX":
            return pd.DataFrame()      # TAIEX 抓不到
        if data_id == "TWII":
            return twii.copy()
        return pd.DataFrame()

    monkeypatch.setattr(ds, "fetch_finmind_cached", fake_fetch)
    out = ds.get_index_history("TAIEX", start="2024-01-01")
    assert {"date", "open", "high", "low", "close"}.issubset(out.columns)
    assert len(out) == 2
    assert out.iloc[1]["high"] == 17150   # max→high 正規化
