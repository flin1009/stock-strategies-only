import pandas as pd
import numpy as np


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    df["ma5"] = df["close"].rolling(5).mean()
    df["ma20"] = df["close"].rolling(20).mean()
    df["ma60"] = df["close"].rolling(60).mean()

    df["bb_mid"] = df["close"].rolling(20).mean()
    bb_std = df["close"].rolling(20).std()
    df["bb_upper"] = df["bb_mid"] + 2 * bb_std
    df["bb_lower"] = df["bb_mid"] - 2 * bb_std

    low_min = df["low"].rolling(9).min()
    high_max = df["high"].rolling(9).max()
    rsv = (df["close"] - low_min) / (high_max - low_min) * 100
    df["k"] = rsv.ewm(com=2).mean()
    df["d"] = df["k"].ewm(com=2).mean()

    ema12 = df["close"].ewm(span=12, adjust=False).mean()
    ema26 = df["close"].ewm(span=26, adjust=False).mean()
    df["dif"] = ema12 - ema26
    df["dea"] = df["dif"].ewm(span=9, adjust=False).mean()
    df["macd_hist"] = df["dif"] - df["dea"]

    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift()).abs(),
        (df["low"] - df["close"].shift()).abs(),
    ], axis=1).max(axis=1)
    df["atr"] = tr.rolling(14).mean()

    return df


def tech_score_at(row: pd.Series, params: dict | None = None) -> dict:
    """對一天計算技術分 (0-100)。
    params 可包含 use_ma_alignment / use_bollinger_bounce / use_kd_golden_cross /
    use_macd_bullish 四個布林開關來開關各訊號。
    """
    if params is None:
        params = {}
    use_ma = params.get("use_ma_alignment", True)
    use_bb = params.get("use_bollinger_bounce", True)
    use_kd = params.get("use_kd_golden_cross", True)
    use_macd = params.get("use_macd_bullish", True)

    # 開啟的訊號數量決定每個訊號最大分數，讓總分維持 0-100
    enabled = sum([use_ma, use_bb, use_kd, use_macd]) or 1
    max_per = 100 / enabled

    score = 0.0
    signals: list[str] = []

    if use_ma and pd.notna(row["ma20"]) and pd.notna(row["ma60"]):
        if row["close"] > row["ma20"] > row["ma60"]:
            score += max_per
            signals.append("均線多頭")
        elif row["close"] > row["ma20"]:
            score += max_per * 0.48

    if use_bb and pd.notna(row["bb_lower"]) and pd.notna(row["bb_mid"]):
        dist = (row["close"] - row["bb_lower"]) / row["bb_lower"]
        if 0 < dist < 0.03:
            score += max_per
            signals.append("布林下軌反彈")
        elif row["close"] < row["bb_mid"]:
            score += max_per * 0.4

    if use_kd and pd.notna(row["k"]) and pd.notna(row["d"]):
        if row["k"] > row["d"] and row["k"] < 80:
            score += max_per
            signals.append("KD黃金交叉")
        elif row["k"] > row["d"]:
            score += max_per * 0.4

    if use_macd and pd.notna(row["macd_hist"]):
        if row["macd_hist"] > 0 and row["dif"] > row["dea"]:
            score += max_per
            signals.append("MACD多頭")
        elif row["macd_hist"] > 0:
            score += max_per * 0.4

    return {"score": int(round(score)), "signals": signals}
