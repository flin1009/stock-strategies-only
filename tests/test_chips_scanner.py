import pandas as pd
import pytest

from stock_strategies.chips_scanner import (
    calculate_streak,
    enrich_candidate_price,
    scan_market_chips_streak,
)
from stock_strategies.exchange_data import _parse_int
from stock_strategies.notify import format_chips_streak
from stock_strategies.sheet import write_chips_streak, CHIPS_STREAK_HEADERS


def test_parse_int():
    assert _parse_int("1,234,567") == 1234567
    assert _parse_int("-5,000") == -5000
    assert _parse_int(None) == 0
    assert _parse_int("abc") == 0


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


def test_enrich_candidate_price(monkeypatch):
    candidate = {
        "stock_id": "2330",
        "name": "台積電",
        "main_streak": 3,
    }
    price_data = pd.DataFrame([
        {"date": "2026-09-10", "close": 100.0, "volume": 10000},
        {"date": "2026-09-11", "close": 102.0, "volume": 12000},
        {"date": "2026-09-12", "close": 105.0, "volume": 15000},
        {"date": "2026-09-15", "close": 110.0, "volume": 20000},
    ])
    monkeypatch.setattr(
        "stock_strategies.chips_scanner.get_price_history",
        lambda sid, years: price_data,
    )

    enriched = enrich_candidate_price(candidate)
    assert enriched["today_close"] == 110.0
    assert enriched["today_pct"] == round((110.0 / 105.0 - 1) * 100, 2)
    assert enriched["streak_pct"] == round((110.0 / 100.0 - 1) * 100, 2)
    assert len(enriched["recent_daily_pcts"]) == 3


def test_scan_market_chips_streak(monkeypatch):
    fake_market_res = {
        "trading_days": ["2026-09-12", "2026-09-15", "2026-09-16"],
        "candidates": [
            {
                "stock_id": "2330",
                "name": "台積電",
                "total_streak": 3,
                "foreign_streak": 3,
                "trust_streak": 3,
                "main_streak": 3,
                "streak_total_net_lots": 15000,
                "today_total_net_lots": 5000,
                "tags": ["土洋同買"],
            },
            {
                "stock_id": "3231",
                "name": "緯創",
                "total_streak": 4,
                "foreign_streak": 4,
                "trust_streak": 0,
                "main_streak": 4,
                "streak_total_net_lots": 20000,
                "today_total_net_lots": 8000,
                "tags": ["外資買進"],
            },
        ],
    }

    monkeypatch.setattr(
        "stock_strategies.chips_scanner.get_market_streak_candidates",
        lambda min_streak, trading_days_count: fake_market_res,
    )
    monkeypatch.setattr(
        "stock_strategies.chips_scanner.enrich_candidate_price",
        lambda c: {**c, "today_close": 100.0, "today_pct": 1.0, "streak_pct": 3.0, "recent_daily_pcts": ["+1.0%"]},
    )

    watchlist = [{"stock_id": "2330", "name": "台積電"}]
    res = scan_market_chips_streak(watchlist=watchlist, min_streak=3, delay_sec=0)

    assert len(res) == 2
    # 自選股應優先置頂
    assert res[0]["stock_id"] == "2330"
    assert res[0]["is_watchlist"] is True
    assert res[1]["stock_id"] == "3231"
    assert res[1]["is_watchlist"] is False


def test_format_chips_streak_content():
    records = [
        {
            "stock_id": "2330",
            "name": "台積電",
            "is_watchlist": True,
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
        },
        {
            "stock_id": "3231",
            "name": "緯創",
            "is_watchlist": False,
            "main_streak": 4,
            "foreign_streak": 4,
            "trust_streak": 0,
            "total_streak": 4,
            "streak_total_net_lots": 18000,
            "today_close": 120.0,
            "today_pct": 3.5,
            "streak_pct": 8.5,
            "recent_daily_pcts": ["+3.5%"],
            "tags": ["外資買進"],
        },
    ]
    msg = format_chips_streak(records)
    assert "自選股連買追蹤" in msg
    assert "全市場法人連買精選" in msg
    assert "台積電" in msg
    assert "緯創" in msg
    assert "土洋同買" in msg


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
        "is_watchlist": True,
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
    assert "is_watchlist" in CHIPS_STREAK_HEADERS
    assert fake_ws.header_row == CHIPS_STREAK_HEADERS
    assert len(fake_ws.rows) == 1
    assert fake_ws.rows[0][1] == "2330"
    assert fake_ws.rows[0][3] == "⭐ 是"
