import pandas as pd
import pytest

from stock_strategies.chips_scanner import (
    calculate_streak,
    enrich_candidate_price,
    scan_market_chips_streak,
)
from stock_strategies.exchange_data import _parse_int
from stock_strategies.notify import format_chips_streak
from stock_strategies.sheet import (
    write_chips_streak,
    write_chips_sell_streak,
    CHIPS_STREAK_HEADERS,
)


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
        "buy_candidates": [
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
        "sell_candidates": [
            {
                "stock_id": "2454",
                "name": "聯發科",
                "total_streak": 3,
                "foreign_streak": 3,
                "trust_streak": 3,
                "main_streak": 3,
                "streak_total_net_lots": -5000,
                "today_total_net_lots": -1500,
                "tags": ["土洋同賣"],
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

    # 驗證 buy_records
    assert len(res["buy_records"]) == 2
    assert res["buy_records"][0]["stock_id"] == "2330"
    assert res["buy_records"][0]["is_watchlist"] is True
    assert res["buy_records"][1]["stock_id"] == "3231"
    assert res["buy_records"][1]["is_watchlist"] is False

    # 驗證 sell_records
    assert len(res["sell_records"]) == 1
    assert res["sell_records"][0]["stock_id"] == "2454"
    assert res["sell_records"][0]["is_watchlist"] is False

    # 向後相容 list 迭代行為
    assert len(res) == 2
    assert res[0]["stock_id"] == "2330"


def test_format_chips_streak_content():
    scan_result = {
        "buy_records": [
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
        ],
        "sell_records": [
            {
                "stock_id": "2454",
                "name": "聯發科",
                "is_watchlist": True,
                "main_streak": 3,
                "foreign_streak": 3,
                "trust_streak": 0,
                "total_streak": 3,
                "streak_total_net_lots": -6000,
                "today_close": 1400.0,
                "today_pct": -1.8,
                "streak_pct": -4.2,
                "recent_daily_pcts": ["-1.0%", "-1.4%", "-1.8%"],
                "tags": ["外資提款"],
            },
            {
                "stock_id": "2603",
                "name": "長榮",
                "is_watchlist": False,
                "main_streak": 4,
                "foreign_streak": 4,
                "trust_streak": 4,
                "total_streak": 4,
                "streak_total_net_lots": -12000,
                "today_close": 180.0,
                "today_pct": -2.5,
                "streak_pct": -6.0,
                "recent_daily_pcts": ["-2.5%"],
                "tags": ["土洋同賣"],
            },
        ],
    }
    msg = format_chips_streak(scan_result)
    assert "自選股連買追蹤" in msg
    assert "全市場連買精選" in msg
    assert "台積電" in msg
    assert "緯創" in msg
    assert "土洋同買" in msg

    assert "【空方 — 法人連賣避險警示】" in msg
    assert "自選股連賣警戒" in msg
    assert "全市場連賣提款榜" in msg
    assert "聯發科" in msg
    assert "長榮" in msg
    assert "外資提款" in msg


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
        "trust_ratio": 0.35,
        "streak_total_net_lots": 10000,
        "today_total_net_lots": 3000,
        "today_close": 1000.0,
        "today_pct": 1.5,
        "streak_pct": 4.5,
        "recent_daily_pcts": ["+1.0%", "+2.0%", "+1.5%"],
        "tags": ["土洋同買", "投信認養"],
    }]

    write_chips_streak(records)
    assert fake_ws.cleared is True
    assert "is_watchlist" in CHIPS_STREAK_HEADERS
    assert "trust_ratio" in CHIPS_STREAK_HEADERS
    assert fake_ws.header_row == CHIPS_STREAK_HEADERS
    assert len(fake_ws.rows) == 1
    assert fake_ws.rows[0][1] == "2330"
    assert fake_ws.rows[0][3] == "⭐ 是"
    assert fake_ws.rows[0][7] == "+0.35%"


def test_write_chips_sell_streak(monkeypatch):
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
        "stock_id": "2454",
        "name": "聯發科",
        "is_watchlist": True,
        "total_streak": 3,
        "foreign_streak": 3,
        "trust_streak": 0,
        "trust_ratio": -0.22,
        "streak_total_net_lots": -5000,
        "today_total_net_lots": -1500,
        "today_close": 1400.0,
        "today_pct": -1.5,
        "streak_pct": -4.5,
        "recent_daily_pcts": ["-1.0%", "-2.0%", "-1.5%"],
        "tags": ["外資提款", "投信結帳"],
    }]

    write_chips_sell_streak(records)
    assert fake_ws.cleared is True
    assert fake_ws.header_row == CHIPS_STREAK_HEADERS
    assert len(fake_ws.rows) == 1
    assert fake_ws.rows[0][1] == "2454"
    assert fake_ws.rows[0][3] == "⭐ 是"
    assert fake_ws.rows[0][7] == "-0.22%"


def test_format_messages_lean():
    from stock_strategies.notify import format_messages

    signals = [
        {
            "stock_id": "2330",
            "name": "台積電",
            "action": "BUY",
            "signal_score": 78.5,
            "entry_price": 1000.0,
            "stop_loss_price": 920.0,
            "target_price": 1100.0,
            "components": {
                "tech_score": 85,
                "tech_signals": ["均線多頭", "MACD多頭"],
                "volume_patterns": ["倍量柱"],
                "chips_summary": "土洋同買",
            },
            "trend": {"chg_5d": 3.2},
            "risk_notes": [],
        },
        {
            "stock_id": "2454",
            "name": "聯發科",
            "action": "WATCH",
            "signal_score": 62.0,
            "entry_price": 1400.0,
            "stop_loss_price": 1288.0,
            "target_price": 1540.0,
            "components": {
                "tech_score": 60,
                "tech_signals": ["KD黃金交叉"],
                "volume_patterns": [],
            },
            "trend": {"chg_5d": -0.5},
            "risk_notes": [],
        },
    ]

    messages = format_messages(signals)
    assert len(messages) == 1  # 瘦身後濃縮為 1 則訊息
    msg = messages[0]
    assert "V3.2 每日選股決策晚報" in msg
    assert "【BUY — 建議進場】" in msg
    assert "台積電" in msg
    assert "建議進場 ≤ 1025.0 (開高逾+2.5%不追)" in msg  # 防追高上限指引
    assert "【WATCH — 接近訊號精選】" in msg
    assert "聯發科" in msg
    assert "策略規則" not in msg  # 重複冗贅規則已移除
    assert "量價字典速查" not in msg  # 重複量價字典已移除


def test_streak_amount_and_sorting(monkeypatch):
    from stock_strategies.chips_scanner import enrich_candidate_price, _process_pool
    import pandas as pd

    # 模擬 2330 台積電 (收盤 1000, 買超 10000 張 -> 100.0億)
    # 模擬 1101 台泥 (收盤 30, 買超 20000 張 -> 6.0億)
    def fake_get_price(sid, years=1):
        p = 1000.0 if sid == "2330" else 30.0
        return pd.DataFrame([
            {"date": "2026-09-16", "close": p * 0.98},
            {"date": "2026-09-17", "close": p},
        ])

    monkeypatch.setattr("stock_strategies.chips_scanner.get_price_history", fake_get_price)

    c1 = {"stock_id": "2330", "main_streak": 3, "streak_total_net_lots": 10000}
    c2 = {"stock_id": "1101", "main_streak": 3, "streak_total_net_lots": 20000}

    e1 = enrich_candidate_price(c1)
    e2 = enrich_candidate_price(c2)
    assert e1["streak_amount"] == 100.0  # 10,000張 * 1,000元 = 100億
    assert e2["streak_amount"] == 6.0   # 20,000張 * 30元 = 6億

    # 驗證 _process_pool：同樣連買 3 天，金額高者 (2330) 優先於張數高但金額低者 (1101)
    pool = _process_pool([c2, c1], watchlist_sids=set(), watchlist_names={}, latest_date="2026-09-17", max_market_candidates=10)
    assert pool[0]["stock_id"] == "2330"
    assert pool[1]["stock_id"] == "1101"


def test_format_premarket_top3():
    from stock_strategies.notify import format_premarket

    night = {
        "date": "2026-09-17",
        "close": 21000.0,
        "spread": 50.0,
        "pct": 0.24,
        "volume": 12000,
        "label": "平盤",
        "emoji": "⚪",
        "bias": "flat",
        "direction": "中性盤整",
    }
    signals = [
        {"date": "2026-09-17", "action": "BUY", "stock_id": "2330", "name": "台積電", "signal_score": 85},
        {"date": "2026-09-17", "action": "WATCH", "stock_id": "2454", "name": "聯發科", "signal_score": 75},
        {"date": "2026-09-17", "action": "WATCH", "stock_id": "2308", "name": "台達電", "signal_score": 70},
        {"date": "2026-09-17", "action": "WATCH", "stock_id": "2382", "name": "廣達", "signal_score": 68},
        {"date": "2026-09-17", "action": "WATCH", "stock_id": "3231", "name": "緯創", "signal_score": 65},
        {"date": "2026-09-17", "action": "WATCH", "stock_id": "6669", "name": "緯穎", "signal_score": 60},
    ]

    msg = format_premarket(night, signals)
    assert "BUY 2330 台積電" in msg
    assert "WATCH 2454 聯發科" in msg
    assert "WATCH 2308 台達電" in msg
    assert "WATCH 2382 廣達" in msg
    # 第 4、5 檔 WATCH 不應佔用主要條目，而應被收納在「其餘觀察」
    assert "WATCH 3231 緯創" not in msg
    assert "其餘觀察: 3231緯創, 6669緯穎" in msg


def test_format_chips_streak_cross_confirmation():
    from stock_strategies.notify import format_chips_streak

    buy_records = [
        {
            "stock_id": "2330",
            "name": "台積電",
            "is_watchlist": True,
            "main_streak": 4,
            "foreign_streak": 4,
            "trust_streak": 3,
            "total_streak": 4,
            "streak_total_net_lots": 15000,
            "streak_amount": 150.0,
            "today_close": 1000.0,
            "today_pct": 1.5,
            "streak_pct": 5.0,
            "recent_daily_pcts": ["+1.5%"],
            "tags": ["土洋同買"],
        }
    ]
    sell_records = [
        {
            "stock_id": "2454",
            "name": "聯發科",
            "is_watchlist": True,
            "main_streak": 3,
            "foreign_streak": 3,
            "trust_streak": 0,
            "total_streak": 3,
            "streak_total_net_lots": -2000,
            "streak_amount": -28.0,
            "today_close": 1400.0,
            "today_pct": -2.0,
            "streak_pct": -4.0,
            "recent_daily_pcts": ["-2.0%"],
            "tags": ["外資提款"],
        }
    ]

    # 今日 14:35 系統選出 2330 與 2454
    daily_buys = [
        {"stock_id": "2330", "name": "台積電"},
        {"stock_id": "2454", "name": "聯發科"},
    ]

    msg = format_chips_streak(buy_records, sell_records, daily_buys=daily_buys)
    assert "🎯 *【今日選股 × 籌碼覆核】*" in msg
    assert "🔥 *雙重保證* *2330 台積電*" in msg
    assert "(+150.0億)" in msg
    assert "🚨 *籌碼背離* *2454 聯發科*" in msg
    assert "(-28.0億)" in msg
