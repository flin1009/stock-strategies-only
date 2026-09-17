import pandas as pd
from stock_strategies import datasources as ds


def test_stock_info_normalize(monkeypatch):
    raw = pd.DataFrame({
        "stock_id": ["2330", "2317"],
        "stock_name": ["台積電", "鴻海"],
        "industry_category": ["Semiconductor", "Electronics"],
        "type": ["twse", "twse"],
    })
    monkeypatch.setattr(ds, "fetch_finmind_cached", lambda *a, **k: raw.copy())
    out = ds.get_stock_info()
    assert {"stock_id", "stock_name", "industry_category", "market_type"}.issubset(out.columns)
    assert out.set_index("stock_id").loc["2330", "industry_category"] == "Semiconductor"


def test_capital_and_industry_market_cap(monkeypatch):
    info = pd.DataFrame({
        "stock_id": ["2330"], "stock_name": ["台積電"],
        "industry_category": ["Semiconductor"], "type": ["twse"],
    })
    # 財報股本（普通股，元）；收盤價 → 市值 = 股本/10 * 收盤
    fin = pd.DataFrame({
        "date": pd.to_datetime(["2023-12-31"]),
        "type": ["CommonStocksAndOrdinaryShares"],
        "value": [2_593_000_000_0.0],  # 任意股本
    })
    px = pd.DataFrame({"date": pd.to_datetime(["2024-01-02"]), "close": [600.0]})

    def fake_fetch(dataset, data_id, start, *a, **k):
        if dataset == "TaiwanStockInfo":
            return info.copy()
        if dataset == "TaiwanStockFinancialStatements":
            return fin.copy()
        if dataset == "TaiwanStockPrice":
            return px.copy()
        return pd.DataFrame()

    monkeypatch.setattr(ds, "fetch_finmind_cached", fake_fetch)
    out = ds.get_capital_and_industry("2330", as_of="2024-01-02")
    assert out["industry"] == "Semiconductor"
    assert out["shares_outstanding"] == 2_593_000_000_0.0
    assert out["market_cap"] == 2_593_000_000_0.0 / 10 * 600.0
