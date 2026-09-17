# P2：M2 因子庫 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development 或 superpowers:executing-plans 逐 task 實作。Steps 用 checkbox 追蹤。

**Goal:** 建立 `stock_strategies/factors/` 因子庫：7 流派共 23 個可量化因子 + legacy 包裝 + FACTOR_REGISTRY + build_panel，每個因子 `compute(ctx, params) -> float|None`，可在歷史任一日無 look-ahead 計算，供回測引擎與 runtime 共用。

**Architecture:** 因子是純函式，輸入 P1 的 `FactorContext`（唯一定義在 `stock_strategies/context.py`），輸出 0..1（None=缺料）。registry 統一缺料防護與例外兜底。build_panel 把股池逐檔逐日攤平成 `factor__<name>` 欄供回測。

**Tech Stack:** Python 3.11、pandas、numpy、pytest；套件用 uv。

**對應 spec：** `docs/superpowers/specs/2026-06-13-multi-expert-stock-strategy-design.md` §7（因子庫設計）、§4（C1/C3/C7 契約）。**依賴 P1**（context/datasources/cache 已在 `feat/p1-data-layer`）。

---

## ⚠️ 契約對齊（最重要——spec §7 原文與 P1 實際介面不一致，一律以本節為準）

spec §7 是設計階段版本，與 §4 契約裁決後的 P1 實作有以下必須調整。implementer 讀 §7 因子公式時，一律套用這 7 條：

1. **不重建 FactorContext**：用 P1 的 `from stock_strategies.context import FactorContext`，禁止新建 `factors/context.py`。欄位名以 P1 為準：`price_df`（不是 §7 的 `price`）、`index_df`、`inst`、`revenue`、`valuation`、`margin`、`shareholding`、`fundamentals`、`industry`、`shares_outstanding`、`market_cap`、`meta`。§7 因子碼中所有 `ctx.price` → `ctx.price_df`；`ctx.has(x)` → base.py 的 `has_rows(ctx, x, n)`。
2. **price_df 已含技術指標**：Task 1 會在 P1 的 `build_context_from_bundle` 末段對 price_df 跑一次 `add_indicators`，因子可直接讀 `ma5/ma20/ma60/bb_upper/bb_mid/bb_lower/k/d/dif/dea/macd_hist/atr`。
3. **缺料回 `None`（§4 C3，覆蓋 §7 §1.3 的 0.5）**：required_data 對應 df 整個缺（None/empty/列數 < lookback_min）→ registry 回 `None`；因子內部有 df 但樣本不足/NaN/算不出 → 回 `0.5`。`compute_all_factors`：`composite = Σ(score·weight)/Σ(weight)`，只對非 None 計入分子分母。
4. **eps_q 單季**：growth 因子需單季 EPS。Task 1 擴充 P1 `context.py:_get_fundamentals_raw`，新增回傳 `eps_q: {(year,quarter): value}`。
5. **欄位名對齊 P1**：revenue 時間欄是 `avail_date`（非 announce_date），另有 `period/revenue_year/revenue_month/revenue/mom/yoy`；inst 為 `foreign_net/trust_net/dealer_net/total_net`（單位股，因子用 ratio/正負不受影響）；valuation `per/pbr/dividend_yield`；margin `margin_balance/short_balance/margin_chg/short_chg/short_margin_ratio`；shareholding `foreign_ratio`。
6. **因子欄前綴 `factor__`（§4 C7）**：build_panel 攤平時欄名一律 `factor__<name>`。
7. **registry 缺料判定改 P1 介面**：FactorContext 無 `has()` 方法，改用 base.py 的 `has_rows(ctx, attr, min_rows)`。

---

## File Structure

| 檔案 | 職責 | 動作 |
| --- | --- | --- |
| `stock_strategies/context.py` | build_context 末段加 add_indicators；_get_fundamentals_raw 加 eps_q | Modify (P1) |
| `stock_strategies/factors/__init__.py` | 匯出 registry API + 觸發各 school 註冊 | Create |
| `stock_strategies/factors/base.py` | clip01/rank_pct/zscore_clip/NEUTRAL/has_rows | Create |
| `stock_strategies/factors/registry.py` | FACTOR_REGISTRY/register/compute_factor/compute_all_factors/list_factors | Create |
| `stock_strategies/factors/{value,growth,momentum,chips,revenue,reversal,breakout}.py` | 七派 23 因子 | Create |
| `stock_strategies/factors/legacy.py` | 舊四訊號+量價包裝 6 因子 + legacy_params_to_factors | Create |
| `stock_strategies/factors/panel.py` | build_panel（C7 交界，factor__ 攤平） | Create |
| `tests/factors/` | 對應 spec §7 §7 的測試點 | Create |

---

## Task 1：P1 context.py 升級（add_indicators + eps_q）

**Files:** Modify `stock_strategies/context.py`；Test `tests/test_context_indicators.py`

- [ ] **Step 1: 寫失敗測試**

Create `tests/test_context_indicators.py`：
```python
import pandas as pd
from stock_strategies.context import build_context_from_bundle


def _bundle(n=120):
    dates = pd.bdate_range("2022-01-03", periods=n)
    price = pd.DataFrame({"date": dates, "open": 1.0, "high": 1.0, "low": 1.0,
                          "close": [10.0 + i * 0.1 for i in range(n)], "volume": 1000})
    return {"price": price, "index": pd.DataFrame(), "inst": pd.DataFrame(),
            "revenue": pd.DataFrame(), "valuation": pd.DataFrame(), "margin": pd.DataFrame(),
            "shareholding": pd.DataFrame(), "fundamentals_raw": {"eps": {}, "roe": {}},
            "capital": {}}


def test_price_df_has_indicators():
    ctx = build_context_from_bundle("2330", pd.Timestamp("2022-12-31"), _bundle())
    for col in ["ma5", "ma20", "ma60", "bb_upper", "bb_lower", "k", "d", "macd_hist", "atr"]:
        assert col in ctx.price_df.columns
```

- [ ] **Step 2: 跑確認失敗** — `uv run pytest tests/test_context_indicators.py -q` → FAIL

- [ ] **Step 3: 改 context.py**：頂部 import 加 `from .indicators import add_indicators`。在 `build_context_from_bundle` 內 `price_df = _slice_to(...)` 之後插入：
```python
    if price_df is not None and not price_df.empty:
        price_df = add_indicators(price_df)
```

- [ ] **Step 4: 跑確認通過 + 全套回歸** — `uv run pytest -q` → 全綠

- [ ] **Step 5: eps_q 寫失敗測試**，在 `tests/test_context_indicators.py` 追加：
```python
def test_fundamentals_raw_has_eps_q(monkeypatch):
    from stock_strategies import context as ctxmod
    fin = pd.DataFrame({"date": pd.to_datetime(["2023-03-31", "2023-06-30"]),
                        "type": ["EPS", "EPS"], "value": [2.5, 3.0]})
    monkeypatch.setattr(ctxmod, "fetch_finmind_cached", lambda *a, **k: fin.copy())
    out = ctxmod._get_fundamentals_raw("2330")
    assert "eps_q" in out
    assert out["eps_q"][(2023, 1)] == 2.5
    assert out["eps_q"][(2023, 2)] == 3.0
```

- [ ] **Step 6: 實作 eps_q**：在 `_get_fundamentals_raw` 回傳前加（季別由月份推 3→Q1/6→Q2/9→Q3/12→Q4）：
```python
    eps_rows = df[df["type"] == "EPS"].copy()
    eps_q = {}
    if not eps_rows.empty:
        eps_rows["quarter"] = eps_rows["date"].dt.month.map({3: 1, 6: 2, 9: 3, 12: 4})
        for _, r in eps_rows.dropna(subset=["quarter", "value"]).iterrows():
            eps_q[(int(r["year"]), int(r["quarter"]))] = round(float(r["value"]), 2)
```
回傳改 `{"eps": {...}, "roe": {...}, "eps_q": eps_q}`。

- [ ] **Step 7: 跑確認通過 + Commit**
```bash
git add stock_strategies/context.py tests/test_context_indicators.py
git commit -m "feat: context price_df 末段加 add_indicators + fundamentals 增 eps_q（P2 前置）"
```

---

## Task 2：factors/base.py（工具函式，spec §7 §7 測試點 1-3）

**Files:** Create `stock_strategies/factors/__init__.py`（暫空）、`stock_strategies/factors/base.py`；Test `tests/factors/__init__.py`、`tests/factors/test_base.py`

- [ ] **Step 1:** Create `tests/factors/__init__.py`（空）+ `tests/factors/test_base.py`：
```python
import pandas as pd
from stock_strategies.factors.base import clip01, rank_pct, zscore_clip


def test_clip01_bounds():
    assert clip01(1.5) == 1.0
    assert clip01(-0.2) == 0.0
    assert clip01(float("nan")) == 0.5
    assert clip01(None) == 0.5


def test_rank_pct():
    s = pd.Series([1, 2, 3, 4])
    assert rank_pct(s, 4) == 1.0
    assert rank_pct(s, 1) == 0.25
    assert rank_pct(pd.Series([], dtype=float), 1) == 0.5


def test_zscore_clip():
    assert zscore_clip(10, 10, 2) == 0.5
    assert zscore_clip(14, 10, 2) == 1.0
    assert zscore_clip(10, 10, 0) == 0.5
```

- [ ] **Step 2:** 跑失敗 — `uv run pytest tests/factors/test_base.py -q` → FAIL

- [ ] **Step 3:** Create `stock_strategies/factors/base.py`：
```python
"""因子工具：值域夾擠、百分位、z-score、缺料判定。"""
from __future__ import annotations

import numpy as np
import pandas as pd

NEUTRAL = 0.5


def clip01(x) -> float:
    if x is None or (isinstance(x, float) and (np.isnan(x) or np.isinf(x))):
        return 0.5
    return float(min(1.0, max(0.0, x)))


def rank_pct(series: pd.Series, value: float) -> float:
    s = pd.to_numeric(series, errors="coerce").dropna()
    if len(s) < 2 or value is None or pd.isna(value):
        return 0.5
    return float((s <= value).mean())


def zscore_clip(value, mean, std, lo=-2.0, hi=2.0) -> float:
    if std is None or std == 0 or pd.isna(std) or pd.isna(value) or pd.isna(mean):
        return 0.5
    z = max(lo, min(hi, (value - mean) / std))
    return (z - lo) / (hi - lo)


def has_rows(ctx, attr: str, min_rows: int = 1) -> bool:
    df = getattr(ctx, attr, None)
    return isinstance(df, pd.DataFrame) and len(df) >= min_rows
```

- [ ] **Step 4:** 跑通過 + Commit
```bash
git add stock_strategies/factors/__init__.py stock_strategies/factors/base.py tests/factors/
git commit -m "feat: factors/base.py 工具函式（clip01/rank_pct/zscore_clip/has_rows）"
```

---

## Task 3：factors/registry.py（缺料 None 契約 C3 + composite）

**Files:** Create `stock_strategies/factors/registry.py`；Test `tests/factors/test_registry.py`

- [ ] **Step 1:** Create `tests/factors/test_registry.py`：
```python
import pandas as pd
from stock_strategies.factors.registry import (
    register, compute_factor, compute_all_factors, list_factors,
)
from stock_strategies.context import FactorContext


def _ctx(price_rows=80):
    px = pd.DataFrame({"date": pd.bdate_range("2023-01-02", periods=price_rows), "close": 10.0})
    return FactorContext(stock_id="x", as_of=pd.Timestamp("2024-01-01"),
                         price_df=px, index_df=pd.DataFrame(), inst=pd.DataFrame(),
                         revenue=pd.DataFrame(), valuation=pd.DataFrame(),
                         margin=pd.DataFrame(), shareholding=pd.DataFrame(), fundamentals={})


def test_missing_required_data_returns_none():
    @register("t.needs_inst", "test", ["inst"], "", lookback_min=1)
    def _f(ctx, params):
        return 1.0
    assert compute_factor("t.needs_inst", _ctx(), {}) is None


def test_internal_neutral_is_half():
    @register("t.internal_neutral", "test", ["price_df"], "", lookback_min=1)
    def _f(ctx, params):
        return 0.5
    assert compute_factor("t.internal_neutral", _ctx(), {}) == 0.5


def test_unknown_factor_returns_none():
    assert compute_factor("nope.nope", _ctx(), {}) is None


def test_exception_returns_neutral():
    @register("t.boom", "test", ["price_df"], "", lookback_min=1)
    def _f(ctx, params):
        raise ValueError("boom")
    assert compute_factor("t.boom", _ctx(), {}) == 0.5


def test_compute_all_excludes_none():
    @register("t.a", "test", ["price_df"], "", lookback_min=1)
    def _a(ctx, params):
        return 1.0
    @register("t.b", "test", ["inst"], "", lookback_min=1)
    def _b(ctx, params):
        return 0.0
    out = compute_all_factors(_ctx(), [{"name": "t.a", "weight": 1},
                                       {"name": "t.b", "weight": 1}], {})
    assert out["composite"] == 1.0
    assert "t.b" in out["missing"]


def test_list_factors_filter():
    assert any(f["name"] == "t.a" for f in list_factors("test"))
```

- [ ] **Step 2:** 跑失敗 → FAIL

- [ ] **Step 3:** Create `stock_strategies/factors/registry.py`：
```python
"""因子註冊表。缺 required_data → None（C3）；內部算掛 → 0.5；composite 剔除 None。"""
from __future__ import annotations

from .base import NEUTRAL, has_rows

FACTOR_REGISTRY: dict[str, "FactorEntry"] = {}


class FactorEntry:
    def __init__(self, fn, name, school, required_data, description, lookback_min):
        self.fn = fn
        self.name = name
        self.school = school
        self.required_data = required_data
        self.description = description
        self.lookback_min = lookback_min

    def __call__(self, ctx, params):
        for need in self.required_data:
            min_rows = self.lookback_min if need == "price_df" else 1
            if need == "fundamentals":
                if not getattr(ctx, "fundamentals", None):
                    return None
            elif not has_rows(ctx, need, min_rows):
                return None
        try:
            return self.fn(ctx, params)
        except Exception:
            return NEUTRAL


def register(name, school, required_data, description="", lookback_min=60):
    def deco(fn):
        FACTOR_REGISTRY[name] = FactorEntry(fn, name, school, required_data,
                                            description, lookback_min)
        fn.factor_name = name
        return fn
    return deco


def compute_factor(name, ctx, params):
    entry = FACTOR_REGISTRY.get(name)
    if entry is None:
        return None
    return entry(ctx, params)


def compute_all_factors(ctx, factor_list, params):
    """composite = Σ(score·weight)/Σ(weight)，只計非 None（C3）。
    回 {composite, used:[{name,score,weight}], missing:[name]}。"""
    num = den = 0.0
    used, missing = [], []
    for f in factor_list:
        name = f["name"]
        w = float(f.get("weight", 1.0))
        s = compute_factor(name, ctx, params)
        if s is None:
            missing.append(name)
            continue
        num += s * w
        den += w
        used.append({"name": name, "score": round(float(s), 3), "weight": w})
    composite = (num / den) if den > 0 else 0.5
    return {"composite": composite, "used": used, "missing": missing}


def list_factors(school: str | None = None) -> list[dict]:
    return [{"name": e.name, "school": e.school, "required_data": e.required_data,
             "description": e.description}
            for e in FACTOR_REGISTRY.values()
            if school is None or e.school == school]
```

- [ ] **Step 4:** 跑通過 + Commit
```bash
git add stock_strategies/factors/registry.py tests/factors/test_registry.py
git commit -m "feat: factors/registry.py（缺料None契約C3 + compute_all_factors composite）"
```

---

## Task 4-10：七派因子（每派一個 task，同結構）

> **共同規則**（每個 task 照做）：
> - 公式**依 spec §7 §3.x 規格表與範例碼**，套用本計畫頂部 7 條契約調整（尤其 `ctx.price`→`ctx.price_df`；缺料由 registry 判 None，故因子本體假設 df 在；內部樣本不足回 `0.5`）。
> - `@register(name, school, required_data, description, lookback_min)`，required_data 用 P1 欄位名。
> - 每因子至少 3 測試（spec §7 §7 對應編號）：看多極端、看空極端、內部樣本不足→0.5。測試放 `tests/factors/test_<school>.py`，構造 df 建 `FactorContext` 經 `compute_factor` 測。
> - TDD：先寫測試→跑失敗→實作→跑通過→commit（每派一 commit）。

### Task 4：value.py（§7 §3.1，測試點 4-5）
`value.cheap_pb`、`value.cheap_pe`、`value.high_yield`，required_data=`["valuation"]`，lookback_min=1。讀 `ctx.valuation["pbr"/"per"/"dividend_yield"]`。
Commit: `feat: factors/value.py 價值派3因子`

### Task 5：growth.py（§7 §3.2 + §3.10，測試點 6-7）
`growth.eps_yoy`、`growth.eps_accel`（required_data=`["fundamentals"]`）、`growth.rev_yoy`（required_data=`["revenue"]`）。eps_q 用 §7 §3.10 deadline（Q1→5/15,Q2→8/14,Q3→11/14,Q4→隔年3/31）過濾「發布日 ≤ ctx.as_of」的季。`rev_yoy` 讀 `ctx.revenue["yoy"]` 最新值 + `rank_pct` 近 36 月。
Commit: `feat: factors/growth.py 成長派3因子（eps_q發布日對齊）`

### Task 6：momentum.py（§7 §3.3，測試點 8-10）
`momentum.rs_self`、`momentum.dist_52w_high`、`momentum.ma_slope`、`momentum.above_mas`，required_data=`["price_df"]`，lookback_min=60。讀 `ctx.price_df["close"/"high"/"ma5"/"ma20"/"ma60"]`。
Commit: `feat: factors/momentum.py 動能派4因子`

### Task 7：chips.py（§7 §3.4，測試點 11-12）
`chips.foreign_buy_streak`（`["inst"]`）、`chips.inst_net_strength`（`["inst","price_df"]`）、`chips.foreign_holding_up`（`["shareholding"]`）、`chips.margin_retreat`（`["margin"]`）。讀 `ctx.inst["foreign_net"]`、`ctx.margin["margin_balance"]`、`ctx.shareholding["foreign_ratio"]`。
Commit: `feat: factors/chips.py 籌碼派4因子`

### Task 8：revenue.py（§7 §3.5，測試點 13-14）
`revenue.yoy_accel`、`revenue.mom_turn`、`revenue.new_high_streak`，required_data=`["revenue"]`。讀 `ctx.revenue` 的 `revenue/yoy/mom/period`（已按 avail_date ≤ as_of 切）。
Commit: `feat: factors/revenue.py 營收動能派3因子`

### Task 9：reversal.py（§7 §3.7，測試點 16-17）
`reversal.kd_oversold`、`reversal.bb_lower_bounce`、`reversal.washout_low_vol`，required_data=`["price_df"]`。`bb_lower_bounce` 對齊舊門檻 `0<dist<0.03`，讀 `ctx.price_df["bb_lower"/"bb_mid"/"k"/"d"/"close"]`。
Commit: `feat: factors/reversal.py 技術反轉派3因子`

### Task 10：breakout.py（§7 §3.8，測試點 18-19）
`breakout.box_break`、`breakout.vol_confirm_break`、`breakout.swing_new_high`，required_data=`["price_df"]`。箱頂不含當日 `h.iloc[-n-1:-1].max()`。
Commit: `feat: factors/breakout.py 突破派3因子`

---

## Task 11：legacy.py（向後相容 6 因子 + legacy_params_to_factors，測試點 20-21）

**Files:** Create `stock_strategies/factors/legacy.py`；Test `tests/factors/test_legacy.py`

- [ ] **Step 1:** Create `tests/factors/test_legacy.py`：
```python
import pandas as pd
from stock_strategies.indicators import add_indicators, tech_score_at
from stock_strategies.factors.legacy import legacy_params_to_factors
from stock_strategies.factors.registry import compute_factor
from stock_strategies.context import FactorContext


def _ctx(n=80):
    px = pd.DataFrame({"date": pd.bdate_range("2023-01-02", periods=n),
                       "open": [10.0 + i*0.1 for i in range(n)],
                       "high": [10.2 + i*0.1 for i in range(n)],
                       "low": [9.8 + i*0.1 for i in range(n)],
                       "close": [10.0 + i*0.1 for i in range(n)],
                       "volume": [1000 + i for i in range(n)]})
    px = add_indicators(px)
    return FactorContext(stock_id="x", as_of=pd.Timestamp("2024-01-01"),
                         price_df=px, index_df=pd.DataFrame(), inst=pd.DataFrame(),
                         revenue=pd.DataFrame(), valuation=pd.DataFrame(),
                         margin=pd.DataFrame(), shareholding=pd.DataFrame(), fundamentals={})


def test_legacy_tech_matches_old():
    ctx = _ctx()
    params = {"use_ma_alignment": True, "use_bollinger_bounce": True,
              "use_kd_golden_cross": True, "use_macd_bullish": True}
    factor_val = compute_factor("legacy.tech_score", ctx, params)
    old = tech_score_at(ctx.price_df.iloc[-1], params)["score"] / 100.0
    assert abs(factor_val - old) < 0.011


def test_legacy_params_to_factors():
    allon = {"use_ma_alignment": True, "use_bollinger_bounce": True,
             "use_kd_golden_cross": True, "use_macd_bullish": True, "use_volume_patterns": True}
    fl = legacy_params_to_factors(allon)
    assert len(fl) == 5 and all("legacy." in f["name"] for f in fl)
    assert legacy_params_to_factors({k: False for k in allon}) == []
```

- [ ] **Step 2:** 跑失敗 → FAIL

- [ ] **Step 3:** 實作 `legacy.py`（§7 §3.9，`ctx.price_df`、required_data=`["price_df"]`）：六因子 `legacy.tech_score`/`legacy.ma_alignment`/`legacy.bollinger_bounce`/`legacy.kd_golden_cross`/`legacy.macd_bullish`（各 `tech_score_at(ctx.price_df.iloc[-1], {單一 use_*})["score"]/100`）、`legacy.volume_bonus`（`detect_patterns(ctx.price_df, -1)` 的 `(bonus+20)/38`），及：
```python
def legacy_params_to_factors(params: dict) -> list[dict]:
    mapping = [
        ("use_ma_alignment", "legacy.ma_alignment"),
        ("use_bollinger_bounce", "legacy.bollinger_bounce"),
        ("use_kd_golden_cross", "legacy.kd_golden_cross"),
        ("use_macd_bullish", "legacy.macd_bullish"),
        ("use_volume_patterns", "legacy.volume_bonus"),
    ]
    return [{"name": fn, "weight": 1} for key, fn in mapping if params.get(key)]
```

- [ ] **Step 4:** 跑通過 + Commit
```bash
git add stock_strategies/factors/legacy.py tests/factors/test_legacy.py
git commit -m "feat: factors/legacy.py 向後相容6因子 + legacy_params_to_factors"
```

---

## Task 12：panel.py（build_panel，C7 交界）

**Files:** Create `stock_strategies/factors/panel.py`；Test `tests/factors/test_panel.py`

- [ ] **Step 1:** Create `tests/factors/test_panel.py`：
```python
import pandas as pd
from stock_strategies.factors.panel import build_panel


def test_build_panel_adds_factor_columns(monkeypatch):
    from stock_strategies import context as ctxmod
    dates = pd.bdate_range("2022-01-03", periods=300)
    price = pd.DataFrame({"date": dates, "open": 1.0,
                          "high": [10.0+i*0.1 for i in range(len(dates))], "low": 1.0,
                          "close": [10.0+i*0.1 for i in range(len(dates))], "volume": 1000})
    bundle = {"price": price, "index": pd.DataFrame(), "inst": pd.DataFrame(),
              "revenue": pd.DataFrame(), "valuation": pd.DataFrame(), "margin": pd.DataFrame(),
              "shareholding": pd.DataFrame(), "fundamentals_raw": {"eps": {}, "roe": {}}, "capital": {}}
    monkeypatch.setattr(ctxmod, "_gather_raw_bundle", lambda *a, **k: bundle)
    panel = build_panel(["2330"], ["momentum.dist_52w_high", "momentum.above_mas"],
                        as_of_dates=[pd.Timestamp("2022-12-30"), pd.Timestamp("2023-06-30")])
    assert "factor__momentum.dist_52w_high" in panel.columns
    assert "stock_id" in panel.columns and "date" in panel.columns
    assert len(panel) == 2
```

- [ ] **Step 2:** 跑失敗 → FAIL

- [ ] **Step 3:** Create `stock_strategies/factors/panel.py`：
```python
"""build_panel：股池 × 交易日 攤平成 factor__<name> 欄（C7，地基二/三交界）。
回測一次抓全期、逐日純切片（C2）。"""
from __future__ import annotations

import pandas as pd

from ..context import _gather_raw_bundle, build_context_from_bundle
from .registry import compute_factor
from . import value, growth, momentum, chips, revenue, reversal, breakout, legacy  # noqa: F401


def build_panel(stocks, factor_names, as_of_dates, lookback_years=5, params=None):
    """回 DataFrame：每列 (stock_id, date) + factor__<name> 欄。None 值為 NaN。"""
    params = params or {}
    rows = []
    for sid in stocks:
        start = (min(as_of_dates) - pd.DateOffset(years=lookback_years)
                 - pd.Timedelta(days=60)).strftime("%Y-%m-%d")
        try:
            bundle = _gather_raw_bundle(sid, start, lookback_years,
                                        as_of=max(as_of_dates).strftime("%Y-%m-%d"))
        except Exception:
            continue
        for as_of in as_of_dates:
            ctx = build_context_from_bundle(sid, as_of, bundle)
            row = {"stock_id": sid, "date": pd.Timestamp(as_of)}
            for name in factor_names:
                row[f"factor__{name}"] = compute_factor(name, ctx, params)
            rows.append(row)
    return pd.DataFrame(rows)
```

- [ ] **Step 4:** 跑通過 + Commit
```bash
git add stock_strategies/factors/panel.py tests/factors/test_panel.py
git commit -m "feat: factors/panel.py build_panel（factor__ 攤平，C7 交界）"
```

---

## Task 13：__init__.py 匯出 + 全因子註冊整合測試

**Files:** Modify `stock_strategies/factors/__init__.py`；Test `tests/factors/test_integration.py`

- [ ] **Step 1:** Create `tests/factors/test_integration.py`：
```python
import stock_strategies.factors  # 觸發全部註冊
from stock_strategies.factors.registry import FACTOR_REGISTRY, list_factors


def test_all_factors_registered():
    expected = {
        "value.cheap_pb", "value.cheap_pe", "value.high_yield",
        "growth.eps_yoy", "growth.eps_accel", "growth.rev_yoy",
        "momentum.rs_self", "momentum.dist_52w_high", "momentum.ma_slope", "momentum.above_mas",
        "chips.foreign_buy_streak", "chips.inst_net_strength", "chips.foreign_holding_up", "chips.margin_retreat",
        "revenue.yoy_accel", "revenue.mom_turn", "revenue.new_high_streak",
        "reversal.kd_oversold", "reversal.bb_lower_bounce", "reversal.washout_low_vol",
        "breakout.box_break", "breakout.vol_confirm_break", "breakout.swing_new_high",
        "legacy.tech_score", "legacy.ma_alignment", "legacy.bollinger_bounce",
        "legacy.kd_golden_cross", "legacy.macd_bullish", "legacy.volume_bonus",
    }
    assert expected.issubset(set(FACTOR_REGISTRY)), f"缺少: {expected - set(FACTOR_REGISTRY)}"


def test_list_factors_counts():
    assert len(list_factors("value")) == 3
    assert len(list_factors("momentum")) == 4
    assert len(list_factors()) >= 29
```

- [ ] **Step 2:** 跑失敗 → FAIL

- [ ] **Step 3:** 寫 `stock_strategies/factors/__init__.py`：
```python
"""因子庫：import 即註冊全部因子。"""
from .registry import (
    FACTOR_REGISTRY, register, compute_factor, compute_all_factors, list_factors,
)
from . import value, growth, momentum, chips, revenue, reversal, breakout, legacy  # noqa: F401
from .panel import build_panel

__all__ = ["FACTOR_REGISTRY", "register", "compute_factor", "compute_all_factors",
           "list_factors", "build_panel"]
```

- [ ] **Step 4:** 跑全套 + Commit — `uv run pytest -q` → 全綠
```bash
git add stock_strategies/factors/__init__.py tests/factors/test_integration.py
git commit -m "feat: factors/__init__.py 匯出 + 29 因子註冊整合測試"
```

---

## 完成標準（Definition of Done）

- [ ] `uv run pytest -q` 全綠（P1 + P2）。
- [ ] `FACTOR_REGISTRY` 含 29 因子（23 流派 + 6 legacy），各派數量正確。
- [ ] 每因子值域 ∈ [0,1] 或 None（缺料）；無 look-ahead。
- [ ] `compute_all_factors` composite 正確剔除 None（C3）。
- [ ] `build_panel` 產 `factor__<name>` 欄（C7）。
- [ ] P1 既有測試不回歸。
- [ ] 介面凍結供 P3：`compute_all_factors`、`build_panel`、`FACTOR_REGISTRY`。

## Self-Review 紀錄

**1. Spec 覆蓋**：§7 的 29 因子→Task 4-11；§7 測試點 1-23→各 task 測試；§7 §4 composite→Task 3；build_panel→Task 12。
**2. 契約對齊**：§7 vs §4/P1 的 7 處差異已在頂部明列並貫穿各 task（FactorContext 不重建、price_df、缺料 None、eps_q、欄位名、factor__、has_rows）。
**3. 型別一致**：`compute_factor(name, ctx, params)->float|None`、`compute_all_factors(ctx, factor_list, params)->{composite,used,missing}`、`build_panel(stocks, factor_names, as_of_dates, lookback_years=5, params=None)->DataFrame`、`register(name, school, required_data, description, lookback_min)` 全程一致。
