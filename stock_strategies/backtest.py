import numpy as np
import pandas as pd

from .config import CONFIG
from .indicators import tech_score_at
from .vcp import vcp_breakout_indices


def backtest(df: pd.DataFrame, params: dict | None = None) -> dict:
    """對歷史所有技術分達門檻（或 VCP 突破）的日子做持有 N 日結算。

    訊號日為第 i 天收盤產生，實際進場為第 i+1 天開盤（符合可執行性）。

    params (扁平 dict) 可覆寫：
      - hold_days
      - min_tech_score_for_signal
      - target_return / stop_loss
      - use_ma_alignment / use_bollinger_bounce / use_kd_golden_cross / use_macd_bullish
      - vcp_only_signal / _index_df
    沒給就退回到舊 CONFIG。
    """
    if params is None:
        params = {}
    hold_days = int(params.get("hold_days", CONFIG["hold_days"]))
    min_score = int(params.get("min_tech_score_for_signal", CONFIG["min_tech_score_for_signal"]))
    target = float(params.get("target_return", CONFIG["target_return"]))
    stop = float(params.get("stop_loss", CONFIG["stop_loss"]))

    if params.get("vcp_only_signal"):
        raw_indices = vcp_breakout_indices(
            df,
            start_idx=220,
            end_idx=len(df) - hold_days - 1,
            index_df=params.get("_index_df"),
        )
        # 10 日冷卻期，避免同波連續突破重複計數
        indices = []
        last_i = -999
        for i in raw_indices:
            if i - last_i >= 10:
                indices.append(i)
                last_i = i
    else:
        indices = []
        for i in range(60, len(df) - hold_days - 1):
            if tech_score_at(df.iloc[i], params)["score"] >= min_score:
                indices.append(i)

    if not indices:
        return {"winrate": None, "samples": 0, "avg_return": None}

    wins = losses = 0
    returns = []
    for idx in indices:
        next_day = df.iloc[idx + 1]
        entry = next_day.get("open")
        if entry is None or pd.isna(entry) or entry <= 0:
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
