"""
V3.0 每日選股訊號系統 (All-in-one)

每天跑一次：
  1. 從 Google Sheet 讀 watchlist
  2. FinMind 抓基本面 + 日 K
  3. V3.0 基本面篩選 + 技術面評分 + 歷史回測
  4. 發 Telegram 通知
  5. 寫回 Google Sheet Signals 分頁

環境變數 (設在 GitHub Actions secrets 或本機 .env):
  FINMIND_TOKEN          - FinMind API token
  TELEGRAM_BOT_TOKEN     - Telegram Bot token
  TELEGRAM_CHAT_ID       - 你的 Telegram chat ID
  GOOGLE_SHEET_ID        - Google Sheet 的 ID
  GOOGLE_CREDS_JSON      - Google Service Account JSON 整串（單行）

執行: python v3_daily_signal.py
"""

import os
import sys
import json
import time
from datetime import datetime, timedelta
from typing import Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import requests
import pandas as pd
import numpy as np
import gspread
from google.oauth2.service_account import Credentials


# ============ 設定 ============

FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"
TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"

CONFIG = {
    "eps_threshold": 5.0,
    "roe_threshold": 15.0,
    "backtest_years": 3,
    "hold_days": 20,
    "target_return": 0.10,
    "stop_loss": 0.08,
    "min_tech_score_for_signal": 60,
    "min_total_score_for_buy": 65,
}


# ============ Google Sheet 操作 ============

def get_gsheet():
    creds_json = os.environ["GOOGLE_CREDS_JSON"]
    creds_dict = json.loads(creds_json)
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    gc = gspread.authorize(creds)
    return gc.open_by_key(os.environ["GOOGLE_SHEET_ID"])


def read_watchlist() -> list[dict]:
    """從 Google Sheet Watchlist 分頁讀股票清單"""
    sh = get_gsheet()
    ws = sh.worksheet("Watchlist")
    rows = ws.get_all_records()
    enabled = [
        r for r in rows
        if str(r.get("enabled", "")).upper() in ("TRUE", "1", "YES")
    ]
    return enabled


def append_signals(signals: list[dict]):
    """把結果寫回 Signals 分頁"""
    if not signals:
        return
    sh = get_gsheet()
    try:
        ws = sh.worksheet("Signals")
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title="Signals", rows=1000, cols=20)
        ws.append_row([
            "date", "stock_id", "name", "action", "signal_score",
            "entry_price", "stop_loss_price", "target_price",
            "rr_ratio", "position_pct", "winrate", "samples",
            "tech_signals", "risk_notes"
        ])

    rows = []
    for s in signals:
        c = s.get("components", {})
        rows.append([
            s.get("date", ""),
            s.get("stock_id", ""),
            s.get("name", ""),
            s.get("action", ""),
            s.get("signal_score", ""),
            s.get("entry_price", ""),
            s.get("stop_loss_price", ""),
            s.get("target_price", ""),
            s.get("risk_reward_ratio", ""),
            s.get("position_size_pct", ""),
            c.get("backtest_winrate", ""),
            c.get("backtest_samples", ""),
            ", ".join(c.get("tech_signals", [])),
            " / ".join(s.get("risk_notes", [])),
        ])
    ws.append_rows(rows)


# ============ FinMind 資料抓取 ============

def fetch_finmind(dataset: str, stock_id: str, start_date: str) -> pd.DataFrame:
    params = {
        "dataset": dataset,
        "data_id": stock_id,
        "start_date": start_date,
        "token": os.environ["FINMIND_TOKEN"],
    }
    r = requests.get(FINMIND_URL, params=params, timeout=20)
    r.raise_for_status()
    return pd.DataFrame(r.json().get("data", []))


def get_price_history(stock_id: str, years: int = 3) -> pd.DataFrame:
    start = (datetime.now() - timedelta(days=365 * years + 60)).strftime("%Y-%m-%d")
    df = fetch_finmind("TaiwanStockPrice", stock_id, start)
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    df = df.rename(columns={"max": "high", "min": "low", "Trading_Volume": "volume"})
    for col in ["open", "high", "low", "close", "volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def get_fundamental(stock_id: str) -> dict:
    """近 3 完整年度 EPS、ROE"""
    start = f"{datetime.now().year - 4}-01-01"
    df = fetch_finmind("TaiwanStockFinancialStatements", stock_id, start)
    if df.empty:
        return {"eps": {}, "roe": {}}

    df["date"] = pd.to_datetime(df["date"])
    df["year"] = df["date"].dt.year
    df["value"] = pd.to_numeric(df["value"], errors="coerce")

    eps = df[df["type"] == "EPS"].groupby("year")["value"].sum().to_dict()
    roe = df[df["type"] == "ROE"].groupby("year")["value"].sum().to_dict()

    cy = datetime.now().year
    return {
        "eps": {y: round(v, 2) for y, v in eps.items() if cy - 3 <= y < cy},
        "roe": {y: round(v, 2) for y, v in roe.items() if cy - 3 <= y < cy},
    }


# ============ 技術指標 ============

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


def tech_score_at(row: pd.Series) -> dict:
    """對一天計算技術分 (0-100)"""
    score = 0
    signals = []

    if pd.notna(row["ma20"]) and pd.notna(row["ma60"]):
        if row["close"] > row["ma20"] > row["ma60"]:
            score += 25
            signals.append("均線多頭")
        elif row["close"] > row["ma20"]:
            score += 12

    if pd.notna(row["bb_lower"]) and pd.notna(row["bb_mid"]):
        dist = (row["close"] - row["bb_lower"]) / row["bb_lower"]
        if 0 < dist < 0.03:
            score += 25
            signals.append("布林下軌反彈")
        elif row["close"] < row["bb_mid"]:
            score += 10

    if pd.notna(row["k"]) and pd.notna(row["d"]):
        if row["k"] > row["d"] and row["k"] < 80:
            score += 25
            signals.append("KD黃金交叉")
        elif row["k"] > row["d"]:
            score += 10

    if pd.notna(row["macd_hist"]):
        if row["macd_hist"] > 0 and row["dif"] > row["dea"]:
            score += 25
            signals.append("MACD多頭")
        elif row["macd_hist"] > 0:
            score += 10

    return {"score": score, "signals": signals}


# ============ 回測 ============

def backtest(df: pd.DataFrame) -> dict:
    """對過去 3 年所有技術分 ≥60 的日子做持有 20 日結算"""
    indices = []
    for i in range(60, len(df) - CONFIG["hold_days"]):
        if tech_score_at(df.iloc[i])["score"] >= CONFIG["min_tech_score_for_signal"]:
            indices.append(i)

    if not indices:
        return {"winrate": None, "samples": 0, "avg_return": None}

    wins = losses = 0
    returns = []
    for idx in indices:
        entry = df.iloc[idx]["close"]
        future = df.iloc[idx + 1 : idx + 1 + CONFIG["hold_days"]]
        if len(future) < CONFIG["hold_days"]:
            continue

        hi, lo = future["high"].max(), future["low"].min()
        fc = future.iloc[-1]["close"]

        hit_target = hi >= entry * (1 + CONFIG["target_return"])
        hit_stop = lo <= entry * (1 - CONFIG["stop_loss"])

        if hit_target and not hit_stop:
            wins += 1
            returns.append(CONFIG["target_return"])
        elif hit_stop:
            losses += 1
            returns.append(-CONFIG["stop_loss"])
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


# ============ 評估一檔股票 ============

def evaluate(stock_id: str, name: str) -> Optional[dict]:
    result = {
        "stock_id": stock_id,
        "name": name,
        "date": datetime.now().strftime("%Y-%m-%d"),
        "risk_notes": [],
    }

    try:
        fund = get_fundamental(stock_id)
        eps_vals = list(fund["eps"].values())
        roe_vals = list(fund["roe"].values())
        fund_pass = (
            len(eps_vals) >= 2
            and len(roe_vals) >= 2
            and min(eps_vals) > CONFIG["eps_threshold"]
            and min(roe_vals) > CONFIG["roe_threshold"]
        )

        px = get_price_history(stock_id, CONFIG["backtest_years"])
        if len(px) < 100:
            result["action"] = "SKIP"
            result["risk_notes"].append("價格資料不足")
            return result

        px = add_indicators(px)
        latest = px.iloc[-1]
        ts = tech_score_at(latest)
        bt = backtest(px)

        fund_score = 100 if fund_pass else 40
        tech_score = ts["score"]
        winrate = bt.get("winrate") or 0.5
        bt_score = winrate * 100

        signal_score = round(
            0.3 * fund_score + 0.3 * tech_score + 0.4 * bt_score, 1
        )

        if (
            signal_score >= CONFIG["min_total_score_for_buy"]
            and fund_pass
            and tech_score >= 50
        ):
            action = "BUY"
        elif signal_score >= 50:
            action = "WATCH"
        else:
            action = "SKIP"

        entry = float(latest["close"])
        stop_price = round(entry * (1 - CONFIG["stop_loss"]), 2)
        target_price = round(entry * (1 + CONFIG["target_return"]), 2)
        rr = round(CONFIG["target_return"] / CONFIG["stop_loss"], 2)
        position_pct = min(2.0 / (CONFIG["stop_loss"] * 100) * 100, 20.0)

        if bt.get("samples", 0) < 8:
            result["risk_notes"].append(f"回測樣本僅 {bt.get('samples', 0)} 次，統計弱")
        if not fund_pass:
            result["risk_notes"].append("基本面未過門檻")
        if winrate < 0.5:
            result["risk_notes"].append(f"歷史勝率 {winrate*100:.0f}% 低於五成")
        if pd.notna(latest.get("bb_upper")) and latest["close"] > latest["bb_upper"]:
            result["risk_notes"].append("已突破布林上軌，追高風險")

        # 近期走勢
        chg_5d = (latest["close"] / px.iloc[-6]["close"] - 1) * 100 if len(px) >= 6 else 0
        chg_20d = (latest["close"] / px.iloc[-21]["close"] - 1) * 100 if len(px) >= 21 else 0
        vol_5 = px["volume"].iloc[-5:].mean()
        vol_20 = px["volume"].iloc[-20:].mean()
        vol_ratio = vol_5 / vol_20 if vol_20 > 0 else 1
        high_252 = px["high"].iloc[-252:].max() if len(px) >= 252 else px["high"].max()
        low_252 = px["low"].iloc[-252:].min() if len(px) >= 252 else px["low"].min()
        pct_from_high = (latest["close"] / high_252 - 1) * 100
        above_ma20 = latest["close"] > latest["ma20"] if pd.notna(latest["ma20"]) else False
        above_ma60 = latest["close"] > latest["ma60"] if pd.notna(latest["ma60"]) else False

        result.update({
            "action": action,
            "signal_score": signal_score,
            "components": {
                "fundamental_pass": fund_pass,
                "eps_min": min(eps_vals) if eps_vals else None,
                "roe_min": min(roe_vals) if roe_vals else None,
                "tech_score": tech_score,
                "tech_signals": ts["signals"],
                "backtest_winrate": winrate,
                "backtest_samples": bt.get("samples", 0),
            },
            "trend": {
                "chg_5d": round(chg_5d, 2),
                "chg_20d": round(chg_20d, 2),
                "vol_ratio": round(vol_ratio, 2),
                "pct_from_high": round(pct_from_high, 1),
                "above_ma20": bool(above_ma20),
                "above_ma60": bool(above_ma60),
            },
            "entry_price": entry,
            "stop_loss_price": stop_price,
            "target_price": target_price,
            "risk_reward_ratio": rr,
            "position_size_pct": round(position_pct, 1),
        })
        return result

    except Exception as e:
        result["action"] = "ERROR"
        result["risk_notes"].append(f"錯誤: {str(e)[:80]}")
        return result


# ============ Telegram ============

def send_telegram(text: str):
    url = TELEGRAM_API.format(token=os.environ["TELEGRAM_BOT_TOKEN"])
    payload = {
        "chat_id": os.environ["TELEGRAM_CHAT_ID"],
        "text": text,
        "parse_mode": "Markdown",
    }
    r = requests.post(url, json=payload, timeout=10)
    if not r.ok:
        print(f"Telegram 送失敗: {r.text}", file=sys.stderr)


def _trend_emoji(chg: float) -> str:
    if chg > 3:
        return "🔥"
    elif chg > 0:
        return "📈"
    elif chg > -3:
        return "📉"
    return "💥"


def _format_stock_detail(s: dict, show_trend: bool = True) -> list[str]:
    """格式化單檔股票的詳細資訊"""
    c = s.get("components", {})
    t = s.get("trend", {})
    lines = []
    wr = f"{c['backtest_winrate']*100:.0f}%" if c.get("backtest_winrate") else "N/A"
    fund = "✅" if c.get("fundamental_pass") else "❌"

    lines.append(f"*{s['stock_id']} {s['name']}*  綜合 {s['signal_score']} 分")
    if show_trend and t:
        ma_status = ""
        if t.get("above_ma20") and t.get("above_ma60"):
            ma_status = "站上月季線"
        elif t.get("above_ma20"):
            ma_status = "站上月線"
        else:
            ma_status = "月線下"
        vol_note = f"量能{'放大' if t.get('vol_ratio', 1) > 1.2 else '縮量' if t.get('vol_ratio', 1) < 0.8 else '持平'}"
        lines.append(
            f"{_trend_emoji(t.get('chg_5d', 0))} 5日{t.get('chg_5d', 0):+.1f}% | 20日{t.get('chg_20d', 0):+.1f}% | "
            f"距高點{t.get('pct_from_high', 0):.0f}% | {ma_status} | {vol_note}"
        )
    lines.append(
        f"進場 {s['entry_price']} → 停損 {s['stop_loss_price']} / 目標 {s['target_price']}"
    )
    lines.append(
        f"風報比 1:{s['risk_reward_ratio']} | 建議部位 {s['position_size_pct']}%"
    )
    lines.append(
        f"基本面{fund} | 技術分 {c.get('tech_score', 'N/A')} | 勝率 {wr} ({c.get('backtest_samples', 0)}次)"
    )
    if c.get("tech_signals"):
        lines.append(f"觸發: {', '.join(c['tech_signals'])}")
    if s.get("risk_notes"):
        lines.append(f"⚠️ {' / '.join(s['risk_notes'])}")
    return lines


def _explain_why(s: dict) -> str:
    """解釋為什麼是 BUY / WATCH / SKIP"""
    c = s.get("components", {})
    reasons = []
    if not c.get("fundamental_pass"):
        reasons.append("基本面未達標(EPS>5,ROE>15)")
    if c.get("tech_score", 0) < 50:
        reasons.append(f"技術分僅{c.get('tech_score', 0)}(<50)")
    if s.get("signal_score", 0) < 65:
        reasons.append(f"綜合分{s.get('signal_score', 0)}(<65)")
    if not reasons:
        return "所有條件皆達標"
    return " / ".join(reasons)


def _sector_summary(signals: list[dict], watchlist: list[dict]) -> list[str]:
    """類股強弱分析"""
    cat_map = {str(w["stock_id"]): w.get("category", "其他") for w in watchlist}
    sectors = {}
    for s in signals:
        cat = cat_map.get(s["stock_id"], "其他")
        if cat not in sectors:
            sectors[cat] = {"stocks": [], "chg_5d": [], "buy": 0, "watch": 0}
        sectors[cat]["stocks"].append(s)
        t = s.get("trend", {})
        if t.get("chg_5d") is not None:
            sectors[cat]["chg_5d"].append(t["chg_5d"])
        if s.get("action") == "BUY":
            sectors[cat]["buy"] += 1
        elif s.get("action") == "WATCH":
            sectors[cat]["watch"] += 1

    ranked = sorted(
        sectors.items(),
        key=lambda x: np.mean(x[1]["chg_5d"]) if x[1]["chg_5d"] else 0,
        reverse=True,
    )

    lines = []
    for cat, d in ranked:
        avg = np.mean(d["chg_5d"]) if d["chg_5d"] else 0
        emoji = _trend_emoji(avg)
        total = len(d["stocks"])
        lines.append(
            f"{emoji} *{cat}* ({total}檔) 5日均漲{avg:+.1f}% | "
            f"BUY {d['buy']} WATCH {d['watch']}"
        )
    return lines


def _market_sentiment(signals: list[dict]) -> str:
    """判斷市場氛圍"""
    valid = [s for s in signals if s.get("trend")]
    if not valid:
        return "無法判斷"
    up = sum(1 for s in valid if s["trend"].get("chg_5d", 0) > 0)
    above_ma20 = sum(1 for s in valid if s["trend"].get("above_ma20"))
    pct_up = up / len(valid) * 100
    pct_ma20 = above_ma20 / len(valid) * 100

    if pct_up > 70 and pct_ma20 > 60:
        return "🟢 偏多 — 多數標的上漲且站穩月線，可積極佈局"
    elif pct_up > 50:
        return "🟡 中性偏多 — 漲多跌少但力道分歧，選股不選市"
    elif pct_up > 30:
        return "🟠 中性偏空 — 多數標的走弱，保守觀望為主"
    else:
        return "🔴 偏空 — 普遍下跌，建議空手等待"


def format_messages(signals: list[dict], watchlist: list[dict] = None) -> list[str]:
    """產生多則 Telegram 訊息"""
    buys = [s for s in signals if s.get("action") == "BUY"]
    watches = [s for s in signals if s.get("action") == "WATCH"]
    skips = [s for s in signals if s.get("action") in ("SKIP", "ERROR")]
    today = datetime.now().strftime("%Y/%m/%d")
    total = len(signals)
    messages = []

    # === 第一則：市場總覽 + 類股強弱 ===
    msg1 = []
    msg1.append(f"📊 *V3.0 每日選股報告* {today}")
    msg1.append(f"掃描 {total} 檔 | BUY {len(buys)} | WATCH {len(watches)} | SKIP {len(skips)}")
    msg1.append("")

    msg1.append("🌡️ *市場氛圍*")
    msg1.append(_market_sentiment(signals))
    valid = [s for s in signals if s.get("trend")]
    if valid:
        avg_5d = np.mean([s["trend"]["chg_5d"] for s in valid])
        up_count = sum(1 for s in valid if s["trend"]["chg_5d"] > 0)
        above_ma20 = sum(1 for s in valid if s["trend"]["above_ma20"])
        msg1.append(
            f"池內均漲 {avg_5d:+.1f}% | {up_count}/{len(valid)} 檔上漲 | "
            f"{above_ma20}/{len(valid)} 檔站上月線"
        )
    msg1.append("")

    if watchlist:
        msg1.append("📡 *類股強弱排名*")
        msg1.extend(_sector_summary(signals, watchlist))
        msg1.append("")

    msg1.append("📋 *策略規則*")
    msg1.append(
        "基本面(EPS>5,ROE>15) + 技術面(均線/布林/KD/MACD) + 3年回測\n"
        f"綜合 = 基本面30% + 技術30% + 回測40%\n"
        f"BUY≥65(三關全過) | WATCH≥50\n"
        f"停損{CONFIG['stop_loss']*100:.0f}% / 停利{CONFIG['target_return']*100:.0f}% / 持有{CONFIG['hold_days']}日"
    )
    messages.append("\n".join(msg1))

    # === 第二則：BUY 詳細 ===
    msg2 = []
    if buys:
        msg2.append(f"🟢 *BUY — 建議進場 ({len(buys)})*")
        msg2.append("")
        for s in buys:
            msg2.extend(_format_stock_detail(s))
            msg2.append(f"💡 為何買: {_explain_why(s)}")
            msg2.append("")
    else:
        msg2.append("🟢 *BUY: 今日無符合全部條件的標的*")
        msg2.append("（需基本面+技術面+回測三關全過）")
        msg2.append("")

    # WATCH TOP 8
    if watches:
        top_watches = watches[:8]
        rest_watches = watches[8:]
        msg2.append(f"🟡 *WATCH — 接近訊號 TOP {len(top_watches)}*")
        msg2.append("")
        for s in top_watches:
            msg2.extend(_format_stock_detail(s))
            msg2.append(f"❓ 差在: {_explain_why(s)}")
            msg2.append("")

        if rest_watches:
            msg2.append(f"📎 *其他觀察 ({len(rest_watches)})*")
            rest_line = ", ".join(
                [f"{s['stock_id']}{s['name']}({s['signal_score']})" for s in rest_watches]
            )
            msg2.append(rest_line)
            msg2.append("")
    messages.append("\n".join(msg2))

    # === 第三則：操作建議總結 ===
    msg3 = []
    msg3.append("🧠 *今日操作建議*")
    msg3.append("")

    # 最值得關注的 2-3 檔
    focus = (buys + watches)[:3]
    if focus:
        msg3.append("🔑 *最值得關注*")
        for s in focus:
            c = s.get("components", {})
            t = s.get("trend", {})
            reason_parts = []
            if c.get("tech_signals"):
                reason_parts.append(f"技術面出現{'/'.join(c['tech_signals'])}")
            if t.get("chg_5d", 0) > 0 and t.get("vol_ratio", 1) > 1.2:
                reason_parts.append("帶量上攻")
            if t.get("above_ma20") and t.get("above_ma60"):
                reason_parts.append("多頭排列")
            if c.get("backtest_winrate", 0) >= 0.6:
                reason_parts.append(f"歷史勝率{c['backtest_winrate']*100:.0f}%")
            reason = "，".join(reason_parts) if reason_parts else "綜合分數領先"
            msg3.append(
                f"• *{s['stock_id']} {s['name']}* ({s['action']}, {s['signal_score']}分)"
            )
            msg3.append(f"  {reason}")
            msg3.append(
                f"  若進場: 進 {s['entry_price']} → 損 {s['stop_loss_price']} / 標 {s['target_price']}"
            )
            msg3.append("")

    # 整體操作方向
    msg3.append("📌 *操作方向*")
    sentiment = _market_sentiment(signals)
    if "偏多" in sentiment and "中性" not in sentiment:
        msg3.append("• 市場偏多，可挑選技術面強勢股分批進場")
        msg3.append("• 優先選回測勝率>60%、站穩月線的標的")
    elif "偏多" in sentiment:
        msg3.append("• 市場中性偏多，選股不選市")
        msg3.append("• 等拉回月線支撐再找買點，不追高")
    elif "偏空" in sentiment and "中性" not in sentiment:
        msg3.append("• 市場偏空，建議空手觀望")
        msg3.append("• 等止跌訊號出現再考慮進場")
    else:
        msg3.append("• 市場中性偏空，控制總部位在半倉以下")
        msg3.append("• 只做高勝率、風報比好的機會")
    msg3.append("")
    msg3.append("_以上為系統自動分析，僅供參考，投資決策請自行判斷_")
    messages.append("\n".join(msg3))

    return messages


def format_message(signals: list[dict]) -> str:
    """向後相容"""
    return format_messages(signals)[0]


# ============ 主程式 ============

def main():
    required = ["FINMIND_TOKEN", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
                "GOOGLE_SHEET_ID", "GOOGLE_CREDS_JSON"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        print(f"❌ 缺少環境變數: {missing}", file=sys.stderr)
        sys.exit(1)

    print(f"[{datetime.now()}] 讀取 watchlist...")
    watchlist = read_watchlist()
    print(f"  → {len(watchlist)} 檔啟用中")

    results = []
    for i, row in enumerate(watchlist, 1):
        sid = str(row["stock_id"])
        name = row.get("name", "")
        print(f"[{i}/{len(watchlist)}] {sid} {name}")
        r = evaluate(sid, name)
        if r:
            results.append(r)
        time.sleep(0.6)

    # 排序：BUY > WATCH > SKIP
    order = {"BUY": 0, "WATCH": 1, "SKIP": 2, "ERROR": 3}
    results.sort(key=lambda x: (order.get(x.get("action"), 4), -x.get("signal_score", 0)))

    print(f"\n{sum(1 for r in results if r['action']=='BUY')} BUY, "
          f"{sum(1 for r in results if r['action']=='WATCH')} WATCH")

    print("寫回 Google Sheet...")
    append_signals(results)

    print("發送 Telegram...")
    for msg in format_messages(results, watchlist):
        send_telegram(msg)
        time.sleep(0.5)

    print("✅ 完成")


if __name__ == "__main__":
    main()
