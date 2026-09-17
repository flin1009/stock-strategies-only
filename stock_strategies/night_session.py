"""夜盤盤前參考

抓台指期 (TX) 夜盤 (after_market session) 的近月合約，計算昨晚整段夜盤的
漲跌幅，作為「今日開盤方向」的領先預判。

夜盤交易時段為 15:00 ~ 隔日 05:00，FinMind 以「結束日」標記該段 session，
因此早上 08:00 排程跑時可抓到最近一筆完整夜盤（週一會自動抓到上週五夜盤）。
"""

import os
from datetime import datetime, timedelta

import pandas as pd
import requests

from .config import FINMIND_URL, FUTURES_ID, CONFIG


def _fetch_futures(days: int = 14) -> pd.DataFrame:
    """抓最近 N 天台指期日資料（含日盤與夜盤兩種 trading_session）。"""
    start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    token = os.environ.get("FINMIND_TOKEN", "")
    r = requests.get(
        FINMIND_URL,
        params={
            "dataset": "TaiwanFuturesDaily",
            "data_id": FUTURES_ID,
            "start_date": start,
            "token": token,
        },
        timeout=20,
    )
    r.raise_for_status()
    return pd.DataFrame(r.json().get("data", []))


def classify_gap(pct: float) -> dict:
    """依夜盤漲跌幅分類開盤方向預判。回傳 label / emoji / direction / bias。"""
    big = CONFIG.get("night_gap_big", 1.5)
    small = CONFIG.get("night_gap_small", 0.5)
    if pct >= big:
        return {
            "label": "大漲", "emoji": "🚀", "bias": "bull",
            "direction": "今日開盤偏多，留意開高走低、別追高",
        }
    if pct >= small:
        return {
            "label": "小漲", "emoji": "🟢", "bias": "bull",
            "direction": "今日開盤偏多，可留意強勢股表態",
        }
    if pct > -small:
        return {
            "label": "平盤", "emoji": "⚪", "bias": "flat",
            "direction": "夜盤波動有限，開盤方向中性，看量價表態",
        }
    if pct > -big:
        return {
            "label": "小跌", "emoji": "🟠", "bias": "bear",
            "direction": "今日開盤偏弱，等止穩再進場",
        }
    return {
        "label": "大跌", "emoji": "🔴", "bias": "bear",
        "direction": "夜盤重挫，開盤恐跳空走弱，謹慎觀望",
    }


def tailwind_tag(bias: str) -> str:
    """夜盤對多方訊號 (BUY/WATCH) 的順風/逆風短標籤。"""
    return {"bull": "夜盤順風🟢", "bear": "夜盤逆風🔴"}.get(bias, "夜盤中性⚪")


def bias_guidance(bias: str) -> str:
    """夜盤偏向對應的一句操作提示。"""
    if bias == "bull":
        return "夜盤偏多 — 回檔承接優於追高，開高別追、留意開高走低"
    if bias == "bear":
        return "夜盤偏弱 — 等開盤止穩再進、嚴設停損，空手者可先觀望"
    return "夜盤中性 — 開盤看量價表態，選股不選市"


def night_filter_note(night: dict | None) -> str:
    """產生 14:30 選股報告用的「夜盤濾鏡」說明行。"""
    if not night:
        return "⚠️ 夜盤資料取得失敗，未套用夜盤濾鏡"
    big = CONFIG.get("night_gap_big", 1.5)
    tag = {"bull": "順風", "bear": "逆風", "flat": "中性"}[night["bias"]]
    base = (
        f"{night['emoji']} 昨晚夜盤 {night['pct']:+.2f}%"
        f"（{night['label']}・{tag}）"
    )
    if night["bias"] == "bear" and night["pct"] <= -big:
        return base + "，BUY 全數降為 WATCH（情緒保守）"
    if night["bias"] == "bear":
        return base + "，BUY 保留但標逆風、留意開盤承壓"
    if night["bias"] == "bull":
        return base + "，順風環境、BUY 照常"
    return base + "，方向中性"


def apply_night_filter(results: list[dict], night: dict | None) -> int:
    """夜盤情緒風控濾鏡（套用在 main.py 的選股結果上）。

    定位：14:30 拿到的是「昨晚」夜盤，已反映在今日收盤，因此這是
    風險管理用的情緒濾鏡（夜盤重挫 → 隔日選股轉保守），非精準開盤預測。
    精準的「夜盤預測今日開盤」由早上 08:00 的 premarket.py 負責。

    規則：
        - 昨晚夜盤大跌（≤ -night_gap_big）→ BUY 降為 WATCH + 風險註記
        - 小跌（逆風）→ BUY/WATCH 保留，加逆風註記
        - 順風 / 平盤 → 不加個股註記（僅在報告標頭顯示濾鏡狀態）

    回傳被降級的 BUY 數量。
    """
    if not night:
        return 0
    big = CONFIG.get("night_gap_big", 1.5)
    pct = night["pct"]
    downgraded = 0
    if night["bias"] == "bear" and pct <= -big:
        for r in results:
            if r.get("action") == "BUY":
                r["action"] = "WATCH"
                r.setdefault("risk_notes", []).append(
                    f"昨晚夜盤大跌 {pct:+.1f}%，自動降為 WATCH"
                )
                downgraded += 1
    elif night["bias"] == "bear":
        for r in results:
            if r.get("action") in ("BUY", "WATCH"):
                r.setdefault("risk_notes", []).append(
                    f"昨晚夜盤逆風 {pct:+.1f}%，留意開盤承壓"
                )
    return downgraded


def get_night_session() -> dict | None:
    """回傳最近一筆台指期夜盤近月資訊；抓不到回 None。

    回傳 dict 欄位：
        date, contract, open, high, low, close,
        spread (漲跌點數), pct (漲跌幅 %), volume,
        label, emoji, bias, direction
    """
    try:
        df = _fetch_futures()
    except Exception as e:
        print(f"[night] 夜盤資料抓取失敗: {str(e)[:80]}")
        return None

    if df.empty or "trading_session" not in df.columns:
        return None

    night = df[df["trading_session"] == "after_market"].copy()
    if night.empty:
        return None

    # 排除價差組合單（contract_date 形如 "202606/202607"）
    night = night[~night["contract_date"].astype(str).str.contains("/")]
    if night.empty:
        return None

    for col in ["open", "max", "min", "close", "spread", "spread_per", "volume"]:
        if col in night.columns:
            night[col] = pd.to_numeric(night[col], errors="coerce")

    night["date"] = pd.to_datetime(night["date"])
    latest_date = night["date"].max()
    latest = night[night["date"] == latest_date]

    # 近月 = 該日成交量最大那筆（最活躍合約，避開已轉倉的遠月）
    row = latest.sort_values("volume", ascending=False).iloc[0]

    pct = float(row.get("spread_per") or 0.0)
    gap = classify_gap(pct)
    return {
        "date": latest_date.strftime("%Y-%m-%d"),
        "contract": str(row.get("contract_date", "")),
        "open": float(row.get("open") or 0.0),
        "high": float(row.get("max") or 0.0),
        "low": float(row.get("min") or 0.0),
        "close": float(row.get("close") or 0.0),
        "spread": float(row.get("spread") or 0.0),
        "pct": pct,
        "volume": int(row.get("volume") or 0),
        **gap,
    }
