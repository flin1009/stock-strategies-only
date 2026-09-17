import pandas as pd
from stock_strategies.context import FactorContext


def _mk():
    px = pd.DataFrame({"date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
                       "close": [10.0, 11.0]})
    sh = pd.DataFrame({"date": pd.to_datetime(["2024-01-01", "2024-01-08"]),
                       "foreign_ratio": [40.0, 41.0]})
    return FactorContext(
        stock_id="2330", as_of=pd.Timestamp("2024-01-05"),
        price_df=px, index_df=pd.DataFrame(), inst=pd.DataFrame(),
        revenue=pd.DataFrame(), valuation=pd.DataFrame(), margin=pd.DataFrame(),
        shareholding=sh, fundamentals={"eps": {}, "roe": {}},
        industry="Semiconductor", shares_outstanding=None, market_cap=None,
    )


def test_latest_price_returns_last_row():
    ctx = _mk()
    assert ctx.latest_price()["close"] == 11.0


def test_asof_row_picks_last_before_asof():
    ctx = _mk()
    row = ctx.asof_row("shareholding")
    assert row is not None
    # shareholding 有 2024-01-01 與 2024-01-08，as_of=2024-01-05
    # → date<=as_of 的最後一筆是 2024-01-01（foreign_ratio=40.0）
    assert row["foreign_ratio"] == 40.0


def test_asof_row_revenue_uses_avail_date():
    """revenue 的時間欄是 avail_date，asof_row 需用 date_col 指定（review issue #4）。"""
    import pandas as pd
    from stock_strategies.context import FactorContext
    rev = pd.DataFrame({
        "avail_date": pd.to_datetime(["2024-02-10", "2024-03-10"]),
        "revenue": [100, 120],
    })
    ctx = FactorContext(
        stock_id="2330", as_of=pd.Timestamp("2024-02-20"),
        price_df=pd.DataFrame(), index_df=pd.DataFrame(), inst=pd.DataFrame(),
        revenue=rev, valuation=pd.DataFrame(), margin=pd.DataFrame(),
        shareholding=pd.DataFrame(), fundamentals={"eps": {}, "roe": {}},
    )
    assert ctx.asof_row("revenue") is None              # 預設 date_col=date → revenue 無 date 欄
    row = ctx.asof_row("revenue", "avail_date")
    assert row is not None and row["revenue"] == 100    # <=as_of 的最後一筆是 2024-02-10
