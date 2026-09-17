"""三大法人連續買超早報掃描器

執行時機：每週一至週五 08:00 (台股開盤前)
功能：掃描 Watchlist 中的股票，篩選三大法人連續買超 >= 3 天的標的，
     計算連買天數與期間每日漲幅，寫入 Google Sheet (Chips_Streak) 並發送 Telegram 推播。

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
from stock_strategies.chips_scanner import scan_watchlist_chips
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

    print(f"[{datetime.now()}] 讀取 Watchlist 觀察名單...")
    try:
        watchlist = read_watchlist()
    except Exception as e:
        print(f"❌ 讀取 Watchlist 失敗: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"  → 共 {len(watchlist)} 檔啟用中")
    if not watchlist:
        print("⚠️ Watchlist 為空或無啟用標的，結束執行。")
        return

    print("開始掃描三大法人連續買超標的...")
    matched = scan_watchlist_chips(watchlist, min_streak=3, delay_sec=0.15)
    print(f"  → 掃描完成，共 {len(matched)} 檔符合連續買超 >= 3 天條件")

    for r in matched:
        print(
            f"    - {r['stock_id']} {r['name']}: 連買 {r['main_streak']} 天 | "
            f"累計買超 {r['streak_total_net_lots']:,} 張 | 累計漲幅 {r['streak_pct']:+.2f}%"
        )


    print("寫入 Google Sheet (Chips_Streak 分頁)...")
    try:
        write_chips_streak(matched)
        print("  → 寫入 Google Sheet 完成")
    except Exception as e:
        print(f"⚠️ 寫入 Google Sheet 失敗: {e}", file=sys.stderr)

    print("發送 Telegram 早報推播...")
    try:
        msg = format_chips_streak(matched)
        send_telegram(msg)
        print("  → Telegram 推播發送成功")
    except Exception as e:
        print(f"⚠️ 發送 Telegram 失敗: {e}", file=sys.stderr)

    print("✅ 全流程完成")


if __name__ == "__main__":
    main()
