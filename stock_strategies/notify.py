import os
import sys
from datetime import datetime

import numpy as np
import requests

from .config import CONFIG, TELEGRAM_API


def send_telegram(text: str):
    url = TELEGRAM_API.format(token=os.environ["TELEGRAM_BOT_TOKEN"])
    payload = {
        "chat_id": os.environ["TELEGRAM_CHAT_ID"],
        "text": text,
        "parse_mode": "Markdown",
    }
    r = requests.post(url, json=payload, timeout=10)
    if not r.ok:
        # 若 Markdown 解析失敗 (400)，改以純文字重發，確保訊息必定送達
        if "can't parse entities" in r.text or r.status_code == 400:
            payload.pop("parse_mode", None)
            r2 = requests.post(url, json=payload, timeout=10)
            if r2.ok:
                return
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
        f"📌 *明日開盤進場* | 參考價 {s['entry_price']}"
    )
    lines.append(
        f"停損 {s['stop_loss_price']} (-{CONFIG['stop_loss']*100:.0f}%) / "
        f"目標 {s['target_price']} (+{CONFIG['target_return']*100:.0f}%)"
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
        reasons.append("基本面未達標(EPS>2,ROE>15)")
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


def format_messages(
    signals: list[dict],
    watchlist: list[dict] = None,
    market: dict = None,
    night_note: str = None,
) -> list[str]:
    """產生精實、無重複、一頁式的 Telegram 每日選股決策晚報（瘦身版）。"""
    buys = [s for s in signals if s.get("action") == "BUY"]
    watches = [s for s in signals if s.get("action") == "WATCH"]
    today = datetime.now().strftime("%Y/%m/%d")
    total = len(signals)

    lines = []
    lines.append(f"📊 *V3.2 每日選股決策晚報* {today}")
    lines.append(f"池內 {total} 檔 | BUY {len(buys)} 檔 | WATCH {len(watches)} 檔")

    # 1. 市場與風控濾鏡總結
    filter_status = []
    if market and market.get("note"):
        filter_status.append(market["note"])
    if night_note:
        filter_status.append(night_note)
    if filter_status:
        lines.append("🎯 " + " · ".join(filter_status))

    sentiment = _market_sentiment(signals)
    lines.append(f"🌡️ 市場氛圍: {sentiment.split('—')[0].strip()}")
    lines.append("")

    # 2. 🟢 BUY — 建議進場標的
    lines.append(f"🟢 *【BUY — 建議進場】* ({len(buys)} 檔)")
    if buys:
        for s in buys:
            c = s.get("components", {})
            t = s.get("trend", {})
            tech_sigs = "/".join(c.get("tech_signals", [])) or "多頭指標"
            vp = " + ".join(c.get("volume_patterns", [])) or "量價穩健"
            chips_note = c.get("chips_summary", "")
            lines.append(f"• *{s['stock_id']} {s['name']}* (綜合 {s['signal_score']}分 | 技術 {c.get('tech_score', 0)}分)")
            lines.append(f"  收盤 {s.get('entry_price')} ({t.get('chg_5d', 0):+.1f}% 5日) | 訊號: {tech_sigs}")
            lines.append(f"  量能: {vp}" + (f" | 籌碼: {chips_note}" if chips_note else ""))
            lines.append(f"  🎯 開盤進場 → 損 {s['stop_loss_price']} (-8%) / 標 {s['target_price']} (+10%)")
            if s.get("risk_notes"):
                lines.append(f"  ⚠️ 提醒: {' / '.join(s['risk_notes'])}")
            lines.append("")
    else:
        lines.append("• 今日無完全符合條件標的（嚴格風控，建議保守空手）\n")

    # 3. 🟡 WATCH — 接近訊號精選 (TOP 5)
    top_watches = watches[:5]
    rest_watches = watches[5:]
    lines.append(f"🟡 *【WATCH — 接近訊號精選】* (TOP {len(top_watches)})")
    if top_watches:
        for s in top_watches:
            c = s.get("components", {})
            lines.append(f"• *{s['stock_id']} {s['name']}* (綜合 {s['signal_score']}分) — 收盤 {s.get('entry_price')}")
            lines.append(f"  ↳ 差在: {_explain_why(s)}")
            lines.append(f"  ↳ 參考防守價: {s['stop_loss_price']} (-8%)")
            lines.append("")
        if rest_watches:
            rest_strs = [f"{s['stock_id']}{s['name']}({s['signal_score']})" for s in rest_watches]
            lines.append(f"📎 *其餘觀察 ({len(rest_watches)} 檔)*: {', '.join(rest_strs)}")
            lines.append("")
    else:
        lines.append("• 今日無觀察標的\n")

    # 4. ⚠️ 異常量價 / 風險警戒
    danger_stocks = [
        s for s in signals if "放量滯漲" in s.get("components", {}).get("volume_patterns", [])
    ]
    if danger_stocks:
        lines.append(f"⚠️ *【量能警示 — 放量滯漲】* ({len(danger_stocks)} 檔)")
        for s in danger_stocks:
            lines.append(f"• *{s['stock_id']} {s['name']}*: 高檔爆量但收黑或留長上影，慎防主力出貨")
        lines.append("")

    # 5. 操作叮嚀
    lines.append("📌 *操作叮嚀*")
    if "偏多" in sentiment and "中性" not in sentiment:
        lines.append("• 市場強勢多頭，可順勢布局 BUY 標的，嚴守 8% 停損")
    elif "偏多" in sentiment:
        lines.append("• 市場中性偏多，選股不選市，拉回月線有守再承接")
    elif "偏空" in sentiment:
        lines.append("• 市場走弱，嚴格控制總倉位在 3 成以下，多看少做")
    else:
        lines.append("• 市場分歧，謹慎操作，以風報比佳之個股為主")

    lines.append("")
    lines.append("💡 _完整評分、歷史回測與個股指標已同步寫入 Google Sheet Signals 分頁_")

    full_text = "\n".join(lines)
    return [full_text]


def _format_deep_analysis(signals: list[dict], today: str) -> str:
    """量價陣列深度解析（V3.1）"""
    lines = [f"🔬 *量價深度解析* {today}", ""]

    buys = [s for s in signals if s.get("action") == "BUY"]
    watches = [s for s in signals if s.get("action") == "WATCH"]

    has_patterns = lambda s: bool(s.get("components", {}).get("volume_patterns"))
    has_danger = lambda s: "放量滯漲" in s.get("components", {}).get("volume_patterns", [])

    danger_stocks = [s for s in signals if has_danger(s)]

    if buys:
        lines.append("🟢 *BUY 深度解析*")
        lines.append("")
        for s in buys:
            lines.extend(_format_volume_block(s))
            lines.append("")

    interesting_watches = [s for s in watches if has_patterns(s)]
    if interesting_watches:
        lines.append(f"🟡 *WATCH 量價解讀 ({len(interesting_watches)})*")
        lines.append("")
        for s in interesting_watches[:8]:
            lines.extend(_format_volume_block(s))
            lines.append("")

    if danger_stocks:
        lines.append("⚠️ *風險警示 — 放量滯漲*")
        lines.append("")
        for s in danger_stocks:
            if s.get("action") in ("BUY", "WATCH") and has_patterns(s):
                continue
            lines.append(
                f"• *{s['stock_id']} {s['name']}* ({s.get('action', '—')})"
            )
            c = s.get("components", {})
            details = c.get("volume_details", {})
            if "放量滯漲" in details:
                lines.append(f"  ↳ {details['放量滯漲']}")
            lines.append(f"  {c.get('volume_verdict', '')}")
            lines.append("")

    if not buys and not interesting_watches and not danger_stocks:
        lines.append("_今日無顯著量價訊號_")
        lines.append("")

    lines.append("📖 *V3.1 量價字典速查*")
    lines.append("• 倍量柱 = 今日量 ≥ 昨日 2x（主力點火）")
    lines.append("• 梯量柱 = 連續 3 日量能遞增（健康上攻）")
    lines.append("• 縮量柱 = 下跌時量能遞減（洗盤，主力未退）")
    lines.append("• 低量柱 = 極限窒息量（拋壓耗盡）")
    lines.append("• 放量滯漲 = 高檔爆量但 K 收黑（主力倒貨）")

    return "\n".join(lines)


def _format_volume_block(s: dict) -> list[str]:
    """格式化單檔股票的量價區塊"""
    c = s.get("components", {})
    patterns = c.get("volume_patterns", [])
    details = c.get("volume_details", {})
    verdict = c.get("volume_verdict", "")

    lines = [f"• *{s['stock_id']} {s['name']}* ({s.get('action')}, {s['signal_score']}分)"]

    if patterns:
        lines.append(f"  量能陣列: {' + '.join(patterns)}")
        for p in patterns:
            if p in details:
                lines.append(f"  ↳ {details[p]}")
    else:
        lines.append("  量能陣列: 無特殊型態")

    if verdict:
        lines.append(f"  結論: {verdict}")
    return lines


def format_premarket(night: dict | None, signals: list[dict]) -> str:
    """夜盤盤前快報：夜盤方向預判 + 疊加昨日 BUY/WATCH 訊號。

    night   — night_session.get_night_session() 的回傳（可能為 None）
    signals — sheet.read_latest_signals() 的回傳（Sheet 扁平 dict，最新在最前）
    """
    from .night_session import tailwind_tag, bias_guidance

    today = datetime.now()
    wd = "一二三四五六日"[today.weekday()]
    lines = [f"🌙 *夜盤盤前快報* {today.strftime('%Y/%m/%d')} (週{wd})", ""]

    # === 夜盤方向預判 ===
    if night:
        lines.append(
            f"{night['emoji']} *台指期夜盤 {night['pct']:+.2f}% "
            f"({night['spread']:+.0f} 點)*"
        )
        lines.append(f"近月收 {night['close']:.0f} | 量 {night['volume']:,}")
        if night["date"] != today.strftime("%Y-%m-%d"):
            lines.append(f"_（資料時間：{night['date']} 夜盤）_")
        lines.append(f"📈 開盤方向預判：*{night['label']}* → {night['direction']}")
    else:
        lines.append("⚠️ 夜盤資料暫時取不到，今日盤前以個股訊號為主")
    lines.append("")

    # === 疊加昨日訊號 ===
    bias = night["bias"] if night else "flat"
    tag = tailwind_tag(bias)
    actionable = [
        s for s in signals
        if str(s.get("action", "")).upper() in ("BUY", "WATCH")
    ]
    if actionable:
        latest_day = actionable[0].get("date", "")  # 最新在最前
        batch = [s for s in actionable if s.get("date", "") == latest_day]
        buys = [s for s in batch if str(s["action"]).upper() == "BUY"]
        watches = [s for s in batch if str(s["action"]).upper() == "WATCH"]
        lines.append(f"📋 *昨日訊號 × 夜盤對照* ({latest_day})")
        for s in (buys + watches)[:12]:
            act = str(s["action"]).upper()
            dot = "🟢" if act == "BUY" else "🟡"
            lines.append(
                f"{dot} {act} {s.get('stock_id', '')} {s.get('name', '')} "
                f"{s.get('signal_score', '')}分 · {tag}"
            )
        lines.append(f"↳ _{bias_guidance(bias)}_")
    else:
        lines.append("📋 昨日無 BUY/WATCH 訊號（或尚未跑過選股）")
    lines.append("")

    lines.append("💡 _夜盤僅領先參考，開盤後仍以實際量價為準_")
    return "\n".join(lines)


def format_message(signals: list[dict]) -> str:
    """向後相容"""
    return format_messages(signals)[0]


def format_chips_streak(
    buy_records: list[dict] | dict = None,
    sell_records: list[dict] = None,
) -> str:
    """三大法人連續買超與連賣全市場多空晚報推播格式"""
    if isinstance(buy_records, dict) and "buy_records" in buy_records:
        sell_records = buy_records.get("sell_records", [])
        buy_records = buy_records.get("buy_records", [])
    elif buy_records is None:
        buy_records = []
    if sell_records is None:
        sell_records = []

    today = datetime.now()
    wd = "一二三四五六日"[today.weekday()]
    lines = [
        f"🔥 *全市場三大法人多空晚報* {today.strftime('%Y/%m/%d')} (週{wd})",
        f"全市場 2,200+ 檔上市櫃初篩 | 連買 {len(buy_records)} 檔 | 連賣 {len(sell_records)} 檔",
        "",
    ]

    # === 🟢 【多方 — 法人連買強勢】 ===
    lines.append("🟢 *【多方 — 法人連買強勢】*")
    wl_buys = [r for r in buy_records if r.get("is_watchlist")]
    mkt_buys = [r for r in buy_records if not r.get("is_watchlist")]

    lines.append(f"⭐ *自選股連買追蹤* (共 {len(wl_buys)} 檔達標)")
    if wl_buys:
        for r in wl_buys:
            tags_str = f" ({'/'.join(r['tags'])})" if r.get("tags") else ""
            lines.append(
                f"• *{r['stock_id']} {r['name']}*{tags_str} — 連買 *{r['main_streak']}* 天"
            )
            sub_info = []
            if r.get("foreign_streak", 0) > 0:
                sub_info.append(f"外資連{r['foreign_streak']}")
            if r.get("trust_streak", 0) > 0:
                sub_info.append(f"投信連{r['trust_streak']}")
            if r.get("total_streak", 0) > 0:
                sub_info.append(f"合計連{r['total_streak']}")
            tr_ratio = r.get("trust_ratio", 0.0)
            ratio_str = f" (投信佔股本 {tr_ratio:+.2f}%)" if tr_ratio != 0 else ""
            lines.append(f"  買超: {', '.join(sub_info)} | 累計 {r['streak_total_net_lots']:,} 張{ratio_str}")
            recent_pcts = " / ".join(r.get("recent_daily_pcts", []))
            lines.append(
                f"  收盤 {r['today_close']} ({r['today_pct']:+.2f}%) | 期間累計 {r['streak_pct']:+.2f}%"
            )
            if recent_pcts:
                lines.append(f"  ↳ 近日漲幅: {recent_pcts}")
            lines.append("")
    else:
        lines.append("📋 今日自選股中無連續買超 ≥ 3 天標的。")
        lines.append("")

    top_mkt_buys = mkt_buys[:5]
    if top_mkt_buys:
        lines.append(f"🚀 *全市場連買精選* (TOP {len(top_mkt_buys)})")
        for r in top_mkt_buys:
            tags_str = f" ({'/'.join(r['tags'])})" if r.get("tags") else ""
            lines.append(
                f"• *{r['stock_id']} {r['name']}*{tags_str} — 連買 *{r['main_streak']}* 天"
            )
            sub_info = []
            if r.get("foreign_streak", 0) > 0:
                sub_info.append(f"外資連{r['foreign_streak']}")
            if r.get("trust_streak", 0) > 0:
                sub_info.append(f"投信連{r['trust_streak']}")
            tr_ratio = r.get("trust_ratio", 0.0)
            ratio_str = f" (投信佔股本 {tr_ratio:+.2f}%)" if tr_ratio != 0 else ""
            lines.append(f"  買超: {', '.join(sub_info)} | 累計 {r['streak_total_net_lots']:,} 張{ratio_str}")
            recent_pcts = " / ".join(r.get("recent_daily_pcts", []))
            lines.append(
                f"  收盤 {r['today_close']} ({r['today_pct']:+.2f}%) | 期間累計 {r['streak_pct']:+.2f}%"
            )
            if recent_pcts:
                lines.append(f"  ↳ 近日漲幅: {recent_pcts}")
            lines.append("")

    # === 🔴 【空方 — 法人連賣避險警示】 ===
    lines.append("🔴 *【空方 — 法人連賣避險警示】*")
    wl_sells = [r for r in sell_records if r.get("is_watchlist")]
    mkt_sells = [r for r in sell_records if not r.get("is_watchlist")]

    lines.append(f"⚠️ *自選股連賣警戒* (共 {len(wl_sells)} 檔遭調節)")
    if wl_sells:
        for r in wl_sells:
            tags_str = f" ({'/'.join(r['tags'])})" if r.get("tags") else ""
            lines.append(
                f"• *{r['stock_id']} {r['name']}*{tags_str} — 連賣 *{r['main_streak']}* 天"
            )
            sub_info = []
            if r.get("foreign_streak", 0) > 0:
                sub_info.append(f"外資連{r['foreign_streak']}")
            if r.get("trust_streak", 0) > 0:
                sub_info.append(f"投信連{r['trust_streak']}")
            if r.get("total_streak", 0) > 0:
                sub_info.append(f"合計連{r['total_streak']}")
            tr_ratio = r.get("trust_ratio", 0.0)
            ratio_str = f" (投信佔股本 {tr_ratio:+.2f}%)" if tr_ratio != 0 else ""
            lines.append(f"  賣超: {', '.join(sub_info)} | 累計 {r['streak_total_net_lots']:,} 張{ratio_str}")
            recent_pcts = " / ".join(r.get("recent_daily_pcts", []))
            lines.append(
                f"  收盤 {r['today_close']} ({r['today_pct']:+.2f}%) | 期間累計 {r['streak_pct']:+.2f}%"
            )
            if recent_pcts:
                lines.append(f"  ↳ 近日跌幅: {recent_pcts}")
            lines.append("")
    else:
        lines.append("📋 今日自選股中無連續賣超 ≥ 3 天標的，持股籌碼安全。")
        lines.append("")

    top_mkt_sells = mkt_sells[:5]
    if top_mkt_sells:
        lines.append(f"❄️ *全市場連賣提款榜* (TOP {len(top_mkt_sells)})")
        for r in top_mkt_sells:
            tags_str = f" ({'/'.join(r['tags'])})" if r.get("tags") else ""
            lines.append(
                f"• *{r['stock_id']} {r['name']}*{tags_str} — 連賣 *{r['main_streak']}* 天"
            )
            sub_info = []
            if r.get("foreign_streak", 0) > 0:
                sub_info.append(f"外資連{r['foreign_streak']}")
            if r.get("trust_streak", 0) > 0:
                sub_info.append(f"投信連{r['trust_streak']}")
            tr_ratio = r.get("trust_ratio", 0.0)
            ratio_str = f" (投信佔股本 {tr_ratio:+.2f}%)" if tr_ratio != 0 else ""
            lines.append(f"  賣超: {', '.join(sub_info)} | 累計 {r['streak_total_net_lots']:,} 張{ratio_str}")
            recent_pcts = " / ".join(r.get("recent_daily_pcts", []))
            lines.append(
                f"  收盤 {r['today_close']} ({r['today_pct']:+.2f}%) | 期間累計 {r['streak_pct']:+.2f}%"
            )
            if recent_pcts:
                lines.append(f"  ↳ 近日跌幅: {recent_pcts}")
            lines.append("")

    lines.append("💡 完整多空名單已同步寫入 Google Sheet Chips\\_Streak 與 Chips\\_Sell\\_Streak 分頁")
    return "\n".join(lines)




