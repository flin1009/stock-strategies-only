import pandas as pd
from stock_strategies import datasources as ds


def _raw():
    return pd.DataFrame({
        "date": pd.to_datetime(["2024-02-01", "2024-03-01"]),  # 所屬月
        "revenue_year": [2024, 2024],
        "revenue_month": [2, 3],
        "revenue": [100_000, 120_000],
    })


def test_revenue_avail_date_blocks_lookahead(monkeypatch):
    monkeypatch.setattr(ds, "fetch_finmind_cached", lambda *a, **k: _raw())
    # 3 月營收 avail_date = 2024-04-10；as_of=2024-04-05 不應看到 3 月
    out = ds.get_month_revenue("2330", "2024-01-01", as_of="2024-04-05")
    assert out["revenue_month"].max() == 2  # 只到 2 月（avail=2024-03-10）
    # as_of=2024-04-10 才看得到 3 月
    out2 = ds.get_month_revenue("2330", "2024-01-01", as_of="2024-04-10")
    assert 3 in out2["revenue_month"].values


def test_revenue_mom_yoy_columns(monkeypatch):
    monkeypatch.setattr(ds, "fetch_finmind_cached", lambda *a, **k: _raw())
    out = ds.get_month_revenue("2330", "2024-01-01")
    assert {"avail_date", "mom", "yoy", "revenue"}.issubset(out.columns)


def test_revenue_yoy_aligns_by_period(monkeypatch):
    """YoY 對齊去年同月，月份有缺口也不錯位（review issue #7）。"""
    raw = pd.DataFrame({
        "date": pd.to_datetime(["2023-01-01", "2023-02-01", "2024-01-01"]),
        "revenue_year": [2023, 2023, 2024],
        "revenue_month": [1, 2, 1],
        "revenue": [100, 200, 150],
    })
    monkeypatch.setattr(ds, "fetch_finmind_cached", lambda *a, **k: raw.copy())
    out = ds.get_month_revenue("2330", "2023-01-01")
    row = out[(out["revenue_year"] == 2024) & (out["revenue_month"] == 1)].iloc[0]
    assert abs(row["yoy"] - 0.5) < 1e-9   # 2024-01 對 2023-01：150/100-1，非對位置上一筆 200
