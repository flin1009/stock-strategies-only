import numpy as np
import pandas as pd

from .bb_trend import bb_trend_indices
from .config import CONFIG
from .indicators import tech_score_at
from .vcp import vcp_breakout_indices


def _apply_cooldown(raw_indices: list[int], cooldown: int) -> list[int]:
    indices: list[int] = []
    last_i = -999
    for i in raw_indices:
        if i - last_i >= cooldown:
            indices.append(i)
            last_i = i
    return indices


def backtest(df: pd.DataFrame, params: dict | None = None) -> dict:
    """對歷史所有技術分達門檻（或 VCP / 布林開口）的日子做結算。

    訊號日為第 i 天收盤產生，實際進場為第 i+1 天開盤（符合可執行性）。

    params (扁平 dict) 可覆寫：
      - hold_days
      - min_tech_score_for_signal
      - target_return / stop_loss
      - use_ma_alignment / use_bollinger_bounce / use_kd_golden_cross / use_macd_bullish
      - vcp_only_signal / bb_only_signal / exit_mode ('fixed' | 'ma20_3d') / _index_df
    沒給就退回到舊 CONFIG。
    """
    if params is None:
        params = {}
    hold_days = int(params.get("hold_days", CONFIG["hold_days"]))
    min_score = int(params.get("min_tech_score_for_signal", CONFIG["min_tech_score_for_signal"]))
    target = float(params.get("target_return", CONFIG["target_return"]))
    stop = float(params.get("stop_loss", CONFIG["stop_loss"]))
    exit_mode = str(params.get("exit_mode", "fixed")).strip().lower()
    end_idx = len(df) - (5 if exit_mode == "ma20_3d" else hold_days + 1)

    if params.get("vcp_only_signal"):
        raw_indices = vcp_breakout_indices(
            df,
            start_idx=220,
            end_idx=end_idx,
            index_df=params.get("_index_df"),
        )
        indices = _apply_cooldown(raw_indices, cooldown=10)
    elif params.get("bb_only_signal"):
        raw_indices = bb_trend_indices(
            df,
            start_idx=40,
            end_idx=end_idx,
            min_turnover=float(params.get("bb_min_turnover", 30_000_000.0)),
        )
        indices = _apply_cooldown(raw_indices, cooldown=5)
    else:
        indices = []
        for i in range(60, max(60, end_idx)):
            if tech_score_at(df.iloc[i], params)["score"] >= min_score:
                indices.append(i)

    if not indices:
        return {"winrate": None, "samples": 0, "avg_return": None}

    wins = losses = 0
    returns = []
    ma20_arr = (
        df["ma20"].values
        if "ma20" in df.columns
        else pd.to_numeric(df["close"], errors="coerce").rolling(20).mean().values
    )
    for idx in indices:
        next_day = df.iloc[idx + 1]
        entry = next_day.get("open")
        if entry is None or pd.isna(entry) or entry <= 0:
            continue

        if exit_mode == "ma20_3d":
            end_f = min(len(df), idx + 1 + hold_days)
            if end_f - (idx + 1) < 4:
                continue
            r: float | None = None
            below_streak = 0
            for k in range(idx + 1, end_f):
                row = df.iloc[k]
                if row["low"] <= entry * (1 - stop):
                    r = -stop
                    break
                ma20 = ma20_arr[k]
                if pd.notna(ma20) and row["close"] < ma20:
                    below_streak += 1
                    if below_streak >= 3:
                        r = (float(row["close"]) - entry) / entry
                        break
                else:
                    below_streak = 0
            if r is None:
                r = (float(df.iloc[end_f - 1]["close"]) - entry) / entry
            returns.append(r)
            if r > 0:
                wins += 1
            else:
                losses += 1
            continue

        future = df.iloc[idx + 2 : idx + 2 + hold_days]
        if len(future) < hold_days:
            continue

        hi, lo = future["high"].max(), future["low"].min()
        fc = future.iloc[-1]["close"]

        hit_target = hi >= entry * (1 + target)
        hit_stop = lo <= entry * (1 - stop)

        if hit_target and not hit_stop:
            wins += 1
            returns.append(target)
        elif hit_stop:
            losses += 1
            returns.append(-stop)
        else:
            r = (fc - entry) / entry
            returns.append(r)
            if r > 0:
                wins += 1
            else:
                losses += 1

    total = wins + losses
    if total == 0:
        return {"winrate": None, "samples": 0}

    return {
        "winrate": round(wins / total, 3),
        "samples": total,
        "avg_return": round(float(np.mean(returns)), 4),
    }
