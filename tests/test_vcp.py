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
