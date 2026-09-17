import pandas as pd
import pytest

from stock_strategies.chips_scanner import (
    calculate_streak,
    analyze_stock_chips,
    scan_watchlist_chips,
)
from stock_strategies.notify import format_chips_streak
from stock_strategies.sheet import write_chips_streak, CHIPS_STREAK_HEADERS


def test_calculate_streak_basic():
    assert calculate_streak([]) == 0
    assert calculate_streak(None) == 0
    assert calculate_streak([-100, -200]) == 0
    assert calculate_streak([-100, 200]) == 1
    assert calculate_streak([100, 200, 300]) == 3
    assert calculate_streak([-50, 100, 200, 300, 400]) == 4
    assert calculate_streak([100, 200, -10, 300, 400]) == 2


def test_calculate_streak_pandas_series():
    s = pd.Series([-100, 50, 150, 250])
    assert calculate_streak(s) == 3

    s_nan = pd.Series([100, float("nan"), 200])
    assert calculate_streak(s_nan) == 1


def test_analyze_stock_chips_success(monkeypatch):
    inst_data = pd.DataFrame([
        {"date": "2026-09-10", "foreign_net": 1000, "trust_net": -500, "dealer_net": 0, "total_net": 500},
        {"date": "2026-09-11", "foreign_net": 2000, "trust_net": 1000, "dealer_net": 100, "total_net": 3100},
        {"date": "2026-09-12", "foreign_net": 3000, "trust_net": 2000, "dealer_net": 200, "total_net": 5200},
        {"date": "2026-09-15", "foreign_net": 4000, "trust_net": 3000, "dealer_net": -500, "total_net": 6500},
    ])
    price_data = pd.DataFrame([
        {"date": "2026-09-10", "close": 100.0, "volume": 10000},
        {"date": "2026-09-11", "close": 102.0, "volume": 12000},
        {"date": "2026-09-12", "close": 105.0, "volume": 15000},
        {"date": "2026-09-15", "close": 110.0, "volume": 20000},
    ])

    monkeypatch.setattr(
        "stock_strategies.chips_scanner.get_institutional",
        lambda sid, start: inst_data,
    )
    monkeypatch.setattr(
        "stock_strategies.chips_scanner.get_price_history",
        lambda sid, years: price_data,
    )

    res = analyze_stock_chips("2330", "台積電")
    assert res is not None
    assert res["stock_id"] == "2330"
    assert res["name"] == "台積電"
    assert res["total_streak"] == 4
    assert res["foreign_streak"] == 4
    assert res["trust_streak"] == 3
    assert res["today_close"] == 110.0
    assert res["today_pct"] == round((110.0 / 105.0 - 1) * 100, 2)
    assert res["streak_pct"] == round((110.0 / 100.0 - 1) * 100, 2)
    assert "土洋同買" in res["tags"]
    assert len(res["recent_daily_pcts"]) == 4


def test_scan_watchlist_chips(monkeypatch):
    def fake_analyze(sid, name):
        if sid == "2330":
            return {
                "stock_id": "2330", "name": "台積電",
                "total_streak": 4, "foreign_streak": 4, "trust_streak": 3, "main_streak": 4,
                "streak_total_net_lots": 15000, "streak_pct": 10.0, "today_pct": 2.0,
                "today_close": 1000.0, "recent_daily_pcts": ["+2.0%"], "tags": ["土洋同買"],
            }
        elif sid == "2308":
            return {
                "stock_id": "2308", "name": "台達電",
                "total_streak": 1, "foreign_streak": 1, "trust_streak": 0, "main_streak": 1,
                "streak_total_net_lots": 500, "streak_pct": 1.0, "today_pct": 0.5,
                "today_close": 400.0, "recent_daily_pcts": ["+0.5%"], "tags": [],
            }
        return None

    monkeypatch.setattr("stock_strategies.chips_scanner.analyze_stock_chips", fake_analyze)

    watchlist = [
        {"stock_id": "2330", "name": "台積電"},
        {"stock_id": "2308", "name": "台達電"},
    ]
    res = scan_watchlist_chips(watchlist, min_streak=3, delay_sec=0)
    assert len(res) == 1
    assert res[0]["stock_id"] == "2330"


def test_format_chips_streak_empty():
    msg = format_chips_streak([])
    assert "今日無符合" in msg


def test_format_chips_streak_content():
    records = [{
        "stock_id": "2330",
        "name": "台積電",
        "main_streak": 5,
        "foreign_streak": 5,
        "trust_streak": 3,
        "total_streak": 5,
        "streak_total_net_lots": 25000,
        "today_close": 1020.0,
        "today_pct": 2.1,
        "streak_pct": 6.8,
        "recent_daily_pcts": ["+1.2%", "+0.5%", "+2.1%"],
        "tags": ["土洋同買"],
    }]
    msg = format_chips_streak(records)
    assert "台積電" in msg
    assert "2330" in msg
    assert "連買 *5* 天" in msg
    assert "土洋同買" in msg
    assert "+6.80%" in msg


def test_write_chips_streak(monkeypatch):
    class FakeWorksheet:
        def __init__(self):
            self.cleared = False
            self.header_row = None
            self.rows = []

        def clear(self):
            self.cleared = True

        def append_row(self, row):
            self.header_row = row

        def append_rows(self, rows):
            self.rows.extend(rows)

    fake_ws = FakeWorksheet()

    class FakeSheet:
        def worksheet(self, title):
            return fake_ws

    monkeypatch.setattr("stock_strategies.sheet.get_gsheet", lambda: FakeSheet())

    records = [{
        "date": "2026-09-17",
        "stock_id": "2330",
        "name": "台積電",
        "total_streak": 3,
        "foreign_streak": 3,
        "trust_streak": 3,
        "streak_total_net_lots": 10000,
        "today_total_net_lots": 3000,
        "today_close": 1000.0,
        "today_pct": 1.5,
        "streak_pct": 4.5,
        "recent_daily_pcts": ["+1.0%", "+2.0%", "+1.5%"],
        "tags": ["土洋同買"],
    }]

    write_chips_streak(records)
    assert fake_ws.cleared is True
    assert fake_ws.header_row == CHIPS_STREAK_HEADERS
    assert len(fake_ws.rows) == 1
    assert fake_ws.rows[0][1] == "2330"
    assert fake_ws.rows[0][9] == "+1.50%"
