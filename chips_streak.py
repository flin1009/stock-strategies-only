"""全市場三大法人連續買超晚報掃描器

執行時機：每週一至週五 20:00 (台股盤後籌碼完全結算)
資料來源：證交所 (TWSE) + 櫃買中心 (TPEx) 官方開放資料 (全市場 2,200+ 檔)
功能：全市場掃描連續買超 >= 3 天的標的，比對自選股 (Watchlist)，
     計算連買天數與每日漲幅，寫入 Google Sheet (Chips_Streak) 並發送 Telegram 晚報。

執行: uv run python chips_streak.py
"""

import os
import sys
from datetime import datetime

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from stock_strategies.sheet import read_watchlist, write_chips_streak
from stock_strategies.chips_scanner import scan_market_chips_streak
from stock_strategies.notify import send_telegram, format_chips_streak


if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


REQUIRED_ENV = [
    "FINMIND_TOKEN",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "GOOGLE_SHEET_ID",
    "GOOGLE_CREDS_JSON",
]


def main():
    missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
    if missing:
        print(f"❌ 缺少環境變數: {missing}", file=sys.stderr)
        sys.exit(1)

    print(f"[{datetime.now()}] 讀取 Watchlist 自選名單...")
    try:
        watchlist = read_watchlist()
    except Exception as e:
        print(f"⚠️ 讀取 Watchlist 失敗: {e}", file=sys.stderr)
        watchlist = []

    print(f"  → 自選名單共 {len(watchlist)} 檔啟用中")

    print("啟動證交所/櫃買中心全市場 (2,200+ 檔) 三大法人連買掃描...")
    matched = scan_market_chips_streak(
        watchlist=watchlist,
        min_streak=3,
        max_market_candidates=60,
        delay_sec=0.03,
    )
    wl_matched = [r for r in matched if r.get("is_watchlist")]
    mkt_matched = [r for r in matched if not r.get("is_watchlist")]

    print(f"  → 掃描完成！共納入 {len(matched)} 檔標的")
    print(f"    • 自選股達標: {len(wl_matched)} 檔")
    for r in wl_matched:
        print(
            f"      ⭐ {r['stock_id']} {r['name']}: 連買 {r['main_streak']} 天 | "
            f"累計買超 {r['streak_total_net_lots']:,} 張 | 累計漲幅 {r['streak_pct']:+.2f}%"
        )

    print(f"    • 全市場精選: {len(mkt_matched)} 檔 (顯示前 5 檔範例)")
    for r in mkt_matched[:5]:
        print(
            f"      - {r['stock_id']} {r['name']}: 連買 {r['main_streak']} 天 | "
            f"累計買超 {r['streak_total_net_lots']:,} 張 | 累計漲幅 {r['streak_pct']:+.2f}%"
        )

    print("寫入 Google Sheet (Chips_Streak 分頁)...")
    try:
        write_chips_streak(matched)
        print("  → 寫入 Google Sheet 完成")
    except Exception as e:
        print(f"⚠️ 寫入 Google Sheet 失敗: {e}", file=sys.stderr)

    print("發送 Telegram 晚報推播...")
    try:
        msg = format_chips_streak(matched)
        send_telegram(msg)
        print("  → Telegram 推播發送成功")
    except Exception as e:
        print(f"⚠️ 發送 Telegram 失敗: {e}", file=sys.stderr)

    print("✅ 全流程完成")


if __name__ == "__main__":
    main()
