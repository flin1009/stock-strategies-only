import pandas as pd
from stock_strategies import market


def test_market_state_bullish(monkeypatch):
    # 造一段站上 20 日線的指數
    n = 40
    dates = pd.bdate_range("2024-01-01", periods=n)
    close = [17000 + i * 10 for i in range(n)]  # 持續上升 → 站上月線
    idx = pd.DataFrame({"date": dates, "open": close, "high": close,
                        "low": close, "close": close})
    monkeypatch.setattr(market, "get_index_history", lambda *a, **k: idx.copy())
    state = market.get_market_state(ma_period=20)
    assert state["bullish"] is True
    assert state["close"] == close[-1]


def test_market_state_handles_empty(monkeypatch):
    monkeypatch.setattr(market, "get_index_history", lambda *a, **k: pd.DataFrame())
    state = market.get_market_state()
    assert state["bullish"] is True   # 資料不足 → 不套濾鏡（沿用原行為）
