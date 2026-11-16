"""台股實戰版 VCP 策略與因子單元測試。"""
import numpy as np
import pandas as pd

import stock_strategies.factors.breakout  # noqa: F401
from stock_strategies.backtest import backtest
from stock_strategies.context import FactorContext
from stock_strategies.factors.registry import compute_factor
from stock_strategies.indicators import add_indicators
from stock_strategies.loader import get_strategy, merge_params
from stock_strategies.notify import format_messages
from stock_strategies.vcp import detect_vcp, vcp_breakout_indices


def _make_vcp_df(breakout: bool = True, solid_bar: bool = True) -> pd.DataFrame:
    """建構符合 Minervini 趨勢樣板 + 兩段波動收縮的合成日 K (280 根)。"""
    n = 280
    dates = pd.bdate_range("2025-01-02", periods=n)
    # 0..224: 穩定上行，讓 MA50 > MA150 > MA200 且 MA200 持續上揚，從 50 漲到 110
    close = np.linspace(50.0, 110.0, n)
    high = close * 1.01
    low = close * 0.99
    open_ = close * 0.995
    vol = np.full(n, 1_000_000.0)

    # 前段整理 [n-55 : n-15] (40 天): 先拉到 120 再回檔到 100 -> 深度 ~16.7% (>=10%)
    for i in range(n - 55, n - 15):
        if i < n - 35:
            c_val = 118.0 if i == n - 54 else 112.0
            h_val = 120.0 if i == n - 54 else 113.0
            l_val = 110.0
        else:
            c_val = 102.0 if i == n - 34 else 107.0
            h_val = 108.0
            l_val = 100.0 if i == n - 34 else 105.0
        close[i], open_[i], high[i], low[i] = c_val, c_val * 0.995, h_val, l_val

    # 末段整理 [n-15 : n-1] (14 天): 收縮於 108 ~ 115 -> 深度 (115-108)/115 ≈ 6.1% (<=12% 且 <= 前段 65%)
    for i in range(n - 15, n - 1):
        c_val = 112.0 + (i % 2) * 1.0
        h_val = 115.0 if i == n - 10 else 113.5
        l_val = 108.0 if i == n - 12 else 110.0
        close[i], open_[i], high[i], low[i] = c_val, c_val * 0.995, h_val, l_val

    # pivot = 115.0, 前一日 close = 113.0 (<= 115)
    close[n - 2] = 113.0
    open_[n - 2] = 112.0
    high[n - 2] = 113.5
    low[n - 2] = 111.5

    # 當日 (idx = n - 1)
    if breakout:
        if solid_bar:
            open_[n - 1] = 113.5
            low[n - 1] = 113.0
            high[n - 1] = 118.0
            close[n - 1] = 117.5  # 收盤穿越 115 且收在振幅上半部陽線
        else:
            # 假突破長上影收黑
            open_[n - 1] = 117.5
            high[n - 1] = 122.0
            low[n - 1] = 114.0
            close[n - 1] = 116.0
        vol[n - 1] = 2_000_000.0  # > 1.5x avg50 (1_000_000)
    else:
        # 蓄勢狀態：距離 115 在 3% 內 (>= 111.55)
        open_[n - 1] = 113.0
        low[n - 1] = 112.5
        high[n - 1] = 114.5
        close[n - 1] = 114.0
        vol[n - 1] = 900_000.0

    return pd.DataFrame({
        "date": dates,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": vol,
    })


def test_detect_vcp_breakout_and_setup():
    df = _make_vcp_df(breakout=True, solid_bar=True)
    res = detect_vcp(df, min_turnover=0)
    assert res["trend_ok"] is True
    assert res["contract_ok"] is True
    assert res["pivot"] == 115.0
    assert res["signal"] == "VCP突破"
    assert res["bonus"] == 12

    df_setup = _make_vcp_df(breakout=False)
    res_setup = detect_vcp(df_setup, min_turnover=0)
    assert res_setup["signal"] == "VCP蓄勢"
    assert res_setup["bonus"] == 5

    df_fake = _make_vcp_df(breakout=True, solid_bar=False)
    res_fake = detect_vcp(df_fake, min_turnover=0)
    assert res_fake["signal"] is None


def test_detect_vcp_rs_filter():
    df = _make_vcp_df(breakout=True, solid_bar=True)
    # 建構漲幅遠勝個股的大盤指數 (從 100 飆到 400 -> +300%)，個股 RS 應不通過
    idx_df = pd.DataFrame({
        "date": df["date"],
        "close": np.linspace(100.0, 400.0, len(df)),
    })
    res = detect_vcp(df, index_df=idx_df, min_turnover=0)
    assert res["rs_ok"] is False
    assert res["signal"] is None


def test_vcp_breakout_indices_and_backtest():
    df = _make_vcp_df(breakout=True, solid_bar=True)
    # 往後延長 35 根上漲 K 棒以完整跑完 30 日持有回測
    extra_n = 35
    last_date = df["date"].iloc[-1]
    extra_dates = pd.bdate_range(last_date + pd.Timedelta(days=1), periods=extra_n)
    extra_close = np.linspace(118.0, 140.0, extra_n)
    extra = pd.DataFrame({
        "date": extra_dates,
        "open": extra_close * 0.995,
        "high": extra_close * 1.01,
        "low": extra_close * 0.99,
        "close": extra_close,
        "volume": np.full(extra_n, 1_200_000.0),
    })
    full_df = pd.concat([df, extra], ignore_index=True)
    full_df = add_indicators(full_df)

    hits = vcp_breakout_indices(full_df, min_turnover=0)
    assert 279 in hits

    bt = backtest(full_df, {
        "vcp_only_signal": True,
        "hold_days": 30,
        "target_return": 0.15,
        "stop_loss": 0.08,
    })
    assert bt["samples"] >= 1
    assert bt["winrate"] == 1.0


def test_vcp_registered_factor():
    df = _make_vcp_df(breakout=True, solid_bar=True)
    e = pd.DataFrame()
    ctx = FactorContext(
        stock_id="2330",
        as_of=df["date"].iloc[-1],
        price_df=df,
        index_df=e,
        inst=e,
        revenue=e,
        valuation=e,
        margin=e,
        shareholding=e,
        fundamentals={},
    )
    assert compute_factor("breakout.vcp", ctx, {"vcp_min_turnover": 0}) == 1.0

    df_setup = _make_vcp_df(breakout=False)
    ctx_setup = FactorContext(
        stock_id="2330",
        as_of=df_setup["date"].iloc[-1],
        price_df=df_setup,
        index_df=e,
        inst=e,
        revenue=e,
        valuation=e,
        margin=e,
        shareholding=e,
        fundamentals={},
    )
    assert compute_factor("breakout.vcp", ctx_setup, {"vcp_min_turnover": 0}) == 0.8


def test_vcp_strategy_json_loads():
    s = get_strategy("vcp")
    assert s is not None
    assert s["id"] == "vcp"
    merged = merge_params(s)
    assert merged["target_return"] == 0.15
    assert merged["stop_loss"] == 0.08
    assert merged["hold_days"] == 30
    assert merged["use_vcp_breakout"] is True
    assert merged["vcp_only_signal"] is True


def test_format_messages_dynamic_stop_target_pct():
    signals = [{
        "stock_id": "2330",
        "name": "台積電",
        "action": "BUY",
        "signal_score": 88.0,
        "entry_price": 100.0,
        "stop_loss_price": 92.0,
        "target_price": 115.0,
        "components": {
            "tech_score": 90,
            "tech_signals": ["均線多頭", "VCP突破"],
            "volume_patterns": ["倍量柱"],
            "valuation": {"pe": 18.0, "yield": 3.0, "pb": 4.0},
        },
        "trend": {"chg_5d": 4.2},
        "risk_notes": [],
    }]
    msg = format_messages(signals)[0]
    assert "訊號: 均線多頭/VCP突破" in msg
    assert "損 92.0 (-8%) / 標 115.0 (+15%)" in msg

    watch_signals = [{
        "stock_id": "3105",
        "name": "穩懋",
        "action": "WATCH",
        "signal_score": 68.0,
        "entry_price": 200.0,
        "stop_loss_price": 184.0,
        "components": {
            "tech_signals": ["均線多頭", "VCP蓄勢"],
            "valuation": {"pe": 25.0, "yield": 1.0, "pb": 2.0},
        },
        "risk_notes": [],
    }]
    wmsg = format_messages(watch_signals)[0]
    assert "🎯VCP蓄勢" in wmsg


def test_explain_why_market_downgrade():
    from stock_strategies.notify import _explain_why

    s = {
        "signal_score": 81.6,
        "components": {"fundamental_pass": True, "tech_score": 80},
        "risk_notes": ["大盤跌破月線，自動降為 WATCH"],
    }
    assert _explain_why(s) == "大盤跌破月線降級（個股條件已達標）"

    s_night = {
        "signal_score": 80.0,
        "components": {"fundamental_pass": True, "tech_score": 75},
        "risk_notes": ["昨晚夜盤大跌 -2.0%，自動降為 WATCH"],
    }
    assert _explain_why(s_night) == "昨晚夜盤大跌降級（個股條件已達標）"


def test_chips_scanner_enrich_vcp_and_format(monkeypatch):
    from stock_strategies.chips_scanner import enrich_candidate_price
    from stock_strategies.notify import format_chips_streak

    df = _make_vcp_df(breakout=True, solid_bar=True)
    monkeypatch.setattr("stock_strategies.chips_scanner.get_price_history", lambda sid, years=1: df)
    monkeypatch.setattr("stock_strategies.chips_scanner.get_market_margin_map", lambda: {"stocks": {}, "market_total_margin_diff_lots": 0})

    cand = {"stock_id": "2330", "name": "台積電", "main_streak": 4, "streak_total_net_lots": 5000, "tags": ["土洋同買"]}
    enriched = enrich_candidate_price(cand, check_vcp=True)
    assert enriched["vcp_signal"] == "VCP突破"
    assert "VCP突破" in enriched["tags"]
    assert enriched["vcp_pivot"] == 115.0

    msg = format_chips_streak({"buy_records": [enriched], "sell_records": []})
    assert "⚡ *【法人連買 × VCP 起漲雷達】* (1 檔)" in msg
    assert "🎯 *2330 台積電* (VCP突破 | 連買 4 天" in msg

    msg_empty = format_chips_streak({"buy_records": [{"stock_id": "2002", "name": "中鋼", "main_streak": 3, "streak_total_net_lots": 100, "today_close": 20.0, "today_pct": 0.5, "streak_pct": 1.0}], "sell_records": []})
    assert "VCP 起漲雷達" not in msg_empty


def test_format_premarket_with_vcp_tag():
    from stock_strategies.notify import format_premarket

    signals = [
        {"date": "2026-11-16", "action": "BUY", "stock_id": "2330", "name": "台積電", "signal_score": 88, "tech_signals": "均線多頭, VCP突破"},
        {"date": "2026-11-16", "action": "WATCH", "stock_id": "3105", "name": "穩懋", "signal_score": 72, "tech_signals": "均線多頭, VCP蓄勢"},
        {"date": "2026-11-16", "action": "WATCH", "stock_id": "2412", "name": "中華電", "signal_score": 65, "tech_signals": "KD黃金交叉"},
    ]
    msg = format_premarket(None, signals)
    assert "BUY 2330 台積電 (88分) ·" in msg
    assert "· 🎯VCP突破" in msg
    assert "WATCH 3105 穩懋 (72分) ·" in msg
    assert "· 🎯VCP蓄勢" in msg
    assert "中華電 (65分) · 夜盤中性⚪\n" in msg  # 無 VCP 者保持乾淨


def test_performance_chronological_stop_before_target(monkeypatch):
    from stock_strategies.performance import update_performance

    # 訊號日 2026-10-01，隔日 2026-10-02 開盤 100.0 進場
    # 第 3 個交易日跌破 -8% (low=90)，第 15 個交易日才漲回 +15% (high=115) → 應只計 hit_stop=Y, hit_target=N
    dates = pd.bdate_range("2026-10-01", periods=25)
    opens = [100.0] * 25
    highs = [102.0] * 25
    lows = [98.0] * 25
    closes = [100.0] * 25

    lows[3] = 90.0    # 先停損 (-10% <= -8%)
    closes[3] = 91.0
    highs[15] = 115.0  # 後才反彈到 +15%
    closes[15] = 114.0

    px = pd.DataFrame({"date": dates, "open": opens, "high": highs, "low": lows, "close": closes})
    monkeypatch.setattr("stock_strategies.performance.get_price_history", lambda sid, years=1: px)

    existing = [{
        "signal_date": "2026-10-01",
        "stock_id": "2330",
        "name": "台積電",
        "entry_close": 100.0,
        "entry_open": "",
        "t5_date": "", "t5_close": "", "t5_ret": "",
        "t10_date": "", "t10_close": "", "t10_ret": "",
        "t20_date": "", "t20_close": "", "t20_ret": "",
        "hit_target": "", "hit_stop": "",
        "status": "追蹤中",
    }]
    out = update_performance(existing, [])
    assert len(out) == 1
    assert out[0]["hit_stop"] == "Y"
    assert out[0]["hit_target"] == "N"
    assert out[0]["status"] == "完成"


def test_get_index_history_twse_fmtqik_fallback(monkeypatch, tmp_path):
    from stock_strategies import datasources as ds
    from stock_strategies import exchange_data as ed
    from stock_strategies.cache import FinMindRateLimitError

    monkeypatch.setattr(ed, "EXCHANGE_CACHE_DIR", tmp_path)

    def boom(*a, **kw):
        raise FinMindRateLimitError("402")

    monkeypatch.setattr(ds, "fetch_finmind_cached", boom)

    fake_fmtqik = {
        "stat": "OK",
        "data": [
            ["115/11/02", "0", "0", "0", "23,000.50", "+100.00"],
            ["115/11/03", "0", "0", "0", "23,250.00", "+249.50"],
        ],
    }

    class FakeResp:
        ok = True
        def json(self):
            return fake_fmtqik

    monkeypatch.setattr(ed.requests, "get", lambda *a, **kw: FakeResp())

    out = ds.get_index_history("TAIEX", start="2026-11-01")
    assert len(out) == 2
    assert list(out["close"]) == [23000.5, 23250.0]


def test_default_strategy_matches_param_defaults():
    s = get_strategy("default")
    assert s is not None
    p = s["params"]
    assert p["eps_threshold"] == 2.0
    assert p["weight_chips"] == 0.25
    assert p["weight_technical"] == 0.35
    assert p["weight_fundamental"] == 0.25
    assert p["weight_backtest"] == 0.15
    assert p["use_vcp_breakout"] is True
    assert p["vcp_only_signal"] is False
