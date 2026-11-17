"""測試台股實戰版布林開口趨勢波段策略 (BB Trend) 全鏈路整合。"""
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from stock_strategies.bb_trend import bb_trend_indices, detect_bb_trend
from stock_strategies.backtest import backtest
from stock_strategies.chips_scanner import enrich_candidate_price
from stock_strategies.factors import compute_factor, list_factors
from stock_strategies.loader import get_strategy
from stock_strategies.notify import format_chips_streak, format_messages, format_premarket


def _make_bb_breakout_df(
    *,
    last_jump: float = 10.0,
    prev_bw_wide: bool = False,
    ma20_falling: bool = False,
    low_turnover: bool = False,
) -> pd.DataFrame:
    """構造符合或刻意違反「布林開口起漲」條件的 80 日 OHLCV DataFrame。"""
    n = 80
    dates = pd.date_range("2025-01-01", periods=n, freq="B").strftime("%Y-%m-%d")

    if ma20_falling:
        # 一路緩跌再突然反彈一天，使得 MA20 < MA20[-4]
        closes = np.linspace(140.0, 95.0, n)
        closes[-3] = 94.0
        closes[-2] = 93.0
        closes[-1] = 102.0
    elif prev_bw_wide:
        # 近 20 日大幅上下震盪，造成前一日帶寬 > 15%，同時讓 MACD 剛翻正
        closes = np.full(n, 100.0)
        closes[:40] = np.linspace(90.0, 102.0, 40)
        for i in range(40, n - 1):
            closes[i] = 100.0 + (12.0 if i % 2 == 0 else -12.0)
        # 讓最近幾天先微壓回再上拉，確保 osc 由負轉正
        closes[n - 8 : n - 1] = [110.0, 88.0, 108.0, 86.0, 85.0, 86.0, 88.0]
        closes[-1] = 106.0
    else:
        # 前段緩跌築底 -> 窄幅上升使 MA20 向上且 MACD 負值收斂 -> 最後一日放量突破帶寬雙向開口
        closes = np.linspace(108.0, 95.0, 50).tolist()
        # 接下來 29 天緩步震盪回升，使 MA20 緩步向上且前一日帶寬 <= 15%、MACD 仍小負
        mid = np.linspace(95.5, 100.2, 29)
        for j, val in enumerate(mid):
            # 稍微修正尾段讓 osc[-2] <= 0
            if j >= 22:
                val -= (j - 21) * 0.45
            closes.append(float(val))
        closes.append(float(closes[-1] + last_jump))
        closes = np.array(closes, dtype=float)

    highs = closes * 1.01
    lows = closes * 0.99
    vols = np.full(n, 1000.0)
    if low_turnover:
        vols[-1] = 10.0  # 10 張 * 100 ~= 1,000 元，遠低於 3,000 萬門檻
    else:
        vols[-1] = 5_000_000.0  # 5000 張 (單位股) -> 成交額 > 5 億

    return pd.DataFrame({
        "date": dates,
        "open": closes * 0.998,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": vols,
    })


def test_detect_bb_trend_trigger_and_filters():
    # 1. 標準觸發：MACD 翻正 + 前日窄帶寬 + 雙向開口 + 站上連 3 日上揚 MA20 + 高成交額
    df_ok = _make_bb_breakout_df(last_jump=10.0)
    res_ok = detect_bb_trend(df_ok)
    assert res_ok["signal"] == "布林開口起漲"
    assert res_ok["bonus"] == 10
    assert res_ok["bw_prev"] <= 15.0
    assert res_ok["bw"] > res_ok["bw_prev"]
    assert res_ok["osc"] > 0
    assert "MACD翻正" in res_ok["detail"]

    # 索引列表也能找到最後一根 K 棒
    idxs = bb_trend_indices(df_ok)
    assert len(df_ok) - 1 in idxs

    # 2. 前日帶寬過寬 (>15%) 過濾
    df_wide = _make_bb_breakout_df(prev_bw_wide=True)
    assert detect_bb_trend(df_wide)["signal"] is None

    # 3. 均線下彎過濾 (MA20 <= MA20[-4])
    df_down = _make_bb_breakout_df(ma20_falling=True)
    assert detect_bb_trend(df_down)["signal"] is None

    # 4. 成交金額過濾 (< 3,000 萬)
    df_illiquid = _make_bb_breakout_df(low_turnover=True)
    assert detect_bb_trend(df_illiquid)["signal"] is None

    # 5. 資料長度不足 40 天不崩潰
    assert detect_bb_trend(df_ok.iloc[:30])["signal"] is None


def test_bb_trend_registered_factor_and_strategy():
    names = {f["name"] for f in list_factors()}
    assert "breakout.bb_trend" in names
    df_ok = _make_bb_breakout_df(last_jump=10.0)
    score = compute_factor("breakout.bb_trend", SimpleNamespace(price_df=df_ok), {})
    assert score == 1.0

    strat = get_strategy("bb_trend")
    assert strat is not None
    assert strat["params"]["exit_mode"] == "ma20_3d"
    assert strat["params"]["bb_only_signal"] is True


def test_backtest_ma20_3d_trailing_exit_and_hard_stop():
    df_ok = _make_bb_breakout_df(last_jump=10.0)
    base = df_ok.copy()
    last_date = pd.to_datetime(base["date"].iloc[-1])
    # 延續 20 根 K 棒：先大漲波段到 ~136，再跌破 MA20 連續 3 日不回 (結束時 ~110 仍有小賺，遠高於 -8% 硬停損)
    extra_rows = []
    wave_closes = [
        112.0, 118.0, 125.0, 130.0, 134.0, 136.0, 135.0, 132.0,
        128.0, 124.0, 121.0, 119.0, 117.0, 115.0, 114.0, 113.0,
        112.0, 111.0, 110.0, 109.0,
    ]
    for k, c_val in enumerate(wave_closes, start=1):
        d_str = (last_date + pd.Timedelta(days=k)).strftime("%Y-%m-%d")
        extra_rows.append({
            "date": d_str,
            "open": c_val,
            "high": c_val * 1.01,
            "low": c_val * 0.99,
            "close": c_val,
            "volume": 5_000_000.0,
        })
    full_df = pd.concat([base, pd.DataFrame(extra_rows)], ignore_index=True)

    # 測試 1：只用 bb_only_signal + ma20_3d 出場，不會觸及 -8% 硬停損而是依破月線連 3 日出場獲利
    bt_trail = backtest(
        full_df,
        params={
            "bb_only_signal": True,
            "exit_mode": "ma20_3d",
            "stop_loss": 0.08,
            "target_return": 0.25,
            "hold_days": 60,
            "market_filter_enabled": False,
        },
    )
    assert bt_trail["samples"] == 1
    assert bt_trail["winrate"] == 1.0
    assert bt_trail["avg_return"] > 0.0

    # 測試 2：觸發 -8% 硬停損時精準鎖定 -0.08 出場
    crash_rows = []
    for k, c_val in enumerate([109.0, 92.0, 91.0, 90.0, 89.0, 88.0], start=1):
        d_str = (last_date + pd.Timedelta(days=k)).strftime("%Y-%m-%d")
        crash_rows.append({
            "date": d_str,
            "open": c_val,
            "high": c_val * 1.005,
            "low": c_val * 0.99,
            "close": c_val,
            "volume": 5_000_000.0,
        })
    crash_df = pd.concat([base, pd.DataFrame(crash_rows)], ignore_index=True)
    bt_stop = backtest(
        crash_df,
        params={
            "bb_only_signal": True,
            "exit_mode": "ma20_3d",
            "stop_loss": 0.08,
            "market_filter_enabled": False,
        },
    )
    assert bt_stop["samples"] == 1
    assert bt_stop["winrate"] == 0.0
    assert bt_stop["avg_return"] == pytest.approx(-0.08, abs=1e-4)


def test_chips_scanner_enrich_bb_and_notify_rendering(monkeypatch):
    import stock_strategies.chips_scanner as cs

    df_ok = _make_bb_breakout_df(last_jump=10.0)
    monkeypatch.setattr(cs, "get_price_history", lambda sid, years=1: df_ok)
    monkeypatch.setattr(cs, "get_market_margin_map", lambda: {"stocks": {}})
    monkeypatch.setattr(cs, "get_market_daily_price_map", lambda: {})
    monkeypatch.setattr(cs, "get_market_recent_prices_map", lambda days=10: {})

    cand = {
        "stock_id": "2317",
        "name": "鴻海",
        "main_streak": 4,
        "streak_total_net_lots": 2500,
        "tags": [],
    }
    enriched = enrich_candidate_price(cand, index_df=None, check_vcp=True)
    assert enriched["bb_signal"] == "布林開口起漲"
    assert "布林開口起漲" in enriched["tags"]
    assert "MACD翻正" in enriched["bb_detail"]

    # 20:08 籌碼雷達統一區塊呈現 🌊 布林開口
    msg_chips = format_chips_streak({
        "buy_records": [enriched],
        "sell_records": [],
    })
    assert "⚡ *【法人連買 × 形態起漲雷達】* (1 檔)" in msg_chips
    assert "🌊 *2317 鴻海* (布林開口 | 連買 4 天" in msg_chips

    # 16:38 BUY(ma20_3d) 與 WATCH(🌊布林開口) + 07:07 早盤 inline 標示
    signals = [
        {
            "stock_id": "2317",
            "name": "鴻海",
            "action": "BUY",
            "signal_score": 86,
            "entry_price": 108.0,
            "stop_loss_price": 99.36,
            "target_price": 135.0,
            "exit_mode": "ma20_3d",
            "components": {
                "tech_signals": ["布林開口起漲", "MACD翻正"],
                "volume_patterns": ["放量突破"],
                "chips_summary": "外資連買",
                "valuation": {"pe": 14.2, "yield": 4.5},
                "exit_mode": "ma20_3d",
            },
            "trend": {"chg_5d": 5.2},
            "risk_notes": [],
        },
        {
            "stock_id": "2303",
            "name": "聯電",
            "action": "WATCH",
            "signal_score": 68,
            "entry_price": 52.0,
            "stop_loss_price": 47.84,
            "components": {
                "tech_signals": ["布林開口起漲"],
                "chips_summary": "投信連買",
                "valuation": {"pe": 12.1, "yield": 5.8},
            },
            "trend": {"chg_5d": 2.1},
        },
    ]

    daily_msgs = format_messages(signals)
    daily_txt = "\n".join(daily_msgs)
    assert "出場: 破月線連3日不回" in daily_txt
    assert "🌊布林開口" in daily_txt

    premarket_txt = format_premarket(
        None,
        [
            {
                "stock_id": "2317",
                "name": "鴻海",
                "action": "BUY",
                "signal_score": 86,
                "chips_summary": "外資連買",
                "tech_signals": "['布林開口起漲']",
            }
        ],
    )
    assert "· 🌊布林開口" in premarket_txt
