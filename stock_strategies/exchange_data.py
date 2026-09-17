"""證交所 (TWSE) 與 櫃買中心 (TPEx) 官方公開資料串接模組

完全免 Token、無查詢上限，提供全市場 (上市約 1300+ 檔 + 上櫃約 880+ 檔) 三大法人買賣超歷史。
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

EXCHANGE_CACHE_DIR = Path(
    os.environ.get(
        "EXCHANGE_CACHE_DIR",
        str(Path(__file__).resolve().parent.parent / ".cache" / "exchange"),
    )
)


def _ensure_cache_dir() -> Path:
    EXCHANGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return EXCHANGE_CACHE_DIR


def _parse_int(val) -> int:
    if val is None or pd.isna(val):
        return 0
    s = str(val).replace(",", "").strip()
    try:
        return int(s)
    except ValueError:
        try:
            return int(float(s))
        except ValueError:
            return 0


def fetch_twse_t86(date_str: str, timeout: int = 15) -> pd.DataFrame:
    """抓取指定日期 (YYYYMMDD) 證交所上市股票三大法人買賣超 (T86)。

    回傳 DataFrame 欄位: stock_id, name, foreign_net, trust_net, dealer_net, total_net (單位: 股)
    """
    url = f"https://www.twse.com.tw/rwd/zh/fund/T86?date={date_str}&selectType=ALLBUT0999&response=json"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    try:
        r = requests.get(url, headers=headers, timeout=timeout)
        if not r.ok:
            return pd.DataFrame()
        d = r.json()
        if d.get("stat") != "OK" or "data" not in d:
            return pd.DataFrame()

        rows = []
        for item in d["data"]:
            if len(item) < 19:
                continue
            sid = str(item[0]).strip()
            # 排除非個股或特殊權證代號（保留普通股、ETF 與主要標的）
            name = str(item[1]).strip()
            f_net = _parse_int(item[4])
            t_net = _parse_int(item[10])
            d_net = _parse_int(item[11])
            tot_net = _parse_int(item[18])
            rows.append({
                "date": f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}",
                "stock_id": sid,
                "name": name,
                "market": "twse",
                "foreign_net": f_net,
                "trust_net": t_net,
                "dealer_net": d_net,
                "total_net": tot_net,
            })
        return pd.DataFrame(rows)
    except Exception as e:
        print(f"[TWSE] 抓取 {date_str} 失敗: {e}")
        return pd.DataFrame()


def fetch_tpex_t86(date_str: str, timeout: int = 15) -> pd.DataFrame:
    """抓取指定日期 (YYYYMMDD) 櫃買中心上櫃股票三大法人買賣超。

    回傳 DataFrame 欄位: stock_id, name, foreign_net, trust_net, dealer_net, total_net (單位: 股)
    """
    # 格式轉為 YYYY/MM/DD
    formatted_date = f"{date_str[:4]}/{date_str[4:6]}/{date_str[6:]}"
    url = f"https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade?response=json&date={formatted_date}&type=Daily"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    try:
        r = requests.get(url, headers=headers, timeout=timeout)
        if not r.ok:
            return pd.DataFrame()
        d = r.json()
        tables = d.get("tables", [])
        if not tables or "data" not in tables[0]:
            return pd.DataFrame()

        data_rows = tables[0]["data"]
        rows = []
        for item in data_rows:
            if len(item) < 24:
                continue
            sid = str(item[0]).strip()
            name = str(item[1]).strip()
            f_net = _parse_int(item[10])   # 外資及陸資合計買賣超
            t_net = _parse_int(item[13])   # 投信買賣超
            d_net = _parse_int(item[22])   # 自營商合計買賣超
            tot_net = _parse_int(item[23]) # 三大法人合計買賣超
            rows.append({
                "date": f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}",
                "stock_id": sid,
                "name": name,
                "market": "tpex",
                "foreign_net": f_net,
                "trust_net": t_net,
                "dealer_net": d_net,
                "total_net": tot_net,
            })
        return pd.DataFrame(rows)
    except Exception as e:
        print(f"[TPEx] 抓取 {date_str} 失敗: {e}")
        return pd.DataFrame()


def get_market_day(date_str: str) -> pd.DataFrame:
    """取得指定日期的全市場 (上市 + 上櫃) 法人買賣超，具備本地 Parquet 快取。"""
    cache_dir = _ensure_cache_dir()
    cache_file = cache_dir / f"market_{date_str}.parquet"
    if cache_file.exists():
        try:
            return pd.read_parquet(cache_file)
        except Exception:
            pass

    twse_df = fetch_twse_t86(date_str)
    tpex_df = fetch_tpex_t86(date_str)

    if twse_df.empty and tpex_df.empty:
        return pd.DataFrame()

    combined = pd.concat([twse_df, tpex_df], ignore_index=True)
    if not combined.empty:
        try:
            combined.to_parquet(cache_file, index=False)
        except Exception as e:
            print(f"快取寫入失敗: {e}")
    return combined


def get_recent_trading_days(count: int = 3, max_lookback: int = 12) -> list[str]:
    """由今天往回尋找最近 count 個有交易資料的開盤日 (格式: YYYYMMDD，由舊到新)。"""
    trading_days = []
    cur = datetime.now()
    tested = 0

    while len(trading_days) < count and tested < max_lookback:
        if cur.weekday() < 5:  # 週一到週五
            d_str = cur.strftime("%Y%m%d")
            # 優先檢查快取
            cache_file = _ensure_cache_dir() / f"market_{d_str}.parquet"
            if cache_file.exists():
                trading_days.append(d_str)
            else:
                # 測試證交所是否有該日資料
                df = get_market_day(d_str)
                if not df.empty:
                    trading_days.append(d_str)
                time.sleep(0.2)
        cur -= timedelta(days=1)
        tested += 1

    # 轉為由舊到新排列
    return sorted(trading_days)


def get_market_streak_candidates(
    min_streak: int = 3,
    trading_days_count: int = 5,
) -> dict:
    """掃描全台股市場連續買超 >= min_streak 天的標的。

    抓取最近 trading_days_count 個開盤日 (由舊到新)，由最新一日倒數計算連買天數。
    """
    days = get_recent_trading_days(count=trading_days_count)
    if len(days) < min_streak:
        return {"trading_days": [], "candidates": []}

    daily_dfs = {}
    for d in days:
        df = get_market_day(d)
        if not df.empty:
            daily_dfs[d] = df.set_index("stock_id")

    if len(daily_dfs) < min_streak:
        return {"trading_days": [], "candidates": []}

    latest_day = days[-1]
    latest_df = daily_dfs[latest_day]

    buy_candidates = []
    sell_candidates = []
    for sid, row in latest_df.iterrows():
        sid = str(sid).strip()
        # 排除 6 碼以上特殊權證，保留普通股票 (4~5碼) 或 ETF (00開頭)
        if len(sid) > 5 and not sid.startswith("00"):
            continue

        name = str(row.get("name", "")).strip()
        market = str(row.get("market", "")).strip()

        history = []
        for d in days:
            if sid in daily_dfs[d].index:
                r = daily_dfs[d].loc[sid]
                tot = int(r["total_net"])
                f_net = int(r["foreign_net"])
                t_net = int(r["trust_net"])
                d_net = int(r["dealer_net"])
            else:
                tot = f_net = t_net = d_net = 0

            history.append({
                "date": f"{d[:4]}-{d[4:6]}-{d[6:]}",
                "total_net": tot,
                "foreign_net": f_net,
                "trust_net": t_net,
                "dealer_net": d_net,
            })

        # 1. 連續買超天數 (值 > 0)
        def _count_streak_buy(key: str) -> int:
            k = 0
            for h in reversed(history):
                if h[key] > 0:
                    k += 1
                else:
                    break
            return k

        # 2. 連續賣超天數 (值 < 0)
        def _count_streak_sell(key: str) -> int:
            k = 0
            for h in reversed(history):
                if h[key] < 0:
                    k += 1
                else:
                    break
            return k

        tot_buy_streak = _count_streak_buy("total_net")
        f_buy_streak = _count_streak_buy("foreign_net")
        t_buy_streak = _count_streak_buy("trust_net")
        main_buy_streak = max(tot_buy_streak, f_buy_streak, t_buy_streak)

        if main_buy_streak >= min_streak:
            tags = []
            if f_buy_streak >= min_streak and t_buy_streak >= min_streak:
                tags.append("土洋同買")
            elif t_buy_streak >= min_streak:
                tags.append("投信認養")
            elif f_buy_streak >= min_streak:
                tags.append("外資買進")
            elif tot_buy_streak >= min_streak:
                tags.append("法人合買")

            streak_records = history[-main_buy_streak:]
            streak_total_net = sum(h["total_net"] for h in streak_records)
            streak_foreign_net = sum(h["foreign_net"] for h in streak_records)
            streak_trust_net = sum(h["trust_net"] for h in streak_records)
            today_tot = history[-1]["total_net"]

            buy_candidates.append({
                "stock_id": sid,
                "name": name,
                "market": market,
                "direction": "buy",
                "total_streak": tot_buy_streak,
                "foreign_streak": f_buy_streak,
                "trust_streak": t_buy_streak,
                "main_streak": main_buy_streak,
                "today_total_net_lots": int(today_tot // 1000),
                "streak_total_net_lots": int(streak_total_net // 1000),
                "streak_foreign_net_lots": int(streak_foreign_net // 1000),
                "streak_trust_net_lots": int(streak_trust_net // 1000),
                "tags": tags,
                "history": history,
            })

        tot_sell_streak = _count_streak_sell("total_net")
        f_sell_streak = _count_streak_sell("foreign_net")
        t_sell_streak = _count_streak_sell("trust_net")
        main_sell_streak = max(tot_sell_streak, f_sell_streak, t_sell_streak)

        if main_sell_streak >= min_streak:
            tags = []
            if f_sell_streak >= min_streak and t_sell_streak >= min_streak:
                tags.append("土洋同賣")
            elif t_sell_streak >= min_streak:
                tags.append("投信結帳")
            elif f_sell_streak >= min_streak:
                tags.append("外資提款")
            elif tot_sell_streak >= min_streak:
                tags.append("法人合賣")

            streak_records = history[-main_sell_streak:]
            streak_total_net = sum(h["total_net"] for h in streak_records)
            streak_foreign_net = sum(h["foreign_net"] for h in streak_records)
            streak_trust_net = sum(h["trust_net"] for h in streak_records)
            today_tot = history[-1]["total_net"]

            sell_candidates.append({
                "stock_id": sid,
                "name": name,
                "market": market,
                "direction": "sell",
                "total_streak": tot_sell_streak,
                "foreign_streak": f_sell_streak,
                "trust_streak": t_sell_streak,
                "main_streak": main_sell_streak,
                "today_total_net_lots": int(today_tot // 1000),
                "streak_total_net_lots": int(streak_total_net // 1000),
                "streak_foreign_net_lots": int(streak_foreign_net // 1000),
                "streak_trust_net_lots": int(streak_trust_net // 1000),
                "tags": tags,
                "history": history,
            })

    formatted_days = [f"{d[:4]}-{d[4:6]}-{d[6:]}" for d in days]
    return {
        "trading_days": formatted_days,
        "candidates": buy_candidates,
        "buy_candidates": buy_candidates,
        "sell_candidates": sell_candidates,
    }


