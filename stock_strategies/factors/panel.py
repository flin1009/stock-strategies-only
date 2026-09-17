"""build_panel：股池 × 交易日 攤平成 factor__<name> 欄（§4 C7，地基二/三交界）。

回測一次抓全期、逐日純切片（C2）：每檔抓一次 raw_bundle，逐 as_of 切片算因子。
用 context.xxx 動態查找（非 from-import），讓回測/測試可 monkeypatch。
"""
from __future__ import annotations

import pandas as pd

from .. import context
from .registry import compute_factor
# 觸發全部因子註冊（各 school 模組獨立，不依賴 __init__）
from . import value, growth, momentum, chips, revenue, reversal, breakout, legacy  # noqa: F401


def build_panel(stocks, factor_names, as_of_dates, lookback_years=5, params=None):
    """回 DataFrame：每列 (stock_id, date) + factor__<name> 欄。None 值為 NaN。"""
    params = params or {}
    as_of_dates = [pd.Timestamp(d) for d in as_of_dates]
    rows = []
    for sid in stocks:
        start = (min(as_of_dates) - pd.DateOffset(years=lookback_years)
                 - pd.Timedelta(days=60)).strftime("%Y-%m-%d")
        try:
            bundle = context._gather_raw_bundle(
                sid, start, lookback_years, as_of=max(as_of_dates).strftime("%Y-%m-%d"))
        except Exception:
            continue
        for as_of in as_of_dates:
            ctx = context.build_context_from_bundle(sid, as_of, bundle)
            row = {"stock_id": sid, "date": pd.Timestamp(as_of)}
            for name in factor_names:
                row[f"factor__{name}"] = compute_factor(name, ctx, params)
            rows.append(row)
    return pd.DataFrame(rows)
