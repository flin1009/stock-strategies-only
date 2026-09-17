# P1：M0 契約凍結 + M1 資料層 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立帶 parquet 快取 + 限流退避的 FinMind 取數層、7 個 point-in-time loader、與無未來資訊的 `FactorContext` 建構器，作為後續因子庫／回測引擎的唯一資料入口。

**Architecture:** 三個新檔案 `cache.py`（快取＋限流）、`datasources.py`（各 dataset loader）、`context.py`（`FactorContext` ＋ `build_context`）。所有對外取數一律走 `fetch_finmind_cached`；所有切片以 `as_of` 為硬上界（日頻用 `date<=as_of`、月營收用 `avail_date<=as_of`、財報用發布日表），杜絕 look-ahead。`build_context` 拆成「一次抓全期 `_gather_raw_bundle`」＋「逐日純切片 `build_context_from_bundle`」兩段，讓回測逐日推進時不重抓、不踩 FinMind 限流。

**Tech Stack:** Python 3.11、pandas、pyarrow（parquet）、requests、pytest + pytest-mock；套件一律用 `uv` 管理。

**對應 spec：** `docs/superpowers/specs/2026-06-13-multi-expert-stock-strategy-design.md` §4（C1/C2 契約）、§6（資料層完整設計）。凡與 spec §6 細節衝突，以 §4 契約為準。

**契約凍結（M0，本計畫負責資料層相關的 C1/C2）：**
- **C1 `FactorContext`** 唯一定義在 `stock_strategies/context.py`，欄位名一律 `price_df`/`index_df`，`as_of` 為 `pd.Timestamp`。後續章節一律 `from .context import FactorContext`，禁止 redefine。
- **C2 `build_context` 拆兩段**：`build_context_from_bundle(stock_id, as_of, raw_bundle)`（純切片、無 IO，回測用）與 `build_context(stock_id, as_of_date, *, lookback_years, info_df, strict)`（抓一次後內部呼叫 from_bundle，runtime 用）。

---

## File Structure

| 檔案 | 職責 | 動作 |
| --- | --- | --- |
| `pyproject.toml` | 加 `pyarrow` 依賴、`pytest`/`pytest-mock` dev 依賴、pytest 設定 | Modify |
| `.gitignore` | 加 `.cache/` | Modify |
| `stock_strategies/config.py` | 新增快取／限流／context 常數 | Modify |
| `stock_strategies/cache.py` | `fetch_finmind_cached`、`_rate_limited_get`、`FinMindRateLimitError`、`cache_path`、`clear_cache` | Create |
| `stock_strategies/datasources.py` | 7 個 loader：法人／月營收／估值／融資券／持股／股票資訊／指數／股本產業 | Create |
| `stock_strategies/context.py` | `FactorContext`、`_gather_raw_bundle`、`build_context_from_bundle`、`build_context`、發布日常數 | Create |
| `stock_strategies/market.py` | `_fetch_taiex` 重構為呼叫 `get_index_history`（行為不變） | Modify |
| `stock_strategies/data.py` | `get_price_history`/`get_fundamental` 內部改走 `fetch_finmind_cached`（純加速） | Modify |
| `tests/` | 對應 10 個測試點 | Create |

---

## Task 0：測試基礎與依賴

**Files:**
- Modify: `pyproject.toml`
- Modify: `.gitignore`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`

- [ ] **Step 1: 安裝依賴**

Run:
```bash
cd /Users/kevinluo/google-agent-ecosystem/stock-strategies-only
uv add pyarrow
uv add --dev pytest pytest-mock
```
Expected: `pyproject.toml` 新增 `pyarrow`，並出現 `[dependency-groups]` 的 dev 群組含 `pytest`、`pytest-mock`。

- [ ] **Step 2: 加 pytest 設定到 `pyproject.toml`**

在 `pyproject.toml` 末端追加：
```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
markers = [
    "live: 對真實 FinMind API 的整合測試（預設不跑，用 -m live 觸發）",
]
addopts = "-m 'not live'"
```

- [ ] **Step 3: `.gitignore` 加快取目錄**

在 `.gitignore` 追加一行：
```
.cache/
```

- [ ] **Step 4: 建測試骨架**

Create `tests/__init__.py`（空檔）。

Create `tests/conftest.py`：
```python
"""共用測試 fixture。FinMind 一律 mock，不打真 API。"""
import os
import pandas as pd
import pytest


@pytest.fixture(autouse=True)
def _isolate_cache(tmp_path, monkeypatch):
    """每個測試用獨立快取目錄，避免互相污染。"""
    cache_dir = tmp_path / "finmind_cache"
    monkeypatch.setenv("FINMIND_CACHE_DIR", str(cache_dir))
    monkeypatch.setenv("FINMIND_TOKEN", "test-token")
    yield


def make_price_df(n=120, start="2023-01-02", base=100.0):
    """造一段遞增日 K，欄位符合 add_indicators 契約。"""
    dates = pd.bdate_range(start=start, periods=n)
    close = [base + i * 0.5 for i in range(n)]
    return pd.DataFrame({
        "date": dates,
        "open": [c - 0.3 for c in close],
        "high": [c + 0.6 for c in close],
        "low": [c - 0.6 for c in close],
        "close": close,
        "volume": [1000 + i for i in range(n)],
    })
```

- [ ] **Step 5: 驗證 pytest 可跑**

Run: `uv run pytest -q`
Expected: `no tests ran`（collected 0 items），無 import error。

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock .gitignore tests/
git commit -m "test: 建立 pytest 測試基礎與 pyarrow 依賴（P1 起點）"
```

---

## Task 1：`config.py` 新增快取／限流／context 常數

**Files:**
- Modify: `stock_strategies/config.py`
- Test: `tests/test_config_constants.py`

- [ ] **Step 1: 寫失敗測試**

Create `tests/test_config_constants.py`：
```python
from stock_strategies import config


def test_cache_and_ratelimit_constants_exist():
    assert isinstance(config.CACHE_FRESH_DAYS, dict)
    assert config.CACHE_FRESH_DAYS["daily"] == 1
    assert config.CACHE_FRESH_DAYS["monthly"] == 20
    assert config.FINMIND_MIN_INTERVAL > 0
    assert config.RATE_LIMIT_BACKOFF_BASE == 5
    assert config.RATE_LIMIT_MAX_RETRIES == 4
    assert config.MIN_PRICE_ROWS == 60
    assert config.FINMIND_CACHE_DIR  # 非空字串
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/test_config_constants.py -q`
Expected: FAIL（`AttributeError: module ... has no attribute 'CACHE_FRESH_DAYS'`）。

- [ ] **Step 3: 實作常數**

在 `stock_strategies/config.py` 頂部加入 import 與常數（沿用現有 `FINMIND_URL` 不動）：
```python
import os
from pathlib import Path

# ── 快取（parquet）──
FINMIND_CACHE_DIR = os.environ.get(
    "FINMIND_CACHE_DIR",
    str(Path(__file__).resolve().parent.parent / ".cache" / "finmind"),
)
# 各頻率快取新鮮天數：超過則增量更新
CACHE_FRESH_DAYS = {"daily": 1, "monthly": 20, "weekly": 5, "static": 7}

# ── 限流（FinMind 免費版約 600 req/hr）──
FINMIND_MIN_INTERVAL = 0.12       # 相鄰請求最小間隔秒
RATE_LIMIT_BACKOFF_BASE = 5       # 限流退避基數秒
RATE_LIMIT_MAX_RETRIES = 4

# ── context ──
MIN_PRICE_ROWS = 60               # 少於此列數視為新股／資料不足
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run pytest tests/test_config_constants.py -q`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add stock_strategies/config.py tests/test_config_constants.py
git commit -m "feat: config 新增快取/限流/context 常數"
```

---

## Task 2：`cache.py` — `cache_path` 與 `clear_cache`

**Files:**
- Create: `stock_strategies/cache.py`
- Test: `tests/test_cache_paths.py`

- [ ] **Step 1: 寫失敗測試**

Create `tests/test_cache_paths.py`：
```python
from pathlib import Path
import pandas as pd
from stock_strategies import cache


def test_cache_path_format():
    p = cache.cache_path("TaiwanStockMonthRevenue", "2330")
    assert isinstance(p, Path)
    assert p.name == "TaiwanStockMonthRevenue__2330.parquet"


def test_clear_cache_counts_removed(tmp_path, monkeypatch):
    monkeypatch.setenv("FINMIND_CACHE_DIR", str(tmp_path))
    # 寫兩個假快取檔
    for did in ("2330", "2317"):
        df = pd.DataFrame({"date": pd.to_datetime(["2024-01-01"]), "v": [1]})
        p = cache.cache_path("TaiwanStockPrice", did)
        p.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(p)
        p.with_suffix(".meta.json").write_text("{}")
    removed = cache.clear_cache(dataset="TaiwanStockPrice")
    assert removed == 2
    assert cache.clear_cache() == 0  # 已清空
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/test_cache_paths.py -q`
Expected: FAIL（`ModuleNotFoundError: stock_strategies.cache`）。

- [ ] **Step 3: 實作 `cache.py` 路徑部分**

Create `stock_strategies/cache.py`：
```python
"""帶 parquet 快取 + 限流退避的 FinMind 取數層。

設計：一檔一 dataset 一 parquet（全歷史，不依日期切檔），讀取時記憶體過濾。
所有新 loader 一律呼叫 fetch_finmind_cached；回測逐日推進時命中快取、不重打 API。
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from .config import FINMIND_CACHE_DIR


class FinMindRateLimitError(RuntimeError):
    """FinMind 限流且退避重試耗盡。由上層 loader 接住回中性結果。"""


def _cache_dir() -> Path:
    # 每次讀 env：測試會用 monkeypatch 改 FINMIND_CACHE_DIR
    import os
    return Path(os.environ.get("FINMIND_CACHE_DIR", FINMIND_CACHE_DIR))


def cache_path(dataset: str, data_id: str) -> Path:
    safe_id = data_id or "_ALL_"
    return _cache_dir() / f"{dataset}__{safe_id}.parquet"


def _meta_path(dataset: str, data_id: str) -> Path:
    return cache_path(dataset, data_id).with_suffix(".meta.json")


def clear_cache(dataset: str | None = None, data_id: str | None = None) -> int:
    """刪除符合條件的快取檔（含 sidecar meta），回傳刪除的 parquet 數。"""
    root = _cache_dir()
    if not root.exists():
        return 0
    if dataset and data_id:
        pattern = f"{dataset}__{data_id}.parquet"
    elif dataset:
        pattern = f"{dataset}__*.parquet"
    else:
        pattern = "*.parquet"
    removed = 0
    for p in root.glob(pattern):
        p.unlink()
        meta = p.with_suffix(".meta.json")
        if meta.exists():
            meta.unlink()
        removed += 1
    return removed
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run pytest tests/test_cache_paths.py -q`
Expected: PASS（2 passed）。

- [ ] **Step 5: Commit**

```bash
git add stock_strategies/cache.py tests/test_cache_paths.py
git commit -m "feat: cache.py 快取路徑與 clear_cache"
```

---

## Task 3：`cache.py` — `_rate_limited_get` 限流退避

**Files:**
- Modify: `stock_strategies/cache.py`
- Test: `tests/test_rate_limit.py`

- [ ] **Step 1: 寫失敗測試**

Create `tests/test_rate_limit.py`：
```python
import pytest
from stock_strategies import cache


class _FakeResp:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body or {"status": 200, "data": [{"x": 1}]}

    def raise_for_status(self):
        if self.status_code >= 400 and self.status_code not in (402, 429):
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._body


def test_rate_limit_retries_then_succeeds(monkeypatch):
    # 前兩次回限流(body status=402)，第三次成功
    seq = [
        _FakeResp(body={"status": 402, "msg": "request limit reached"}),
        _FakeResp(body={"status": 402, "msg": "request limit reached"}),
        _FakeResp(body={"status": 200, "data": [{"x": 1}]}),
    ]
    calls = {"n": 0}

    def fake_get(url, params=None, timeout=None):
        i = calls["n"]; calls["n"] += 1
        return seq[i]

    monkeypatch.setattr(cache.requests, "get", fake_get)
    monkeypatch.setattr(cache.time, "sleep", lambda s: None)  # 不真睡
    out = cache._rate_limited_get({"dataset": "X"}, timeout=5, max_retries=2)
    assert out["data"] == [{"x": 1}]
    assert calls["n"] == 3


def test_rate_limit_exhausts_raises(monkeypatch):
    def fake_get(url, params=None, timeout=None):
        return _FakeResp(body={"status": 402, "msg": "request limit reached"})

    monkeypatch.setattr(cache.requests, "get", fake_get)
    monkeypatch.setattr(cache.time, "sleep", lambda s: None)
    with pytest.raises(cache.FinMindRateLimitError):
        cache._rate_limited_get({"dataset": "X"}, timeout=5, max_retries=2)
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/test_rate_limit.py -q`
Expected: FAIL（`AttributeError: module ... has no attribute 'requests'` 或 `_rate_limited_get`）。

- [ ] **Step 3: 實作限流取數**

在 `stock_strategies/cache.py` 的 import 區補上：
```python
import time

import requests

from .config import (
    FINMIND_URL,
    FINMIND_MIN_INTERVAL,
    RATE_LIMIT_BACKOFF_BASE,
    RATE_LIMIT_MAX_RETRIES,
)
```
（保留既有 `from .config import FINMIND_CACHE_DIR`，可合併成一個 import 區塊。）

在檔案加入模組級節流狀態與函式：
```python
_last_request_monotonic = 0.0


def _throttle() -> None:
    """全域最小間隔節流，避免瞬間爆量。"""
    global _last_request_monotonic
    now = time.monotonic()
    wait = FINMIND_MIN_INTERVAL - (now - _last_request_monotonic)
    if wait > 0:
        time.sleep(wait)
    _last_request_monotonic = time.monotonic()


def _is_rate_limited(resp) -> bool:
    if resp.status_code in (402, 429):
        return True
    try:
        body = resp.json()
    except Exception:
        return False
    return (
        isinstance(body, dict)
        and body.get("status") not in (200, None)
        and "request" in str(body.get("msg", "")).lower()
    )


def _rate_limited_get(params: dict, timeout: int, max_retries: int) -> dict:
    """打 FinMind，處理限流（402/429/body status!=200 含 'request'）：
    指數退避重試，耗盡 raise FinMindRateLimitError。回傳已解析的 json dict。"""
    attempt = 0
    while True:
        _throttle()
        resp = requests.get(FINMIND_URL, params=params, timeout=timeout)
        if _is_rate_limited(resp):
            if attempt >= RATE_LIMIT_MAX_RETRIES:
                raise FinMindRateLimitError(
                    f"FinMind 限流重試 {attempt} 次仍失敗: {params.get('dataset')}"
                )
            backoff = min(RATE_LIMIT_BACKOFF_BASE * (2 ** attempt), 120)
            time.sleep(backoff)
            attempt += 1
            continue
        resp.raise_for_status()
        return resp.json()
```

> 註：測試的 `max_retries` 參數沿用簽名但限流重試上限以 `RATE_LIMIT_MAX_RETRIES` 為準；`max_retries` 保留給連線層（未來可加），本步不使用其值即可通過測試（第三次成功在 4 次上限內）。

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run pytest tests/test_rate_limit.py -q`
Expected: PASS（2 passed）。

- [ ] **Step 5: Commit**

```bash
git add stock_strategies/cache.py tests/test_rate_limit.py
git commit -m "feat: cache.py 限流退避 _rate_limited_get + FinMindRateLimitError"
```

---

## Task 4：`cache.py` — `fetch_finmind_cached` 快取命中與增量

**Files:**
- Modify: `stock_strategies/cache.py`
- Test: `tests/test_fetch_cached.py`

- [ ] **Step 1: 寫失敗測試**

Create `tests/test_fetch_cached.py`：
```python
from stock_strategies import cache


def _patch_api(monkeypatch, rows_by_call):
    """mock _rate_limited_get 回傳每次呼叫對應的 data；記錄呼叫次數。"""
    state = {"n": 0}

    def fake(params, timeout, max_retries):
        i = state["n"]; state["n"] += 1
        return {"status": 200, "data": rows_by_call[min(i, len(rows_by_call) - 1)]}

    monkeypatch.setattr(cache, "_rate_limited_get", fake)
    return state


def test_cache_hit_skips_api(monkeypatch):
    # 新鮮度由 _is_fresh 以 max_date 距今判定（spec §6），故須用「近兩日」資料
    # 才會被判為新鮮而命中快取。不可用固定過去日期（會被判過期 → 第二次重打 API）。
    today = pd.Timestamp.now().normalize()
    d0 = (today - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    d1 = today.strftime("%Y-%m-%d")
    start = (today - pd.Timedelta(days=10)).strftime("%Y-%m-%d")
    rows = [[{"date": d0, "close": 10}, {"date": d1, "close": 11}]]
    state = _patch_api(monkeypatch, rows)
    df1 = cache.fetch_finmind_cached("TaiwanStockPrice", "2330", start)
    assert len(df1) == 2
    assert state["n"] == 1
    # 第二次：新鮮快取 → 不打 API
    df2 = cache.fetch_finmind_cached("TaiwanStockPrice", "2330", start)
    assert state["n"] == 1  # 沒再呼叫
    assert len(df2) == 2


def test_end_date_filters_future(monkeypatch):
    rows = [[
        {"date": "2024-01-02", "close": 10},
        {"date": "2024-01-05", "close": 12},
        {"date": "2024-01-10", "close": 15},
    ]]
    _patch_api(monkeypatch, rows)
    df = cache.fetch_finmind_cached(
        "TaiwanStockPrice", "2330", "2024-01-01", end_date="2024-01-05"
    )
    assert df["date"].max() <= __import__("pandas").Timestamp("2024-01-05")
    assert len(df) == 2


def test_force_refresh_rehits_api(monkeypatch):
    rows = [[{"date": "2024-01-02", "close": 10}]]
    state = _patch_api(monkeypatch, rows)
    cache.fetch_finmind_cached("TaiwanStockPrice", "2330", "2024-01-01")
    cache.fetch_finmind_cached("TaiwanStockPrice", "2330", "2024-01-01", force_refresh=True)
    assert state["n"] == 2
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/test_fetch_cached.py -q`
Expected: FAIL（`AttributeError: ... 'fetch_finmind_cached'`）。

- [ ] **Step 3: 實作 `fetch_finmind_cached`**

在 `stock_strategies/cache.py` 的 import 補 `from datetime import datetime, timezone` 與 `from .config import CACHE_FRESH_DAYS`，加入：
```python
def _freq_of(dataset: str) -> str:
    if dataset == "TaiwanStockMonthRevenue":
        return "monthly"
    if dataset == "TaiwanStockShareholding":
        return "weekly"
    if dataset == "TaiwanStockInfo":
        return "static"
    return "daily"


def _read_cache(dataset: str, data_id: str) -> pd.DataFrame | None:
    p = cache_path(dataset, data_id)
    if not p.exists():
        return None
    try:
        return pd.read_parquet(p)
    except Exception:
        # 損毀 → 刪檔重抓
        p.unlink(missing_ok=True)
        _meta_path(dataset, data_id).unlink(missing_ok=True)
        return None


def _write_cache(dataset: str, data_id: str, df: pd.DataFrame) -> None:
    p = cache_path(dataset, data_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(p, index=False)
    meta = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "min_date": str(df["date"].min()) if "date" in df and len(df) else None,
        "max_date": str(df["date"].max()) if "date" in df and len(df) else None,
        "rows": int(len(df)),
    }
    _meta_path(dataset, data_id).write_text(json.dumps(meta))


def _is_fresh(dataset: str, data_id: str, fresh_days: int | None) -> bool:
    mp = _meta_path(dataset, data_id)
    if not mp.exists():
        return False
    try:
        meta = json.loads(mp.read_text())
        max_date = pd.to_datetime(meta.get("max_date"))
    except Exception:
        return False
    if pd.isna(max_date):
        return False
    days = fresh_days if fresh_days is not None else CACHE_FRESH_DAYS[_freq_of(dataset)]
    return (pd.Timestamp.now().normalize() - max_date.normalize()).days <= days


def _api_to_df(payload: dict) -> pd.DataFrame:
    df = pd.DataFrame(payload.get("data", []))
    if not df.empty and "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
    return df


def fetch_finmind_cached(
    dataset: str,
    data_id: str,
    start_date: str,
    end_date: str | None = None,
    *,
    fresh_days: int | None = None,
    force_refresh: bool = False,
    timeout: int = 30,
    max_retries: int = 2,
) -> pd.DataFrame:
    """帶 parquet 快取 + 限流退避的 FinMind 取數。

    - 命中新鮮快取 → 直接回（不打 API）
    - 過期 → 增量抓 max_date 之後並合併去重
    - 冷啟動 → 全抓並寫快取
    回傳已正規化 date（datetime64）、升冪排序、去重後的 DataFrame；
    最後依 [start_date, end_date] 切片（end_date 杜絕抓進未來）。
    """
    cached = None if force_refresh else _read_cache(dataset, data_id)

    if cached is not None and not force_refresh and _is_fresh(dataset, data_id, fresh_days):
        df = cached
    else:
        params = {
            "dataset": dataset,
            "data_id": data_id,
            "start_date": start_date,
            "token": __import__("os").environ.get("FINMIND_TOKEN", ""),
        }
        # 增量：有舊快取則只抓 max_date 之後（留 7 天 overlap 去重）
        if cached is not None and len(cached) and "date" in cached.columns:
            inc_start = (cached["date"].max() - pd.Timedelta(days=7)).strftime("%Y-%m-%d")
            params["start_date"] = min(start_date, inc_start)
        payload = _rate_limited_get(params, timeout=timeout, max_retries=max_retries)
        fresh = _api_to_df(payload)
        if cached is not None and len(cached):
            df = pd.concat([cached, fresh], ignore_index=True)
            if "date" in df.columns:
                df = df.drop_duplicates(subset=[c for c in df.columns]).sort_values("date")
                df = df.drop_duplicates(subset=["date"] + (["data_id"] if "data_id" in df else []), keep="last")
        else:
            df = fresh
        df = df.reset_index(drop=True)
        if len(df):
            _write_cache(dataset, data_id, df)

    # 切片
    if "date" in df.columns and len(df):
        df = df[df["date"] >= pd.to_datetime(start_date)]
        if end_date:
            df = df[df["date"] <= pd.to_datetime(end_date)]
        df = df.sort_values("date").reset_index(drop=True)
    return df
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run pytest tests/test_fetch_cached.py -q`
Expected: PASS（3 passed）。

- [ ] **Step 5: Commit**

```bash
git add stock_strategies/cache.py tests/test_fetch_cached.py
git commit -m "feat: cache.py fetch_finmind_cached（快取命中/增量/end_date 切片）"
```

---

## Task 5：`datasources.py` — `get_institutional`（三大法人 pivot）

**Files:**
- Create: `stock_strategies/datasources.py`
- Test: `tests/test_ds_institutional.py`

- [ ] **Step 1: 寫失敗測試**

Create `tests/test_ds_institutional.py`：
```python
import pandas as pd
from stock_strategies import datasources as ds


def test_institutional_pivot_and_net(monkeypatch):
    raw = pd.DataFrame({
        "date": pd.to_datetime(["2024-01-02"] * 4),
        "name": ["Foreign_Investor", "Investment_Trust", "Dealer_self", "Dealer_Hedging"],
        "buy": [5000, 2000, 1000, 500],
        "sell": [1000, 500, 800, 200],
    })
    monkeypatch.setattr(ds, "fetch_finmind_cached", lambda *a, **k: raw.copy())
    out = ds.get_institutional("2330", "2024-01-01")
    row = out.iloc[0]
    assert row["foreign_net"] == 4000      # 5000-1000
    assert row["trust_net"] == 1500        # 2000-500
    assert row["dealer_net"] == 500        # (1000-800)+(500-200)=200+300
    assert row["total_net"] == 4000 + 1500 + 500


def test_institutional_empty(monkeypatch):
    monkeypatch.setattr(ds, "fetch_finmind_cached", lambda *a, **k: pd.DataFrame())
    out = ds.get_institutional("2330", "2024-01-01")
    assert out.empty
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/test_ds_institutional.py -q`
Expected: FAIL（`ModuleNotFoundError: stock_strategies.datasources`）。

- [ ] **Step 3: 實作 `datasources.py` 與 `get_institutional`**

Create `stock_strategies/datasources.py`：
```python
"""各 FinMind dataset 的 point-in-time loader。

通則：每個 loader (a) 呼叫 fetch_finmind_cached；(b) rename 正規化；
(c) to_datetime + to_numeric(coerce)；(d) 依 as_of 切片（傳 end_date）；
(e) 空資料回空 DataFrame（不 raise），讓因子層判中性。
as_of 是避免 look-ahead 的單一機制。
"""
from __future__ import annotations

import pandas as pd

from .cache import fetch_finmind_cached, FinMindRateLimitError


def _require_cols(df: pd.DataFrame, cols: list[str]) -> bool:
    return all(c in df.columns for c in cols)


def get_institutional(stock_id: str, start: str, as_of: str | None = None) -> pd.DataFrame:
    """三大法人買賣超（日）。回欄位:
       date, foreign_net, trust_net, dealer_net, total_net（單位：股）。
    FinMind name 欄分桶：Foreign* → 外資、Investment_Trust → 投信、
    Dealer*（self+Hedging）→ 自營；net = buy - sell。"""
    try:
        df = fetch_finmind_cached(
            "TaiwanStockInstitutionalInvestorsBuySell", stock_id, start, end_date=as_of
        )
    except FinMindRateLimitError:
        return pd.DataFrame()
    if df.empty or not _require_cols(df, ["date", "name", "buy", "sell"]):
        return pd.DataFrame()
    df = df.copy()
    df["buy"] = pd.to_numeric(df["buy"], errors="coerce")
    df["sell"] = pd.to_numeric(df["sell"], errors="coerce")
    df["net"] = df["buy"] - df["sell"]

    def bucket(name: str) -> str:
        n = str(name)
        if n.startswith("Foreign"):
            return "foreign_net"
        if n.startswith("Investment_Trust"):
            return "trust_net"
        if n.startswith("Dealer"):
            return "dealer_net"
        return "other"

    df["bucket"] = df["name"].map(bucket)
    df = df[df["bucket"] != "other"]
    wide = df.pivot_table(index="date", columns="bucket", values="net",
                          aggfunc="sum", fill_value=0).reset_index()
    for col in ["foreign_net", "trust_net", "dealer_net"]:
        if col not in wide.columns:
            wide[col] = 0
    wide["total_net"] = wide["foreign_net"] + wide["trust_net"] + wide["dealer_net"]
    return wide[["date", "foreign_net", "trust_net", "dealer_net", "total_net"]].sort_values("date").reset_index(drop=True)
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run pytest tests/test_ds_institutional.py -q`
Expected: PASS（2 passed）。

- [ ] **Step 5: Commit**

```bash
git add stock_strategies/datasources.py tests/test_ds_institutional.py
git commit -m "feat: datasources.get_institutional（三大法人 pivot，自營合併）"
```

---

## Task 6：`datasources.py` — `get_month_revenue`（月營收 + avail_date 防 look-ahead）

**Files:**
- Modify: `stock_strategies/datasources.py`
- Test: `tests/test_ds_revenue.py`

- [ ] **Step 1: 寫失敗測試（spec 測試點 4）**

Create `tests/test_ds_revenue.py`：
```python
import pandas as pd
from stock_strategies import datasources as ds


def _raw():
    return pd.DataFrame({
        "date": pd.to_datetime(["2024-02-01", "2024-03-01"]),  # 所屬月
        "revenue_year": [2024, 2024],
        "revenue_month": [2, 3],
        "revenue": [100_000, 120_000],
    })


def test_revenue_avail_date_blocks_lookahead(monkeypatch):
    monkeypatch.setattr(ds, "fetch_finmind_cached", lambda *a, **k: _raw())
    # 3 月營收 avail_date = 2024-04-10；as_of=2024-04-05 不應看到 3 月
    out = ds.get_month_revenue("2330", "2024-01-01", as_of="2024-04-05")
    assert out["revenue_month"].max() == 2  # 只到 2 月（avail=2024-03-10）
    # as_of=2024-04-10 才看得到 3 月
    out2 = ds.get_month_revenue("2330", "2024-01-01", as_of="2024-04-10")
    assert 3 in out2["revenue_month"].values


def test_revenue_mom_yoy_columns(monkeypatch):
    monkeypatch.setattr(ds, "fetch_finmind_cached", lambda *a, **k: _raw())
    out = ds.get_month_revenue("2330", "2024-01-01")
    assert {"avail_date", "mom", "yoy", "revenue"}.issubset(out.columns)
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/test_ds_revenue.py -q`
Expected: FAIL（`AttributeError: ... 'get_month_revenue'`）。

- [ ] **Step 3: 實作 `get_month_revenue`**

在 `stock_strategies/datasources.py` 追加：
```python
def get_month_revenue(stock_id: str, start: str, as_of: str | None = None) -> pd.DataFrame:
    """月營收（月）。回欄位:
       avail_date(資料可得日≈次月10日), revenue_year, revenue_month, revenue, mom, yoy。
    防 look-ahead：as_of 切片用 avail_date <= as_of（非所屬月）。"""
    try:
        # 月營收全抓（不在快取層用 as_of 切，因 avail_date 在此才算得出）
        df = fetch_finmind_cached("TaiwanStockMonthRevenue", stock_id, start)
    except FinMindRateLimitError:
        return pd.DataFrame()
    if df.empty or not _require_cols(df, ["revenue_year", "revenue_month", "revenue"]):
        return pd.DataFrame()
    df = df.copy()
    for c in ["revenue_year", "revenue_month", "revenue"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["revenue_year", "revenue_month"])
    df["period"] = pd.to_datetime(
        df["revenue_year"].astype(int).astype(str) + "-"
        + df["revenue_month"].astype(int).astype(str).str.zfill(2) + "-01"
    )
    # 公布日保守估：所屬月底 + 10 天（次月 10 日，法規上限）
    df["avail_date"] = df["period"] + pd.offsets.MonthEnd(0) + pd.Timedelta(days=10)
    df = df.sort_values("period").reset_index(drop=True)
    df["mom"] = df["revenue"].pct_change()
    df["yoy"] = df["revenue"].pct_change(periods=12)
    if as_of:
        df = df[df["avail_date"] <= pd.to_datetime(as_of)]
    return df[["avail_date", "period", "revenue_year", "revenue_month",
               "revenue", "mom", "yoy"]].reset_index(drop=True)
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run pytest tests/test_ds_revenue.py -q`
Expected: PASS（2 passed）。

- [ ] **Step 5: Commit**

```bash
git add stock_strategies/datasources.py tests/test_ds_revenue.py
git commit -m "feat: datasources.get_month_revenue（avail_date 防 look-ahead + MoM/YoY）"
```

---

## Task 7：`datasources.py` — `get_valuation`（估值 + 欄位缺失韌性）

**Files:**
- Modify: `stock_strategies/datasources.py`
- Test: `tests/test_ds_valuation.py`

- [ ] **Step 1: 寫失敗測試（含 spec 測試點 8）**

Create `tests/test_ds_valuation.py`：
```python
import pandas as pd
import numpy as np
from stock_strategies import datasources as ds


def test_valuation_normalizes(monkeypatch):
    raw = pd.DataFrame({
        "date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
        "PER": [15.0, -3.0],          # 負本益比 → NaN
        "PBR": [2.0, 2.1],
        "dividend_yield": [3.5, 3.4],
    })
    monkeypatch.setattr(ds, "fetch_finmind_cached", lambda *a, **k: raw.copy())
    out = ds.get_valuation("2330", "2024-01-01")
    assert {"date", "per", "pbr", "dividend_yield"}.issubset(out.columns)
    assert np.isnan(out.iloc[1]["per"])   # 負值轉 NaN
    assert out.iloc[0]["per"] == 15.0


def test_valuation_missing_col_resilient(monkeypatch):
    raw = pd.DataFrame({"date": pd.to_datetime(["2024-01-02"]), "PBR": [2.0]})  # 缺 PER
    monkeypatch.setattr(ds, "fetch_finmind_cached", lambda *a, **k: raw.copy())
    out = ds.get_valuation("2330", "2024-01-01")
    assert out.empty   # 缺關鍵欄 → 回空，不 KeyError
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/test_ds_valuation.py -q`
Expected: FAIL（`AttributeError: ... 'get_valuation'`）。

- [ ] **Step 3: 實作 `get_valuation`**

在 `stock_strategies/datasources.py` 追加：
```python
def get_valuation(stock_id: str, start: str, as_of: str | None = None) -> pd.DataFrame:
    """估值（日）。回 date, per, pbr, dividend_yield。per<=0(虧損)→NaN。
    缺關鍵欄位 → 回空（韌性，不 KeyError）。"""
    try:
        df = fetch_finmind_cached("TaiwanStockPER", stock_id, start, end_date=as_of)
    except FinMindRateLimitError:
        return pd.DataFrame()
    if df.empty:
        return pd.DataFrame()
    rename = {"PER": "per", "PBR": "pbr", "dividend_yield": "dividend_yield"}
    df = df.rename(columns=rename)
    if not _require_cols(df, ["date", "per", "pbr"]):
        return pd.DataFrame()
    df = df.copy()
    for c in ["per", "pbr", "dividend_yield"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df.loc[df["per"] <= 0, "per"] = pd.NA
    df["per"] = pd.to_numeric(df["per"], errors="coerce")
    keep = [c for c in ["date", "per", "pbr", "dividend_yield"] if c in df.columns]
    return df[keep].sort_values("date").reset_index(drop=True)
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run pytest tests/test_ds_valuation.py -q`
Expected: PASS（2 passed）。

- [ ] **Step 5: Commit**

```bash
git add stock_strategies/datasources.py tests/test_ds_valuation.py
git commit -m "feat: datasources.get_valuation（負PER轉NaN + 欄位缺失韌性）"
```

---

## Task 8：`datasources.py` — `get_margin`（融資融券）

**Files:**
- Modify: `stock_strategies/datasources.py`
- Test: `tests/test_ds_margin.py`

- [ ] **Step 1: 寫失敗測試**

Create `tests/test_ds_margin.py`：
```python
import pandas as pd
from stock_strategies import datasources as ds


def test_margin_ratio_and_chg(monkeypatch):
    raw = pd.DataFrame({
        "date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
        "MarginPurchaseTodayBalance": [1000, 1100],
        "ShortSaleTodayBalance": [200, 260],
    })
    monkeypatch.setattr(ds, "fetch_finmind_cached", lambda *a, **k: raw.copy())
    out = ds.get_margin("2330", "2024-01-01")
    assert out.iloc[1]["margin_balance"] == 1100
    assert out.iloc[1]["margin_chg"] == 100
    assert out.iloc[1]["short_chg"] == 60
    assert abs(out.iloc[1]["short_margin_ratio"] - 260 / 1100) < 1e-9
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/test_ds_margin.py -q`
Expected: FAIL（`AttributeError: ... 'get_margin'`）。

- [ ] **Step 3: 實作 `get_margin`**

在 `stock_strategies/datasources.py` 追加：
```python
def get_margin(stock_id: str, start: str, as_of: str | None = None) -> pd.DataFrame:
    """融資融券（日）。回 date, margin_balance, short_balance,
       margin_chg, short_chg, short_margin_ratio(券資比)。"""
    try:
        df = fetch_finmind_cached(
            "TaiwanStockMarginPurchaseShortSale", stock_id, start, end_date=as_of
        )
    except FinMindRateLimitError:
        return pd.DataFrame()
    rename = {
        "MarginPurchaseTodayBalance": "margin_balance",
        "ShortSaleTodayBalance": "short_balance",
    }
    df = df.rename(columns=rename)
    if df.empty or not _require_cols(df, ["date", "margin_balance", "short_balance"]):
        return pd.DataFrame()
    df = df.copy()
    for c in ["margin_balance", "short_balance"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.sort_values("date").reset_index(drop=True)
    df["margin_chg"] = df["margin_balance"].diff()
    df["short_chg"] = df["short_balance"].diff()
    df["short_margin_ratio"] = df["short_balance"] / df["margin_balance"].replace(0, pd.NA)
    return df[["date", "margin_balance", "short_balance",
               "margin_chg", "short_chg", "short_margin_ratio"]].reset_index(drop=True)
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run pytest tests/test_ds_margin.py -q`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add stock_strategies/datasources.py tests/test_ds_margin.py
git commit -m "feat: datasources.get_margin（融資券增減 + 券資比）"
```

---

## Task 9：`datasources.py` — `get_shareholding`（外資持股，不規則頻率）

**Files:**
- Modify: `stock_strategies/datasources.py`
- Test: `tests/test_ds_shareholding.py`

- [ ] **Step 1: 寫失敗測試**

Create `tests/test_ds_shareholding.py`：
```python
import pandas as pd
from stock_strategies import datasources as ds


def test_shareholding_normalize_and_asof(monkeypatch):
    raw = pd.DataFrame({
        "date": pd.to_datetime(["2024-01-05", "2024-01-12", "2024-01-19"]),
        "ForeignInvestmentSharesRatio": [40.1, 40.5, 41.0],
    })
    monkeypatch.setattr(ds, "fetch_finmind_cached", lambda *a, **k: raw.copy())
    out = ds.get_shareholding("2330", "2024-01-01", as_of="2024-01-15")
    assert list(out.columns) == ["date", "foreign_ratio"]
    assert out["date"].max() <= pd.Timestamp("2024-01-15")  # 不含 01-19
    assert out.iloc[-1]["foreign_ratio"] == 40.5
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/test_ds_shareholding.py -q`
Expected: FAIL。

- [ ] **Step 3: 實作 `get_shareholding`**

在 `stock_strategies/datasources.py` 追加：
```python
def get_shareholding(stock_id: str, start: str, as_of: str | None = None) -> pd.DataFrame:
    """外資持股比例（週/不定期）。回 date, foreign_ratio（pct）。
    頻率不規則 → 因子層用 asof_row 取最近一筆。"""
    try:
        df = fetch_finmind_cached("TaiwanStockShareholding", stock_id, start, end_date=as_of)
    except FinMindRateLimitError:
        return pd.DataFrame()
    df = df.rename(columns={"ForeignInvestmentSharesRatio": "foreign_ratio"})
    if df.empty or not _require_cols(df, ["date", "foreign_ratio"]):
        return pd.DataFrame()
    df = df.copy()
    df["foreign_ratio"] = pd.to_numeric(df["foreign_ratio"], errors="coerce")
    return df[["date", "foreign_ratio"]].sort_values("date").reset_index(drop=True)
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run pytest tests/test_ds_shareholding.py -q`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add stock_strategies/datasources.py tests/test_ds_shareholding.py
git commit -m "feat: datasources.get_shareholding（外資持股比 + as_of 切片）"
```

---

## Task 10：`datasources.py` — `get_stock_info` 與 `get_capital_and_industry`

**Files:**
- Modify: `stock_strategies/datasources.py`
- Test: `tests/test_ds_info.py`

- [ ] **Step 1: 寫失敗測試**

Create `tests/test_ds_info.py`：
```python
import pandas as pd
from stock_strategies import datasources as ds


def test_stock_info_normalize(monkeypatch):
    raw = pd.DataFrame({
        "stock_id": ["2330", "2317"],
        "stock_name": ["台積電", "鴻海"],
        "industry_category": ["Semiconductor", "Electronics"],
        "type": ["twse", "twse"],
    })
    monkeypatch.setattr(ds, "fetch_finmind_cached", lambda *a, **k: raw.copy())
    out = ds.get_stock_info()
    assert {"stock_id", "stock_name", "industry_category", "market_type"}.issubset(out.columns)
    assert out.set_index("stock_id").loc["2330", "industry_category"] == "Semiconductor"


def test_capital_and_industry_market_cap(monkeypatch):
    info = pd.DataFrame({
        "stock_id": ["2330"], "stock_name": ["台積電"],
        "industry_category": ["Semiconductor"], "type": ["twse"],
    })
    # 財報股本（普通股，元）；收盤價 → 市值 = 股本/10 * 收盤
    fin = pd.DataFrame({
        "date": pd.to_datetime(["2023-12-31"]),
        "type": ["CommonStocksAndOrdinaryShares"],
        "value": [2_593_000_000_0.0],  # 任意股本
    })
    px = pd.DataFrame({"date": pd.to_datetime(["2024-01-02"]), "close": [600.0]})

    def fake_fetch(dataset, data_id, start, *a, **k):
        if dataset == "TaiwanStockInfo":
            return info.copy()
        if dataset == "TaiwanStockFinancialStatements":
            return fin.copy()
        if dataset == "TaiwanStockPrice":
            return px.copy()
        return pd.DataFrame()

    monkeypatch.setattr(ds, "fetch_finmind_cached", fake_fetch)
    out = ds.get_capital_and_industry("2330", as_of="2024-01-02")
    assert out["industry"] == "Semiconductor"
    assert out["shares_outstanding"] == 2_593_000_000_0.0
    assert out["market_cap"] == 2_593_000_000_0.0 / 10 * 600.0
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/test_ds_info.py -q`
Expected: FAIL。

- [ ] **Step 3: 實作兩個函式**

在 `stock_strategies/datasources.py` 追加：
```python
def get_stock_info(refresh: bool = False) -> pd.DataFrame:
    """全市場靜態資料（一次抓、長快取）。回 stock_id, stock_name,
       industry_category, market_type。"""
    try:
        df = fetch_finmind_cached(
            "TaiwanStockInfo", "", "1990-01-01", fresh_days=7, force_refresh=refresh
        )
    except FinMindRateLimitError:
        return pd.DataFrame()
    if df.empty or not _require_cols(df, ["stock_id"]):
        return pd.DataFrame()
    df = df.rename(columns={"type": "market_type"})
    keep = [c for c in ["stock_id", "stock_name", "industry_category", "market_type"] if c in df.columns]
    return df[keep].drop_duplicates(subset=["stock_id"]).reset_index(drop=True)


_COMMON_STOCK_TYPES = {
    "CommonStocksAndOrdinaryShares", "OrdinaryShare", "CommonStock", "CommonStocks",
}


def get_capital_and_industry(stock_id: str, as_of: str | None = None) -> dict:
    """回 {industry, shares_outstanding(元), market_cap(元 at as_of)}；缺則 None。
    市值 = 股本/10 × 收盤（面額10元 → 股數=股本/10）。"""
    out = {"industry": None, "shares_outstanding": None, "market_cap": None}
    info = get_stock_info()
    if not info.empty and stock_id in set(info["stock_id"]):
        row = info.set_index("stock_id").loc[stock_id]
        out["industry"] = row.get("industry_category")
    # 股本（普通股）
    try:
        fin = fetch_finmind_cached(
            "TaiwanStockFinancialStatements", stock_id, "2015-01-01", end_date=as_of
        )
    except FinMindRateLimitError:
        fin = pd.DataFrame()
    shares = None
    if not fin.empty and _require_cols(fin, ["type", "value"]):
        cap = fin[fin["type"].isin(_COMMON_STOCK_TYPES)]
        if not cap.empty:
            shares = float(pd.to_numeric(cap.sort_values("date")["value"], errors="coerce").dropna().iloc[-1])
    out["shares_outstanding"] = shares
    # 市值
    if shares:
        try:
            px = fetch_finmind_cached("TaiwanStockPrice", stock_id, "2015-01-01", end_date=as_of)
        except FinMindRateLimitError:
            px = pd.DataFrame()
        if not px.empty and "close" in px.columns:
            close = pd.to_numeric(px.sort_values("date")["close"], errors="coerce").dropna()
            if len(close):
                out["market_cap"] = shares / 10 * float(close.iloc[-1])
    return out
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run pytest tests/test_ds_info.py -q`
Expected: PASS（2 passed）。

- [ ] **Step 5: Commit**

```bash
git add stock_strategies/datasources.py tests/test_ds_info.py
git commit -m "feat: datasources.get_stock_info + get_capital_and_industry（市值計算）"
```

---

## Task 11：`datasources.py` — `get_index_history`（TAIEX/TWII fallback）

**Files:**
- Modify: `stock_strategies/datasources.py`
- Test: `tests/test_ds_index.py`

- [ ] **Step 1: 寫失敗測試（spec 測試點 10）**

Create `tests/test_ds_index.py`：
```python
import pandas as pd
from stock_strategies import datasources as ds


def test_index_fallback_taiex_to_twii(monkeypatch):
    twii = pd.DataFrame({
        "date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
        "open": [17000, 17100], "max": [17050, 17150],
        "min": [16950, 17050], "close": [17020, 17120],
    })

    def fake_fetch(dataset, data_id, start, *a, **k):
        if data_id == "TAIEX":
            return pd.DataFrame()      # TAIEX 抓不到
        if data_id == "TWII":
            return twii.copy()
        return pd.DataFrame()

    monkeypatch.setattr(ds, "fetch_finmind_cached", fake_fetch)
    out = ds.get_index_history("TAIEX", start="2024-01-01")
    assert {"date", "open", "high", "low", "close"}.issubset(out.columns)
    assert len(out) == 2
    assert out.iloc[1]["high"] == 17150   # max→high 正規化
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/test_ds_index.py -q`
Expected: FAIL。

- [ ] **Step 3: 實作 `get_index_history`**

在 `stock_strategies/datasources.py` 追加：
```python
_INDEX_FALLBACK = {"TAIEX": ["TAIEX", "TWII"], "TWII": ["TWII", "TAIEX"]}


def get_index_history(index_id: str = "TAIEX", start: str | None = None,
                      as_of: str | None = None) -> pd.DataFrame:
    """大盤指數（日）。回 date, open, high, low, close。
    依序試 TAIEX/TWII（沿用 market.py 慣例）。"""
    start = start or "2015-01-01"
    for did in _INDEX_FALLBACK.get(index_id, [index_id]):
        try:
            df = fetch_finmind_cached("TaiwanStockPrice", did, start, end_date=as_of)
        except FinMindRateLimitError:
            continue
        if df.empty:
            continue
        df = df.rename(columns={"max": "high", "min": "low"})
        for c in ["open", "high", "low", "close"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        keep = [c for c in ["date", "open", "high", "low", "close"] if c in df.columns]
        return df[keep].sort_values("date").reset_index(drop=True)
    return pd.DataFrame()
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run pytest tests/test_ds_index.py -q`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add stock_strategies/datasources.py tests/test_ds_index.py
git commit -m "feat: datasources.get_index_history（TAIEX/TWII fallback + OHLC 正規化）"
```

---

## Task 12：`market.py` 重構走 `get_index_history`（行為回歸）

**Files:**
- Modify: `stock_strategies/market.py:21-51`
- Test: `tests/test_market_regression.py`

- [ ] **Step 1: 寫回歸測試**

Create `tests/test_market_regression.py`：
```python
import pandas as pd
from stock_strategies import market


def test_market_state_bullish(monkeypatch):
    # 造一段站上 20 日線的指數
    n = 40
    dates = pd.bdate_range("2024-01-01", periods=n)
    close = [17000 + i * 10 for i in range(n)]  # 持續上升 → 站上月線
    idx = pd.DataFrame({"date": dates, "open": close, "high": close,
                        "low": close, "close": close})
    monkeypatch.setattr(market, "get_index_history", lambda *a, **k: idx.copy())
    state = market.get_market_state(ma_period=20)
    assert state["bullish"] is True
    assert state["close"] == close[-1]


def test_market_state_handles_empty(monkeypatch):
    monkeypatch.setattr(market, "get_index_history", lambda *a, **k: pd.DataFrame())
    state = market.get_market_state()
    assert state["bullish"] is True   # 資料不足 → 不套濾鏡（沿用原行為）
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/test_market_regression.py -q`
Expected: FAIL（`market` 尚未有 `get_index_history` 屬性可被 patch，或 `_fetch_taiex` 仍直接打 API）。

- [ ] **Step 3: 重構 `market.py`**

修改 `stock_strategies/market.py`：
1. 頂部加 `from .datasources import get_index_history`（放在現有 import 區）。
2. 刪除 `_fetch_taiex` 整個函式（21-51 行）與不再需要的 `TAIEX_IDS`、`requests`、`os` import（若僅此處使用）。
3. 把 `get_market_state` 內 `df = self._fetch_taiex()` 改為：
```python
def get_market_state(ma_period: int = 20) -> dict:
    """回傳大盤狀態 dict（可指定均線天數，預設 20=月線）"""
    try:
        df = get_index_history("TAIEX")
        if len(df) < ma_period + 1:
            return {"bullish": True, "close": None, "ma20": None,
                    "note": "⚠️ 大盤資料不足，暫不套用濾鏡"}
        df = df.copy()
        df["ma20"] = df["close"].rolling(ma_period).mean()
        latest = df.iloc[-1]
        close = float(latest["close"]); ma20 = float(latest["ma20"])
        bullish = close > ma20
        pct = (close / ma20 - 1) * 100
        if bullish:
            note = f"🟢 加權 {close:.0f} 站上 {ma_period} 日線 ({pct:+.1f}%)，BUY 訊號照常發出"
        else:
            note = f"🔴 加權 {close:.0f} 跌破 {ma_period} 日線 ({pct:+.1f}%)，BUY 全數降為 WATCH"
        return {"bullish": bullish, "close": close, "ma20": ma20, "note": note}
    except Exception as e:
        return {"bullish": True, "close": None, "ma20": None,
                "note": f"⚠️ 大盤狀態取得失敗（{str(e)[:60]}），暫不套用濾鏡"}
```
（`apply_market_filter` 不動。）

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run pytest tests/test_market_regression.py -q`
Expected: PASS（2 passed）。

- [ ] **Step 5: Commit**

```bash
git add stock_strategies/market.py tests/test_market_regression.py
git commit -m "refactor: market.py 改用 datasources.get_index_history（行為不變）"
```

---

## Task 13：`context.py` — `FactorContext` 資料結構（C1 契約）

**Files:**
- Create: `stock_strategies/context.py`
- Test: `tests/test_context_dataclass.py`

- [ ] **Step 1: 寫失敗測試**

Create `tests/test_context_dataclass.py`：
```python
import pandas as pd
from stock_strategies.context import FactorContext


def _mk():
    px = pd.DataFrame({"date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
                       "close": [10.0, 11.0]})
    sh = pd.DataFrame({"date": pd.to_datetime(["2024-01-01", "2024-01-08"]),
                       "foreign_ratio": [40.0, 41.0]})
    return FactorContext(
        stock_id="2330", as_of=pd.Timestamp("2024-01-05"),
        price_df=px, index_df=pd.DataFrame(), inst=pd.DataFrame(),
        revenue=pd.DataFrame(), valuation=pd.DataFrame(), margin=pd.DataFrame(),
        shareholding=sh, fundamentals={"eps": {}, "roe": {}},
        industry="Semiconductor", shares_outstanding=None, market_cap=None,
    )


def test_latest_price_returns_last_row():
    ctx = _mk()
    assert ctx.latest_price()["close"] == 11.0


def test_asof_row_picks_last_before_asof():
    ctx = _mk()
    row = ctx.asof_row("shareholding")
    assert row is not None
    # shareholding 有 2024-01-01 與 2024-01-08，as_of=2024-01-05
    # → date<=as_of 的最後一筆是 2024-01-01（foreign_ratio=40.0）
    assert row["foreign_ratio"] == 40.0
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/test_context_dataclass.py -q`
Expected: FAIL（`ModuleNotFoundError: stock_strategies.context`）。

- [ ] **Step 3: 實作 `FactorContext`**

Create `stock_strategies/context.py`：
```python
"""FactorContext（C1 唯一定義）與 point-in-time 建構器。

契約：欄位名一律 price_df/index_df；as_of 為 pd.Timestamp；
後續因子層/回測層一律 `from .context import FactorContext`，禁止 redefine。
price_df 進 ctx 時尚未 add_indicators，由消費端統一呼叫一次。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd


@dataclass
class FactorContext:
    stock_id: str
    as_of: pd.Timestamp
    price_df: pd.DataFrame
    index_df: pd.DataFrame
    inst: pd.DataFrame
    revenue: pd.DataFrame
    valuation: pd.DataFrame
    margin: pd.DataFrame
    shareholding: pd.DataFrame
    fundamentals: dict
    industry: str | None = None
    shares_outstanding: float | None = None
    market_cap: float | None = None
    meta: dict = field(default_factory=dict)

    def latest_price(self) -> pd.Series | None:
        """取 date<=as_of 的最後一筆報價（停牌則為停牌前最後成交）。"""
        if self.price_df is None or self.price_df.empty:
            return None
        df = self.price_df
        if "date" in df.columns:
            df = df[df["date"] <= self.as_of]
        return df.iloc[-1] if len(df) else None

    def asof_row(self, df_name: str) -> pd.Series | None:
        """對不規則頻率資料取 date<=as_of 的最後一筆。"""
        df = getattr(self, df_name, None)
        if df is None or df.empty or "date" not in df.columns:
            return None
        sub = df[df["date"] <= self.as_of]
        return sub.iloc[-1] if len(sub) else None
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run pytest tests/test_context_dataclass.py -q`
Expected: PASS（2 passed）。

- [ ] **Step 5: Commit**

```bash
git add stock_strategies/context.py tests/test_context_dataclass.py
git commit -m "feat: context.FactorContext（C1 契約 + latest_price/asof_row）"
```

---

## Task 14：`context.py` — `build_context_from_bundle`（C2 純切片 + 財報/新股保護）

**Files:**
- Modify: `stock_strategies/context.py`
- Test: `tests/test_context_from_bundle.py`

- [ ] **Step 1: 寫失敗測試（spec 測試點 5、7）**

Create `tests/test_context_from_bundle.py`：
```python
import pandas as pd
from stock_strategies import context as ctxmod
from stock_strategies.context import build_context_from_bundle


def _bundle(price_rows=120):
    dates = pd.bdate_range("2022-01-03", periods=price_rows)
    price = pd.DataFrame({"date": dates, "open": 1.0, "high": 1.0, "low": 1.0,
                          "close": [10.0 + i * 0.1 for i in range(price_rows)], "volume": 1000})
    return {
        "price": price,
        "index": pd.DataFrame({"date": dates, "close": 17000.0}),
        "inst": pd.DataFrame(),
        "revenue": pd.DataFrame(),
        "valuation": pd.DataFrame(),
        "margin": pd.DataFrame(),
        "shareholding": pd.DataFrame(),
        "fundamentals_raw": {"eps": {2022: 30.0, 2023: 32.0}, "roe": {2022: 25.0, 2023: 26.0}},
        "capital": {"industry": "Semiconductor", "shares_outstanding": None, "market_cap": None},
    }


def test_fundamentals_asof_publish_date():
    b = _bundle()
    # 2023 年度 EPS 發布日 = 2024-03-31
    ctx = build_context_from_bundle("2330", pd.Timestamp("2024-03-30"), b)
    assert 2023 not in ctx.fundamentals["eps"]   # 還沒發布
    assert 2022 in ctx.fundamentals["eps"]
    ctx2 = build_context_from_bundle("2330", pd.Timestamp("2024-03-31"), b)
    assert 2023 in ctx2.fundamentals["eps"]      # 發布日當天可用


def test_new_stock_protection_flag():
    b = _bundle(price_rows=30)   # 少於 MIN_PRICE_ROWS(60)
    ctx = build_context_from_bundle("9999", pd.Timestamp("2022-03-01"), b)
    assert "price_history_insufficient" in ctx.meta.get("missing", [])
    # 不 raise，仍回 context


def test_price_sliced_to_asof():
    b = _bundle()
    ctx = build_context_from_bundle("2330", pd.Timestamp("2022-02-01"), b)
    assert ctx.price_df["date"].max() <= pd.Timestamp("2022-02-01")
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/test_context_from_bundle.py -q`
Expected: FAIL（`AttributeError: ... 'build_context_from_bundle'`）。

- [ ] **Step 3: 實作財報常數與 `build_context_from_bundle`**

在 `stock_strategies/context.py` 補 import 與實作：
```python
from .config import MIN_PRICE_ROWS


def _fundamentals_asof(fund_raw: dict, as_of: pd.Timestamp) -> dict:
    """年度 EPS/ROE 以發布日切片：年度 y 的可用日 = (y+1)-03-31。"""
    out = {"eps": {}, "roe": {}}
    for key in ("eps", "roe"):
        for year, val in (fund_raw.get(key) or {}).items():
            publish = pd.Timestamp(year=int(year) + 1, month=3, day=31)
            if publish <= as_of:
                out[key][int(year)] = val
    return out


def _slice_to(df: pd.DataFrame, as_of: pd.Timestamp, date_col: str = "date") -> pd.DataFrame:
    if df is None or df.empty or date_col not in df.columns:
        return df if df is not None else pd.DataFrame()
    return df[df[date_col] <= as_of].reset_index(drop=True)


def build_context_from_bundle(
    stock_id: str, as_of: pd.Timestamp, raw_bundle: dict
) -> FactorContext:
    """純切片組裝（無 IO）。回測逐日呼叫；raw_bundle 為一次抓好的全期資料。
    各資料塊一律以 as_of 為硬上界；月營收用 avail_date、財報用發布日。"""
    as_of = pd.Timestamp(as_of)
    meta: dict = {"warnings": [], "missing": []}

    price_df = _slice_to(raw_bundle.get("price", pd.DataFrame()), as_of)
    if price_df is None or len(price_df) < MIN_PRICE_ROWS:
        meta["missing"].append("price_history_insufficient")

    index_df = _slice_to(raw_bundle.get("index", pd.DataFrame()), as_of)
    inst = _slice_to(raw_bundle.get("inst", pd.DataFrame()), as_of)
    # 月營收以 avail_date 切（loader 已算 avail_date 欄）
    rev = raw_bundle.get("revenue", pd.DataFrame())
    revenue = _slice_to(rev, as_of, date_col="avail_date") if "avail_date" in getattr(rev, "columns", []) else _slice_to(rev, as_of)
    valuation = _slice_to(raw_bundle.get("valuation", pd.DataFrame()), as_of)
    margin = _slice_to(raw_bundle.get("margin", pd.DataFrame()), as_of)
    shareholding = _slice_to(raw_bundle.get("shareholding", pd.DataFrame()), as_of)
    fundamentals = _fundamentals_asof(raw_bundle.get("fundamentals_raw", {}), as_of)
    capital = raw_bundle.get("capital", {}) or {}

    for name, df in [("inst", inst), ("revenue", revenue), ("valuation", valuation),
                     ("margin", margin), ("shareholding", shareholding)]:
        if df is None or df.empty:
            meta["missing"].append(name)

    return FactorContext(
        stock_id=stock_id, as_of=as_of,
        price_df=price_df if price_df is not None else pd.DataFrame(),
        index_df=index_df, inst=inst, revenue=revenue, valuation=valuation,
        margin=margin, shareholding=shareholding, fundamentals=fundamentals,
        industry=capital.get("industry"),
        shares_outstanding=capital.get("shares_outstanding"),
        market_cap=capital.get("market_cap"),
        meta=meta,
    )
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run pytest tests/test_context_from_bundle.py -q`
Expected: PASS（3 passed）。

- [ ] **Step 5: Commit**

```bash
git add stock_strategies/context.py tests/test_context_from_bundle.py
git commit -m "feat: context.build_context_from_bundle（C2 純切片 + 財報發布日 + 新股保護）"
```

---

## Task 15：`context.py` — `_gather_raw_bundle` 與 `build_context`（C2 抓一次 + 切片）

**Files:**
- Modify: `stock_strategies/context.py`
- Test: `tests/test_build_context.py`

- [ ] **Step 1: 寫失敗測試（spec 測試點 9，point-in-time 單調）**

Create `tests/test_build_context.py`：
```python
import pandas as pd
from stock_strategies import context as ctxmod


def _install_fakes(monkeypatch):
    dates = pd.bdate_range("2022-01-03", periods=400)
    price = pd.DataFrame({"date": dates, "open": 1.0, "high": 1.0, "low": 1.0,
                          "close": [10.0 + i * 0.05 for i in range(len(dates))], "volume": 1000})
    idx = pd.DataFrame({"date": dates, "open": 17000.0, "high": 17000.0,
                        "low": 17000.0, "close": 17000.0})
    monkeypatch.setattr(ctxmod, "get_price_history_cached", lambda *a, **k: price.copy())
    monkeypatch.setattr(ctxmod.ds, "get_index_history", lambda *a, **k: idx.copy())
    monkeypatch.setattr(ctxmod.ds, "get_institutional", lambda *a, **k: pd.DataFrame())
    monkeypatch.setattr(ctxmod.ds, "get_month_revenue", lambda *a, **k: pd.DataFrame())
    monkeypatch.setattr(ctxmod.ds, "get_valuation", lambda *a, **k: pd.DataFrame())
    monkeypatch.setattr(ctxmod.ds, "get_margin", lambda *a, **k: pd.DataFrame())
    monkeypatch.setattr(ctxmod.ds, "get_shareholding", lambda *a, **k: pd.DataFrame())
    monkeypatch.setattr(ctxmod, "_get_fundamentals_raw", lambda sid: {"eps": {2022: 30.0}, "roe": {2022: 25.0}})
    monkeypatch.setattr(ctxmod.ds, "get_capital_and_industry",
                        lambda *a, **k: {"industry": "Semiconductor", "shares_outstanding": None, "market_cap": None})


def test_build_context_point_in_time_monotonic(monkeypatch):
    _install_fakes(monkeypatch)
    c1 = ctxmod.build_context("2330", "2022-06-01")
    c2 = ctxmod.build_context("2330", "2023-06-01")
    assert c1.price_df["date"].max() <= pd.Timestamp("2022-06-01")
    assert c2.price_df["date"].max() <= pd.Timestamp("2023-06-01")
    # t1 的價格是 t2 的子集（單調，無未來資訊）
    assert c1.price_df["date"].max() < c2.price_df["date"].max()
    assert c1.as_of == pd.Timestamp("2022-06-01")


def test_build_context_strict_false_survives_missing(monkeypatch):
    _install_fakes(monkeypatch)
    # 法人等回空 → meta.missing 有記錄，但不 raise
    c = ctxmod.build_context("2330", "2022-06-01")
    assert "inst" in c.meta["missing"]
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/test_build_context.py -q`
Expected: FAIL（`AttributeError: ... 'build_context'` 或 `'ds'`）。

- [ ] **Step 3: 實作 `_gather_raw_bundle` 與 `build_context`**

在 `stock_strategies/context.py` 補 import 與實作：
```python
from . import datasources as ds
from .cache import fetch_finmind_cached, FinMindRateLimitError


def get_price_history_cached(stock_id: str, start: str, as_of: str | None = None) -> pd.DataFrame:
    """走快取的個股日 K（取代 data.get_price_history 在 context 內的用途）。"""
    df = fetch_finmind_cached("TaiwanStockPrice", stock_id, start, end_date=as_of)
    if df.empty:
        return df
    df = df.rename(columns={"max": "high", "min": "low", "Trading_Volume": "volume"})
    for c in ["open", "high", "low", "close", "volume"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    keep = [c for c in ["date", "open", "high", "low", "close", "volume"] if c in df.columns]
    return df[keep].sort_values("date").reset_index(drop=True)


def _get_fundamentals_raw(stock_id: str) -> dict:
    """年度 EPS/ROE 原始值（不切發布日，由 from_bundle 切）。"""
    try:
        df = fetch_finmind_cached("TaiwanStockFinancialStatements", stock_id, "2015-01-01")
    except FinMindRateLimitError:
        return {"eps": {}, "roe": {}}
    if df.empty or not all(c in df.columns for c in ["date", "type", "value"]):
        return {"eps": {}, "roe": {}}
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["year"] = df["date"].dt.year
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    eps = df[df["type"] == "EPS"].groupby("year")["value"].sum().to_dict()
    roe = df[df["type"] == "ROE"].groupby("year")["value"].sum().to_dict()
    return {"eps": {int(y): round(float(v), 2) for y, v in eps.items()},
            "roe": {int(y): round(float(v), 2) for y, v in roe.items()}}


def _gather_raw_bundle(stock_id: str, start: str, lookback_years: int) -> dict:
    """一次抓全期資料（回測前置）。不切 as_of；切片交給 from_bundle。"""
    return {
        "price": get_price_history_cached(stock_id, start),
        "index": ds.get_index_history("TAIEX", start),
        "inst": ds.get_institutional(stock_id, start),
        "revenue": ds.get_month_revenue(stock_id, start),
        "valuation": ds.get_valuation(stock_id, start),
        "margin": ds.get_margin(stock_id, start),
        "shareholding": ds.get_shareholding(stock_id, start),
        "fundamentals_raw": _get_fundamentals_raw(stock_id),
        "capital": ds.get_capital_and_industry(stock_id),
    }


def build_context(
    stock_id: str,
    as_of_date: str,
    *,
    lookback_years: int = 5,
    info_df: pd.DataFrame | None = None,
    strict: bool = False,
) -> FactorContext:
    """runtime 單檔用：抓一次全期 → from_bundle 切到 as_of。
    strict=True 時資料缺漏 raise；False(預設) 記 meta 回中性。"""
    as_of = pd.Timestamp(as_of_date)
    start = (as_of - pd.DateOffset(years=lookback_years) - pd.Timedelta(days=60)).strftime("%Y-%m-%d")
    try:
        bundle = _gather_raw_bundle(stock_id, start, lookback_years)
    except Exception:
        if strict:
            raise
        bundle = {"price": pd.DataFrame(), "index": pd.DataFrame(), "inst": pd.DataFrame(),
                  "revenue": pd.DataFrame(), "valuation": pd.DataFrame(), "margin": pd.DataFrame(),
                  "shareholding": pd.DataFrame(), "fundamentals_raw": {"eps": {}, "roe": {}},
                  "capital": {}}
    ctx = build_context_from_bundle(stock_id, as_of, bundle)
    if strict and ctx.meta.get("missing"):
        raise RuntimeError(f"build_context strict: 缺資料 {ctx.meta['missing']}")
    return ctx
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run pytest tests/test_build_context.py -q`
Expected: PASS（2 passed）。

- [ ] **Step 5: 跑全套測試**

Run: `uv run pytest -q`
Expected: 全綠（前面所有 task 的測試都通過）。

- [ ] **Step 6: Commit**

```bash
git add stock_strategies/context.py tests/test_build_context.py
git commit -m "feat: context.build_context + _gather_raw_bundle（C2 抓一次逐日切片）"
```

---

## Task 16：`data.py` 內部走 `fetch_finmind_cached`（純加速，回歸）

**Files:**
- Modify: `stock_strategies/data.py:53-85`
- Test: `tests/test_data_cached_regression.py`

- [ ] **Step 1: 寫回歸測試**

Create `tests/test_data_cached_regression.py`：
```python
import pandas as pd
from stock_strategies import data


def test_get_price_history_uses_cache(monkeypatch):
    raw = pd.DataFrame({
        "date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
        "open": [10, 11], "max": [10.5, 11.5], "min": [9.5, 10.5],
        "close": [10.2, 11.2], "Trading_Volume": [1000, 1100],
    })
    monkeypatch.setattr(data, "fetch_finmind_cached", lambda *a, **k: raw.copy())
    df = data.get_price_history("2330", years=1)
    assert {"date", "open", "high", "low", "close", "volume"}.issubset(df.columns)
    assert df.iloc[1]["high"] == 11.5    # max→high
    assert df.iloc[1]["volume"] == 1100  # Trading_Volume→volume
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/test_data_cached_regression.py -q`
Expected: FAIL（`AttributeError: module 'stock_strategies.data' has no attribute 'fetch_finmind_cached'`）。

- [ ] **Step 3: 改 `data.py` 內部走快取**

修改 `stock_strategies/data.py`：
1. 頂部加 `from .cache import fetch_finmind_cached`。
2. `get_price_history` 改為：
```python
def get_price_history(stock_id: str, years: int = 3) -> pd.DataFrame:
    start = (datetime.now() - timedelta(days=365 * years + 60)).strftime("%Y-%m-%d")
    df = fetch_finmind_cached("TaiwanStockPrice", stock_id, start)
    if df.empty:
        return df
    df = df.rename(columns={"max": "high", "min": "low", "Trading_Volume": "volume"})
    for col in ["open", "high", "low", "close", "volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.sort_values("date").reset_index(drop=True)
```
3. `get_fundamental` 內 `df = fetch_finmind(...)` 改為 `df = fetch_finmind_cached("TaiwanStockFinancialStatements", stock_id, start)`（其餘解析邏輯不變）。
4. 保留舊 `fetch_finmind`（其他地方仍可用、向後相容）。

- [ ] **Step 4: 跑測試確認通過 + 全套回歸**

Run: `uv run pytest -q`
Expected: 全綠。

- [ ] **Step 5: Commit**

```bash
git add stock_strategies/data.py tests/test_data_cached_regression.py
git commit -m "perf: data.get_price_history/get_fundamental 改走 fetch_finmind_cached（快取加速）"
```

---

## Task 17：live 整合校正（手動，校 ★ 欄位 rename 表）

> 這一步**會打真實 FinMind API**（需 `FINMIND_TOKEN`），用來校正 spec 中標 ★ 的欄位名假設。不進 CI。

**Files:**
- Create: `tests/test_live_finmind.py`

- [ ] **Step 1: 寫 live 測試（標 `@pytest.mark.live`）**

Create `tests/test_live_finmind.py`：
```python
import pytest
from stock_strategies import datasources as ds


@pytest.mark.live
def test_live_institutional_2330():
    out = ds.get_institutional("2330", "2024-01-01", as_of="2024-06-30")
    assert not out.empty
    assert {"foreign_net", "trust_net", "dealer_net", "total_net"}.issubset(out.columns)


@pytest.mark.live
def test_live_revenue_2330():
    out = ds.get_month_revenue("2330", "2023-01-01", as_of="2024-06-30")
    assert not out.empty
    assert "avail_date" in out.columns


@pytest.mark.live
def test_live_valuation_2330():
    out = ds.get_valuation("2330", "2024-01-01", as_of="2024-06-30")
    assert not out.empty
    assert "per" in out.columns


@pytest.mark.live
def test_live_index():
    out = ds.get_index_history("TAIEX", start="2024-01-01", as_of="2024-06-30")
    assert not out.empty
```

- [ ] **Step 2: 跑 live 測試並校正**

Run: `uv run pytest tests/test_live_finmind.py -m live -v`
Expected: 若有 ★ 欄位名不符（如 `dividend_yield` 實際叫別名），測試會失敗。**根據真實回傳調整對應 loader 的 `rename` 字典**，直到全綠。記錄每個 dataset 的真實欄位於 commit message。

- [ ] **Step 3: Commit（含校正）**

```bash
git add tests/test_live_finmind.py stock_strategies/datasources.py
git commit -m "test: FinMind live 整合校正 ★ 欄位 rename 表（2330 實機驗證）"
```

---

## 完成標準（Definition of Done）

- [ ] `uv run pytest -q` 全綠（不含 live）。
- [ ] `uv run pytest -m live -v` 對 2330 全綠（手動跑過一次，★ 欄位已校正）。
- [ ] `uv run python main.py` 仍可正常跑（data.py 改快取後的回歸；可乾跑確認無 import error）。
- [ ] `.cache/` 已被 git 忽略，未誤入版控。
- [ ] 介面凍結：`FactorContext`、`build_context`、`build_context_from_bundle`、各 loader 簽名與 spec §4/§6 一致，供 P2 因子庫 `import`。

---

## Self-Review 紀錄（writing-plans 規定，作者自審）

**1. Spec 覆蓋**：§6 的 10 個測試點 → Task 對應：① 快取命中=Task4、② 增量=Task4、③ 限流退避=Task3、④ 月營收 look-ahead=Task6、⑤ 財報 as-of=Task14、⑥ 法人 pivot=Task5、⑦ 新股保護=Task14、⑧ 欄位缺失韌性=Task7、⑨ build_context 單調=Task15、⑩ TAIEX fallback=Task11。§6 實作順序 5 步 → Task1-16 全覆蓋。C1/C2 契約 → Task13-15。**無遺漏**。

**2. Placeholder 掃描**：每個 code step 均含完整可跑碼，無 TBD/TODO/「類似 TaskN」。

**3. 型別一致**：`fetch_finmind_cached(dataset, data_id, start_date, end_date=None, *, ...)`、各 `get_*(stock_id, start, as_of=None)`、`get_index_history(index_id, start, as_of)`、`get_capital_and_industry(...)->dict`、`FactorContext(...)`、`build_context_from_bundle(stock_id, as_of, raw_bundle)`、`build_context(stock_id, as_of_date, *, lookback_years, info_df, strict)` 全程一致。

**已知後續對接點（給 P2）**：因子層一律 `from stock_strategies.context import FactorContext, build_context, build_context_from_bundle`；回測逐日推進用 `_gather_raw_bundle` 一次 + `build_context_from_bundle` 逐日；`build_panel`（P2/P3 交界）尚未在本計畫實作，屬 P2。
