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
    monkeypatch.setattr(
        "stock_strategies.chips_scanner.get_market_margin_map",
        lambda: {"stocks": {}, "market_total_margin_diff_lots": 0},
    )
    monkeypatch.setattr(
        "stock_strategies.chips_scanner.get_market_daily_price_map",
        lambda: {},
    )
    monkeypatch.setattr(
        "stock_strategies.chips_scanner.get_market_recent_prices_map",
        lambda days: {},
    )
    monkeypatch.setattr(
        "stock_strategies.chips_scanner.get_index_history",
        lambda *a, **kw: pd.DataFrame(),
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


def test_get_market_valuation_map(monkeypatch, tmp_path):
    from stock_strategies import exchange_data
    monkeypatch.setattr(exchange_data, "EXCHANGE_CACHE_DIR", tmp_path)

    twse_json = [
        {"Code": "2330", "PEratio": "18.5", "DividendYield": "3.20", "PBratio": "4.50"},
        {"Code": "2317", "PEratio": "-", "DividendYield": "4.50", "PBratio": "1.20"},
    ]
    tpex_json = [
        {"SecuritiesCompanyCode": "8069", "PriceEarningRatio": "22.0", "YieldRatio": "2.80", "PriceBookRatio": "3.10"}
    ]

    class FakeResponse:
        def __init__(self, data):
            self._data = data
            self.ok = True
        def json(self):
            return self._data

    def fake_get(url, *args, **kwargs):
        if "BWIBBU_ALL" in url:
            return FakeResponse(twse_json)
        return FakeResponse(tpex_json)

    monkeypatch.setattr("requests.get", fake_get)

    vmap = exchange_data.get_market_valuation_map(force_refresh=True)
    assert "2330" in vmap
    assert vmap["2330"]["pe"] == 18.5
    assert vmap["2330"]["yield"] == 3.2
    assert vmap["2330"]["pb"] == 4.5
    assert vmap["2317"]["pe"] is None
    assert vmap["2317"]["yield"] == 4.5
    assert vmap["8069"]["pe"] == 22.0


def test_get_market_margin_map(monkeypatch, tmp_path):
    from stock_strategies import exchange_data
    monkeypatch.setattr(exchange_data, "EXCHANGE_CACHE_DIR", tmp_path)

    twse_margin = [
        {"股票代號": "2330", "融資今日餘額": "10,000", "融資前日餘額": "10,500", "融券今日餘額": "500", "融券前日餘額": "400"},
    ]
    tpex_margin = [
        {"SecuritiesCompanyCode": "8069", "MarginPurchaseBalance": "3,000", "MarginPurchaseBalancePreviousDay": "2,800", "ShortSaleBalance": "100", "ShortSaleBalancePreviousDay": "100"}
    ]

    class FakeResponse:
        def __init__(self, data):
            self._data = data
            self.ok = True
        def json(self):
            return self._data

    def fake_get(url, *args, **kwargs):
        if "MI_MARGN" in url:
            return FakeResponse(twse_margin)
        return FakeResponse(tpex_margin)

    monkeypatch.setattr("requests.get", fake_get)

    mmap = exchange_data.get_market_margin_map(force_refresh=True)
    stocks = mmap["stocks"]
    assert "2330" in stocks
    assert stocks["2330"]["margin_diff"] == -500
    assert stocks["8069"]["margin_diff"] == 200
    assert mmap["market_total_margin_diff_lots"] == -300


def test_format_messages_with_valuation():
    from stock_strategies.notify import format_messages

    signals = [
        {
            "stock_id": "2330",
            "name": "台積電",
            "action": "BUY",
            "signal_score": 85.0,
            "entry_price": 1000.0,
            "stop_loss_price": 920.0,
            "target_price": 1100.0,
            "components": {
                "tech_score": 85,
                "tech_signals": ["均線多頭"],
                "valuation": {"pe": 18.2, "yield": 3.5, "pb": 4.5},
            },
            "trend": {"chg_5d": 3.0},
        },
        {
            "stock_id": "2454",
            "name": "聯發科",
            "action": "WATCH",
            "signal_score": 65.0,
            "entry_price": 1400.0,
            "stop_loss_price": 1288.0,
            "components": {
                "tech_score": 65,
                "valuation": {"pe": None, "yield": 0.0, "pb": None},
            },
            "trend": {"chg_5d": -1.0},
        }
    ]
    msgs = format_messages(signals)
    assert len(msgs) == 1
    assert "• *2330 台積電* (綜合 85.0分 | PE 18.2x · 殖 3.5%)" in msgs[0]
    assert "• *2454 聯發科* (綜合 65.0分 | PE -- · 殖 0%)" in msgs[0]


def test_format_chips_streak_with_margin():
    from stock_strategies.notify import format_chips_streak

    buy_records = [
        {
            "stock_id": "2330",
            "name": "台積電",
            "is_watchlist": True,
            "main_streak": 3,
            "foreign_streak": 3,
            "trust_streak": 0,
            "streak_total_net_lots": 5000,
            "streak_amount": 50.0,
            "margin_diff": -650,
            "today_close": 1000.0,
            "today_pct": 1.0,
            "streak_pct": 3.0,
        },
        {
            "stock_id": "2603",
            "name": "長榮",
            "is_watchlist": False,
            "main_streak": 3,
            "foreign_streak": 3,
            "trust_streak": 0,
            "streak_total_net_lots": 3000,
            "streak_amount": 6.0,
            "margin_diff": 450,
            "today_close": 200.0,
            "today_pct": 2.0,
            "streak_pct": 5.0,
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
            "streak_total_net_lots": -2000,
            "streak_amount": -28.0,
            "margin_diff": 350,
            "today_close": 1400.0,
            "today_pct": -1.5,
            "streak_pct": -3.5,
        }
    ]

    scan_res = {
        "buy_records": buy_records,
        "sell_records": sell_records,
        "market_total_margin_diff_lots": -12500,
    }

    msg = format_chips_streak(scan_res)
    assert "融資 -12,500張 (浮額沉澱)" in msg
    assert "融資-650張🔥" in msg
    assert "融資+450張⚠️" in msg
    assert "融資+350張⚠️" in msg


def test_append_signals_valuation(monkeypatch):
    from stock_strategies.sheet import append_signals

    class FakeWorksheet:
        def __init__(self):
            self.header = None
            self.rows = []
        def append_row(self, row):
            self.header = row
        def append_rows(self, rows):
            self.rows.extend(rows)

    fake_ws = FakeWorksheet()
    class FakeSheet:
        def worksheet(self, title):
            return fake_ws

    monkeypatch.setattr("stock_strategies.sheet.get_gsheet", lambda: FakeSheet())

    signals = [{
        "date": "2026-09-24",
        "stock_id": "2330",
        "name": "台積電",
        "action": "BUY",
        "signal_score": 85.0,
        "entry_price": 1000.0,
        "stop_loss_price": 920.0,
        "target_price": 1100.0,
        "risk_reward_ratio": 1.25,
        "position_size_pct": 10.0,
        "components": {
            "backtest_winrate": 0.75,
            "backtest_samples": 40,
            "tech_signals": ["均線多頭"],
            "valuation": {"pe": 18.2, "yield": 3.5, "pb": 4.5},
        },
        "risk_notes": ["量能健康"],
    }]

    append_signals(signals)
    assert len(fake_ws.rows) == 1
    row = fake_ws.rows[0]
    assert row[1] == "2330"
    assert row[14] == "18.2"
    assert row[15] == "3.50%"
    assert row[16] == "4.50"


def test_read_watchlist_preserves_leading_zeros(monkeypatch):
    from stock_strategies.sheet import read_watchlist

    class FakeWorksheet:
        def __init__(self):
            self.seen_ignore = None

        def get_all_records(self, numericise_ignore=None):
            self.seen_ignore = numericise_ignore
            if numericise_ignore and 1 in numericise_ignore:
                return [
                    {"stock_id": "0050", "name": "元大台灣50", "enabled": "TRUE"},
                    {"stock_id": "3227", "name": "原相", "enabled": "TRUE"},
                    {"stock_id": "2330", "name": "台積電", "enabled": "FALSE"},
                ]
            return [
                {"stock_id": 50, "name": "元大台灣50", "enabled": "TRUE"},
            ]

    fake_ws = FakeWorksheet()

    class FakeSheet:
        def worksheet(self, title):
            return fake_ws

    monkeypatch.setattr("stock_strategies.sheet.get_gsheet", lambda: FakeSheet())
    res = read_watchlist()
    assert fake_ws.seen_ignore == [1]
    assert [r["stock_id"] for r in res] == ["0050", "3227"]
    assert all(isinstance(r["stock_id"], str) for r in res)


def test_send_telegram_splits_long_message(monkeypatch):
    from stock_strategies.notify import send_telegram

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123456")

    sent_chunks = []

    class FakeResp:
        ok = True
        status_code = 200
        text = "ok"

    def fake_post(url, json=None, timeout=10):
        sent_chunks.append(json["text"])
        return FakeResp()

    monkeypatch.setattr("stock_strategies.notify.requests.post", fake_post)

    long_msg = "\n".join([f"第 {i} 行測試訊息內容 ABCDEFGHIJ" for i in range(300)])
    assert len(long_msg) > 4096
    send_telegram(long_msg)
    assert len(sent_chunks) >= 2
    assert all(len(c) <= 3950 for c in sent_chunks)
    assert "\n".join(sent_chunks) == long_msg


def test_format_chips_streak_collapses_long_watchlist():
    from stock_strategies.notify import format_chips_streak

    wl_buys = [
        {
            "stock_id": f"23{i:02d}",
            "name": f"股{i}",
            "is_watchlist": True,
            "main_streak": 4,
            "foreign_streak": 4,
            "trust_streak": 0,
            "streak_total_net_lots": 1000,
            "streak_amount": float(10 - i),
            "today_close": 100.0,
            "today_pct": 1.0,
            "streak_pct": 3.0,
        }
        for i in range(8)
    ]
    msg = format_chips_streak({"buy_records": wl_buys, "sell_records": []})
    assert "其餘自選連買 (3 檔)" in msg
    assert "2305股5(連4 +5.0億)" in msg


def test_format_messages_splits_when_many_buys():
    from stock_strategies.notify import format_messages

    signals = [
        {
            "stock_id": f"23{i:02d}",
            "name": f"測試股{i}",
            "action": "BUY",
            "signal_score": 85.0,
            "entry_price": 100.0,
            "stop_loss_price": 92.0,
            "target_price": 115.0,
            "components": {
                "tech_score": 85,
                "tech_signals": ["均線多頭", "MACD多頭", "VCP突破"],
                "volume_patterns": ["倍量柱"],
                "chips_summary": "土洋同買(投信連3/外資連3)",
                "valuation": {"pe": 15.0, "yield": 4.0, "pb": 2.0},
            },
            "trend": {"chg_5d": 3.5},
            "risk_notes": ["量能放大注意追高風險測試文字"],
        }
        for i in range(25)
    ]
    msgs = format_messages(signals)
    assert len(msgs) >= 2
    assert all(len(m) <= 3950 for m in msgs)
    # 確認不會把同一檔股票的卡片從中間切斷
    for m in msgs:
        assert m.count("• *23") == m.count("🎯 建議進場")


def test_get_market_daily_price_map(monkeypatch, tmp_path):
    from stock_strategies import exchange_data
    monkeypatch.setattr(exchange_data, "EXCHANGE_CACHE_DIR", tmp_path)

    twse_quotes = [{"Code": "2330", "ClosingPrice": "102.0", "Change": "2.0"}]
    tpex_quotes = [{"SecuritiesCompanyCode": "8069", "Close": "210.0", "Change": "+10.0"}]

    class FakeResp:
        def __init__(self, data):
            self._data = data
            self.ok = True
        def json(self):
            return self._data

    def fake_get(url, *args, **kwargs):
        if "STOCK_DAY_ALL" in url:
            return FakeResp(twse_quotes)
        return FakeResp(tpex_quotes)

    monkeypatch.setattr("requests.get", fake_get)
    pmap = exchange_data.get_market_daily_price_map(force_refresh=True)
    assert pmap["2330"] == {"close": 102.0, "pct": 2.0}
    assert pmap["8069"] == {"close": 210.0, "pct": 5.0}


def test_chips_scanner_finmind_rate_limit_fallback(monkeypatch):
    from stock_strategies.cache import FinMindRateLimitError
    from stock_strategies.chips_scanner import enrich_candidate_price

    calls = {"n": 0}

    def boom(sid, years=1):
        calls["n"] += 1
        raise FinMindRateLimitError("402")

    monkeypatch.setattr("stock_strategies.chips_scanner.get_price_history", boom)

    finmind_state = {"rate_limited": False}
    daily_map = {
        "2330": {"close": 1000.0, "pct": 2.5},
        "2454": {"close": 1400.0, "pct": -1.0},
    }
    margin_stocks = {"2330": {"margin_diff": -100, "margin_today": 5000}}

    c1 = {"stock_id": "2330", "main_streak": 3, "streak_total_net_lots": 2000}
    c2 = {"stock_id": "2454", "main_streak": 3, "streak_total_net_lots": 1000}

    r1 = enrich_candidate_price(
        c1, margin_stocks=margin_stocks, daily_price_map=daily_map, finmind_state=finmind_state
    )
    r2 = enrich_candidate_price(
        c2, margin_stocks=margin_stocks, daily_price_map=daily_map, finmind_state=finmind_state
    )

    assert finmind_state["rate_limited"] is True
    assert calls["n"] == 1  # 第 2 檔不再打 FinMind（熔斷直接走快照）
    assert r1["today_close"] == 1000.0
    assert r1["today_pct"] == 2.5
    assert r1["streak_amount"] == 20.0
    assert r1["margin_diff"] == -100
    assert r2["today_close"] == 1400.0
    assert r2["streak_amount"] == 14.0


def test_twse_tpex_recent_prices_primary_mode(monkeypatch, tmp_path):
    from stock_strategies import exchange_data
    from stock_strategies.chips_scanner import enrich_candidate_price

    monkeypatch.setattr(exchange_data, "EXCHANGE_CACHE_DIR", tmp_path)

    twse_payload = {
        "stat": "OK",
        "tables": [{
            "title": "115年11月13日 每日收盤行情(全部)",
            "data": [
                ["2330", "台積電", "0", "0", "0", "99", "105", "99", "105.00", "<p style= color:red>+</p>", "5.00"],
                ["2002", "中鋼", "0", "0", "0", "20", "20", "19", "19.60", "<p style= color:green>-</p>", "0.40"],
            ],
        }],
    }
    tpex_payload = {
        "tables": [{
            "data": [
                ["3105", "穩懋", "110.00", "+10.00"],
            ],
        }],
    }

    class FakeResp:
        def __init__(self, data):
            self._data = data
            self.ok = True
        def json(self):
            return self._data

    def fake_get(url, *args, **kwargs):
        if "MI_INDEX" in url:
            return FakeResp(twse_payload)
        return FakeResp(tpex_payload)

    monkeypatch.setattr("requests.get", fake_get)

    recent_map = exchange_data.get_market_recent_prices_map(["2026-11-13"])
    assert recent_map["2330"][0]["close"] == 105.0
    assert recent_map["2330"][0]["prev_close"] == 100.0
    assert recent_map["2330"][0]["pct"] == 5.0
    assert recent_map["2002"][0]["pct"] == -2.0
    assert recent_map["3105"][0]["pct"] == 10.0

    # 驗證主資料源模式：FinMind 被呼叫 0 次，仍完整算出收盤、漲跌幅、期間累計與金額
    finmind_calls = {"n": 0}
    monkeypatch.setattr(
        "stock_strategies.chips_scanner.get_price_history",
        lambda *a, **kw: finmind_calls.__setitem__("n", finmind_calls["n"] + 1),
    )

    multi_recent = {
        "2330": [
            {"date": "2026-11-11", "close": 102.0, "prev_close": 100.0, "pct": 2.0},
            {"date": "2026-11-12", "close": 105.0, "prev_close": 102.0, "pct": 2.94},
            {"date": "2026-11-13", "close": 110.0, "prev_close": 105.0, "pct": 4.76},
        ]
    }
    cand = {"stock_id": "2330", "main_streak": 3, "streak_total_net_lots": 1000}
    res = enrich_candidate_price(
        cand,
        check_vcp=False,
        margin_stocks={"2330": {"margin_diff": -50, "margin_today": 1000}},
        recent_prices_map=multi_recent,
        use_finmind=False,
    )
    assert finmind_calls["n"] == 0
    assert res["today_close"] == 110.0
    assert res["today_pct"] == 4.76
    assert res["streak_pct"] == 10.0  # 100 -> 110 = +10.0%
    assert res["streak_amount"] == 1.1  # 1000 * 110 * 1000 / 1e8 = 1.1億
    assert res["recent_daily_pcts"] == ["+2.0%", "+2.9%", "+4.8%"]


def test_chips_scanner_sorts_by_est_amount_before_vcp_cap(monkeypatch):
    from stock_strategies.chips_scanner import _process_pool

    monkeypatch.setattr("stock_strategies.chips_scanner.get_index_history", lambda *a, **kw: pd.DataFrame())
    called_sids = []
    monkeypatch.setattr(
        "stock_strategies.chips_scanner.get_price_history",
        lambda sid, years=1: called_sids.append(sid) or pd.DataFrame(),
    )

    raw = [
        {"stock_id": "2002", "name": "中鋼", "main_streak": 3, "streak_total_net_lots": 5000, "tags": []},
        {"stock_id": "2330", "name": "台積電", "main_streak": 3, "streak_total_net_lots": 1000, "tags": []},
    ]
    recent_prices = {
        "2002": [{"date": "2026-11-13", "close": 20.0, "prev_close": 19.5, "pct": 2.56}],     # 1.0 億
        "2330": [{"date": "2026-11-13", "close": 1000.0, "prev_close": 980.0, "pct": 2.04}],  # 10.0 億
    }
    res = _process_pool(
        raw,
        watchlist_sids=set(),
        watchlist_names={},
        latest_date="2026-11-13",
        max_market_candidates=1,
        is_sell=False,
        delay_sec=0,
        margin_stocks={},
        daily_price_map={},
        recent_prices_map=recent_prices,
        finmind_state={"rate_limited": False},
    )
    assert len(res) == 1
    assert res[0]["stock_id"] == "2330"  # 高價高金額優先進入前段與 VCP 掃描
    assert called_sids == ["2330"]


def test_evaluate_etf_skips_fundamental_and_uses_free_inst(monkeypatch):
    from stock_strategies.evaluate import evaluate
    from stock_strategies.notify import format_messages

    # 造一段 120 日多頭日 K
    n = 120
    dates = pd.bdate_range("2026-01-01", periods=n)
    close = [50.0 + i * 0.2 for i in range(n)]
    px_df = pd.DataFrame({
        "date": dates,
        "open": [c - 0.1 for c in close],
        "high": [c + 0.3 for c in close],
        "low": [c - 0.3 for c in close],
        "close": close,
        "volume": [10_000_000] * n,
    })

    monkeypatch.setattr("stock_strategies.evaluate.get_price_history", lambda sid, years=3: px_df)
    monkeypatch.setattr("stock_strategies.evaluate.get_index_history", lambda *a, **kw: pd.DataFrame())
    monkeypatch.setattr("stock_strategies.evaluate.get_market_valuation_map", lambda: {})
    monkeypatch.setattr(
        "stock_strategies.evaluate.get_fundamental",
        lambda sid: (_ for _ in ()).throw(AssertionError("ETF 不應呼叫 FinMind get_fundamental")),
    )
    monkeypatch.setattr(
        "stock_strategies.evaluate.get_institutional",
        lambda sid, start=None: (_ for _ in ()).throw(AssertionError("有 TWSE/TPEx 免費法人資料時不應打 FinMind")),
    )
    monkeypatch.setattr(
        "stock_strategies.evaluate.get_market_recent_inst_map",
        lambda trading_days_count=5: {
            "0050": [
                {"date": "2026-11-11", "foreign_net": 1000, "trust_net": 500, "total_net": 1500},
                {"date": "2026-11-12", "foreign_net": 2000, "trust_net": 600, "total_net": 2600},
                {"date": "2026-11-13", "foreign_net": 3000, "trust_net": 700, "total_net": 3700},
            ]
        },
    )

    res = evaluate("0050", "元大台灣50")
    assert res["components"]["is_etf"] is True
    assert res["components"]["fundamental_pass"] is True
    assert "土洋同買" in res["components"]["chips_summary"]
    assert "基本面未過門檻" not in res["risk_notes"]

    msg = format_messages([res])[0]
    assert "ETF 趨勢" in msg


def test_cleanup_old_exchange_cache(monkeypatch, tmp_path):
    from datetime import datetime
    from stock_strategies import exchange_data

    monkeypatch.setattr(exchange_data, "EXCHANGE_CACHE_DIR", tmp_path)
    old_m = tmp_path / "market_20200101.parquet"
    old_p = tmp_path / "prices_20200101.parquet"
    today_str = datetime.now().strftime("%Y%m%d")
    cur_m = tmp_path / f"market_{today_str}.parquet"

    old_m.write_bytes(b"old")
    old_p.write_bytes(b"old")
    cur_m.write_bytes(b"new")

    removed = exchange_data._cleanup_old_exchange_cache(days=30)
    assert removed == 2
    assert not old_m.exists()
    assert not old_p.exists()
    assert cur_m.exists()


def test_dual_track_vcp_selection_includes_small_caps_and_excludes_etf():
    from stock_strategies.chips_scanner import _select_vcp_market_sids

    mkt_pool = [
        # 1. 超高金額 ETF（應排除）
        {"stock_id": "00685L", "main_streak": 5, "streak_total_net_lots": 50000, "shares_issued": 100_000_000},
        # 2. 兩檔大資金權值股（連4天仍應憑大金額進入軌道 A）
        {"stock_id": "3661", "main_streak": 4, "streak_total_net_lots": 5000, "shares_issued": 80_000_000},    # 150 億
        {"stock_id": "2891", "main_streak": 5, "streak_total_net_lots": 80000, "shares_issued": 20_000_000_000},  # 32 億
        # 3. 小型鎖碼股：金額僅 1.2 億（低於權值），但佔股本高達 6% (>= 0.3 億) → 應由軌道 B 入選
        {"stock_id": "5230", "main_streak": 5, "streak_total_net_lots": 3000, "shares_issued": 50_000_000},     # 1.2 億, 6%
        # 4. 極低流動性冷門小股：僅 0.05 億 (< 0.3 億) → 軌道 B 應濾除
        {"stock_id": "9999", "main_streak": 5, "streak_total_net_lots": 500, "shares_issued": 5_000_000},       # 0.05 億, 10%
    ]
    recent_prices = {
        "00685L": [{"close": 100.0}],   # 500 億 ETF
        "3661": [{"close": 3000.0}],    # 150 億
        "2891": [{"close": 40.0}],      # 32 億
        "5230": [{"close": 40.0}],      # 1.2 億
        "9999": [{"close": 10.0}],      # 0.05 億
    }

    selected = _select_vcp_market_sids(mkt_pool, recent_prices, {}, top_amt=2, top_cap=2)
    assert "00685L" not in selected
    assert "3661" in selected
    assert "2891" in selected
    assert "5230" in selected
    assert "9999" not in selected

