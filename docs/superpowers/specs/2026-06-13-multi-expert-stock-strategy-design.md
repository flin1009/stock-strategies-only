---
title: 多角色股市策略系統（Multi-Expert Stock Strategy System）設計規格
date: 2026-06-13
status: 已定案（D1/D2 由 kevin 於 2026-06-13 拍板）— 進入實作計畫（writing-plans）
author: kevin
supersedes: V3.2 扁平 params 策略 + 單次 LLM 生成
---

# 多角色股市策略系統 — 設計規格書

> 本規格由「多專家並行設計 workflow」產出六大技術章節，再經整合審查官交叉檢查介面一致性後組裝。
> 核心架構決策（已與使用者確認）：**兩層架構（研發層 + 固化層）共用一套純 Python 確定性地基；
> 多專家＝規則化因子 + LLM 當設計者／解說員；回測數字一律來自確定性引擎，LLM 不臆造。**

---

## §0. 文件導覽

| 章節 | 內容 | 性質 |
| --- | --- | --- |
| §1 背景與問題 | 為什麼現狀「太 rough」的三個根因 | 動機 |
| §2 目標與非目標 | 要做什麼、刻意不做什麼 | 範圍 |
| §3 整體架構 | 兩層 + 共用地基全景 | 架構 |
| **§4 M0 跨章介面契約凍結** | **八個跨章介面的單一真相（最先做、其餘章節 import）** | **契約** |
| **§5 待你拍板的決策** | **D1 評分模型、D2 survivorship 近似接受度** | **決策** |
| §6 地基一：資料層擴充 | FinMind 7 dataset + 快取限流 + FactorContext 建構 | 實作 |
| §7 地基二：因子庫 | 7 流派 × 2-4 因子的量化公式 | 實作 |
| §8 地基三：回測引擎升級 | 分市況 + 最大回撤/夏普/樣本外 + regime 判定 | 實作 |
| §9 地基四：策略 schema 升級 | 分層 + regime_overrides + v1/v2 相容 | 實作 |
| §10 研發層：多專家 Workflow | 可執行的多 agent 腳本 + 各 agent schema | 實作 |
| §11 固化層：每日 Pipeline | evaluate_v2 + LLM 解說員 + 對接 main.py | 實作 |
| §12 整合審查與里程碑 | 審查官結論 + M0–M7 實作順序 | 計畫 |

---

## §1. 背景與問題

現狀 V3.2 每日選股系統運作正常，但「策略表達能力」與「判斷準確度」受限於三個結構性根因——這也是使用者所說「太 rough」的真正來源，與「AI agent 數量」無關：

1. **策略表達能力太弱**：策略 = 一份扁平 20 格 params dict（門檻 + 四個布林開關 + 三段權重）。它無法表達「電子股用這套、金融股用那套」「大盤多頭時放寬、空頭時收緊」「先看營收動能再看技術面」這類**有條件、分層**的邏輯。不論派幾位專家，最後都只能填回同樣 20 個格子。
2. **輸入維度太窄**：只有「日 K 價量」+「年度 EPS/ROE」。沒有三大法人籌碼、月營收動能、估值分位、融資券、外資持股、大盤結構、產業比較。所謂「籌碼分析師」「市場分析師」若沒有對應資料，只是空有頭銜。
3. **回測太薄**：固定持有 20 日、只算「勝率」，沒有最大回撤、夏普、樣本外，更沒有依市況分段。所謂「歷史勝率 70%」可能只是某一段大多頭撐起來的假象。

**結論**：讓策略「不 rough」的本體，是這三件事一起升級——多專家 workflow 是把它們組織起來的骨架，但骨架必須掛上「更豐富的資料」與「更有表達力的策略結構」才有肉。本規格因此以「四塊純 Python 地基」為主體，workflow 只是驅動它們的編排層。

---

## §2. 目標與非目標

### 目標
- **G1** 擴充資料層至 7 個 FinMind dataset，提供無未來資訊（point-in-time）的 `FactorContext`。
- **G2** 建立可組合、可回測的因子庫，涵蓋 7 流派（價值／成長／動能／籌碼／營收動能／技術反轉／突破）。
- **G3** 升級回測引擎：分市況（多/盤/空）+ 最大回撤/夏普/Sortino/樣本外 + 多持有週期 + 交易成本。
- **G4** 升級策略 schema 至分層結構（factors 加權 + entry/exit + regime_overrides + period + universe），向後相容 v1。
- **G5** 用多專家 workflow 設計 + 確定性回測驗證，產出一批 `strategies/v2/*.json` 策略庫 + 研發報告。
- **G6** 把同一套邏輯固化進 `main.py` 每日 pipeline，逐檔輸出 BUY/WATCH/SKIP + 「專家會議式」理由。

### 非目標（本期刻意不做）
- 不做即時／盤中交易（仍是每日收盤後批次選股）。
- 不做自動下單（仍只輸出訊號到 Google Sheet + Telegram）。
- 不讓 LLM 做買賣決策（LLM 只「設計因子」與「解說已決定的結果」）。
- 不追求學術級無偏回測；survivorship 採近似法（見 §5 D2）。

---

## §3. 整體架構

```
┌─────────────────────────────────────────────────────────────────────┐
│  研發層（用 Claude Code Workflow tool 編排，跑一次、人工挑）              │
│                                                                       │
│   資料專家 ┐                                                           │
│   Regime專家┘→ 7 流派分析師(並行) ─pipeline→ 回測工程師 → 風控批判 → 首席策略長│
│                 各產出 v2 草案      (呼叫確定性     (3 lens:    (篩選+組裝    │
│                 (引用因子庫 name)    backtest_cli)  過擬合/穩健/  strategies/  │
│                                                     市況依賴)    v2/*.json)   │
│   產出 → strategies/v2/*.json + 研發報告.md + 凍結股池快照                │
└───────────────────────────────────┬─────────────────────────────────┘
                                     │ 共用同一套「純 Python 確定性地基」
        ┌────────────────────────────┴────────────────────────────┐
        │  四塊地基（先做、兩層共用）                                  │
        │   §6 資料層(datasources+cache+context) → FactorContext     │
        │   §7 因子庫(factors/ + FACTOR_REGISTRY + build_panel)       │
        │   §8 回測引擎(regime_classify + backtest_v2 + aggregate)    │
        │   §9 策略 schema(schema.py + v1/v2 相容 + regime_overrides) │
        └────────────────────────────┬────────────────────────────┘
                                     │
┌─────────────────────────────────────┴───────────────────────────────┐
│  固化層（進 app，main.py 每天跑）                                       │
│   逐檔: build_context → compute_all_factors → get_regime_today        │
│        → select_strategies → evaluate_v2(規則決定 BUY/WATCH/SKIP)      │
│        → expert_memo(LLM 把理由寫成專家會議紀要，可降級為模板)           │
│   → 沿用現有 market/night 濾鏡 + performance 成績單 + sheet + Telegram  │
└─────────────────────────────────────────────────────────────────────┘
```

**靈魂**：中間「四塊地基」是兩層共用的。研發層的回測**不是 LLM 用嘴算**，而是 LLM agent 產出「策略的量化定義」→ 交給確定性 `backtest_v2` 跑歷史。固化層每天用的，是同一個因子庫 + 同一份策略定義。**研發驗證過的東西，上線一模一樣**。

---

## §4. M0 — 跨章介面契約凍結（單一真相）

> 整合審查官指出：六章並行設計時，有 **8 個跨章介面被多章各自定義且不相容**，是本規格最大的單點風險。
> 因此實作的**第一個里程碑 M0** 就是把這 8 個介面收斂成單一真相（`context.py` / `schema.py` 常數 / `schemas/*.json`），
> 之後所有章節一律 `import` 同一份、**禁止各自 redefine**。以下為採納審查裁決後的**定案版本**；
> §6–§11 各章原文若與此處不一致，**以本節為準**。

### C1 `FactorContext`（擁有者：地基一，置於 `stock_strategies/context.py`）
- 欄位名一律 **`price_df`**（不用 `price`）；保留 **`index_df`**（相對強弱因子需要）。
- `as_of` 一律 **`pd.Timestamp`**（字串在 `build_context` 入口轉換）。
- 標準欄位：`price_df, index_df, fundamentals, inst, revenue, valuation, margin, shareholding, industry, market_regime`；`name, meta` 為可選。
- `price_df` 進 ctx 時**尚未** `add_indicators`，由 `build_context` 末段統一呼叫一次並 cache，避免重算。
- §7／§11 原本各自的 `@dataclass` 定義**刪除**，改 `from .context import FactorContext`。

### C2 `build_context` 拆成兩個函式（避免回測限流 vs point-in-time 衝突）
- `build_context_from_bundle(stock_id, as_of, raw_bundle) -> FactorContext`：**純切片、無 IO**。回測引擎／CLI 逐日呼叫用這個（一次抓全期 → 逐日純切片）。
- `build_context(stock_id, as_of, *, lookback_years, strict) -> FactorContext`：抓資料一次後內部呼叫 `from_bundle`。runtime 單檔用這個。
- 明定：**回測路徑一律走 `from_bundle`，runtime 單檔走 `build_context`**。

### C3 因子缺料回傳契約（影響總分，必須統一）
- 因子本體回 **`Optional[float]`**：缺 `required_data` 回 **`None`**；`0.5` 表示「有資料但中性」。
- 加權合成：`composite = Σ(score·weight) / Σ(weight)`，**僅對非 None 因子計入分子與分母**（缺料因子剔除，不汙染總分、不當 0.5）。
- 此句寫進 `factors/base.py` docstring 與 §9 composite 公式，三章引用同一句。

### C4 `regime_overrides` 白名單與 key 名（唯一真相在 `schema.py`）
- `REGIME_OVERRIDE_WHITELIST`（定案 key 名）：`no_entry: bool`、`position_scale: float`、`min_score: float`、`stop_loss: float`、`target_return: float`、`factor_weight_multipliers: dict`。
- 「停止進場」一律叫 **`no_entry`**（不用 stop_entry/paused）；「權重縮放」一律叫 **`factor_weight_multipliers`**（不用 weight_scale）。
- **策略級覆寫**歸 `schema.apply_regime_overrides`（編譯期，merge_params 內）；**系統級硬保底**（bear 強制縮部位）歸 runtime `_apply_regime_overrides`，後者不得自帶一套白名單。

### C5 v2 策略 schema 單一真相（`stock_strategies/schema.py`）
- §9 的 `schema.py` 為唯一真相；§10 workflow 的 `research/schemas/v2_strategy.schema.json` 必須是它的 **JSON Schema 鏡像**（或由 schema.py 自動生成）。
- 對齊缺漏：補 `fundamental:{eps_threshold, roe_threshold}` 區塊；`factors` 筆數限制 [3..6] 寫進 CLAMPS；`source` enum 加 `'research'`；`regime_overrides` key 名依 C4。
- CI 測試：用 `schema.validate_strategy` 驗 workflow 產的每個 draft。

### C6 回測結果 schema 與粒度（單檔 vs 投組分離）
- `backtest_v2(strategy_def, price_with_factors_df, regime_series) -> 單檔結果`（含 `by_regime{bull/range/bear}`、`oos{in_sample, out_sample, degradation}`、sharpe/sortino/max_drawdown/profit_factor）。
- 新增 `aggregate_portfolio(single_results 或 trades) -> 投組結果`（由回測章提供，因 sharpe/maxDD 需串權益曲線、不能事後平均）；`backtest_cli` 只是薄殼呼叫它。
- 投組資金分配假設（定案）：**等權、單檔同時只持一張**，寫進結果 `meta`。
- runtime 讀勝率時直接讀 `by_regime[regime].winrate`（不另立 `winrate_by_regime` 鍵）。

### C7 因子欄前綴與交界函式
- 批次攤平的因子欄名前綴一律 **`factor__<name>`**（`backtest_v2` 假設此前綴）。
- 新增 `build_panel(stocks, strat, as_of, years) -> {price_with_factors, taiex_df, errors}`，為地基二／三交界（建議置於 `stock_strategies/research/` 或 `factors/`）：對股池逐檔逐日 `compute_all_factors` 攤平成 `factor__*` 欄。
- 因子名清單由 `list_factors()` 提供；workflow 的流派分析師**只能引用 `list_factors()` 回傳的 name**，加測試擋未知因子名。

### C8 regime 唯一真相
- 唯一 regime 邏輯：`regime.py::regime_classify(taiex_df) -> regime_series`（三態 bull/range/bear）。
- `get_regime_today()` 必須是 `regime_classify(get_index_history()).iloc[-1]` 的**薄包裝**，不得另寫門檻。
- `market.get_market_state()` 保留但**降格為純二元硬降級濾鏡**（站上/跌破月線），與 regime 軟調整**並存疊加**（審查官確認接受）。
- 所有 `taiex_df` 一律來自 `data.get_index_history()`；regime 參數（ma_fast/ma_slow/slope_win）集中於 `regime.py` 常數，回測與 runtime 共用。

---

## §5. 待你拍板的決策（✅ 已定案）

> 以下兩項是**主觀取捨**，審查官無法代決。**已於 2026-06-13 由 kevin 拍板定案如下**；
> 原選項說明保留供追溯。（技術正確性類的裁決——快取選 parquet+pyarrow、regime 唯一真相 `regime_classify`——已直接採納審查建議，列入 §4。）

> ✅ **定案結果（2026-06-13，kevin）**
> - **D1 = 三段外層保留 + composite 決定技術內部**：`factors composite × 100 → tech_score`，外層仍三段加權，但權重由 `period` 決定（短線偏技術、長線偏基本面）；多策略投票僅用於「選哪檔策略評這檔股票」，不取代評分。**此即 §10 回測 CLI 與 §11 `evaluate_v2` 的唯一評分真相**，各 period 三段權重寫進 §9 `PERIOD_DEFAULTS`。
> - **D2 = 接受近似 + 標註**：用 `TaiwanStockInfo` 上市/下市日反推存活清單（§10 `load_universe` 產凍結股池快照 commit 進 repo），回測 `meta.universe_note` 標 tag；接受殘留輕微 survivorship bias（回測績效略樂觀）。

### D1 — 評分模型（三選一）
v2 怎麼把「因子合成分數」與現有「三段加權（基本面0.3/技術0.3/回測0.4）」整合？三章原本各自假設不同模型，會得到完全不同的 `signal_score` 與 BUY 門檻：

- **(推薦) 三段外層保留、composite 決定技術內部**：`factors composite × 100 → tech_score`，外層仍三段加權，但**權重由 period 決定**（短線偏技術、長線偏基本面）。多策略投票只用來「選哪檔策略評這檔股票」，不取代評分。→ 平滑沿用現有門檻直覺、可解釋。
- **純 composite 取代**：直接用 factors composite 當總分，廢除三段加權。→ 最簡潔，但與現有門檻校準斷裂、基本面/回測退為普通因子。
- **多策略投票制**：跑多檔策略、以投票數決定 action。→ 最「集體決策」，但 signal_score 語意改變最大、最難和回測對齊。

### D2 — Survivorship（生存者偏誤）近似接受度
FinMind 無歷史指數成分快照，回測股池只能用「上市/下市日反推」近似（workflow `load_universe` 產凍結股池快照 commit 進 repo）。需你**書面接受**：此近似仍可能殘留輕微 survivorship bias（回測績效略樂觀）。

- **(推薦) 接受近似 + 標註**：用 TaiwanStockInfo 上市/下市日反推存活清單，於回測 `meta.universe_note` 標註 tag；接受殘留輕微偏誤。
- **嚴格但成本高**：另尋付費歷史成分資料源（超出本期範圍，不建議）。

---

> 以下 §6–§11 為六位設計專家產出的詳細技術設計原文（已與現有程式碼對接核對）。
> **凡與 §4 契約衝突者，以 §4 為準。**


---

## §6 地基一：資料層擴充（讓各路分析師有料）

> 目標：把資料層從「日 K 價量 + 年度 EPS/ROE」擴充成能餵養 7 流派（價值 / 成長 / 動能 / 籌碼 / 營收動能 / 技術反轉 / 突破）的程度，並提供一個無未來資訊（point-in-time）的 `FactorContext` 建構器，作為「因子層」與「回測引擎」的唯一資料入口。
>
> 設計鐵律：**所有對外資料一律走帶快取 + 限流退避的 `fetch_finmind_cached`，回測期重複抓同一檔同一 dataset 必須命中本地快取；任何切片到 `as_of_date` 的動作一律 as-of join（只取 `<= as_of` 或 `資料可得日 <= as_of`），杜絕 look-ahead。**

---

### 0. 與現有 code 的對接點（先講清楚改哪裡、沿用什麼）

| 現有檔案 / 函式 | 本章如何處理 |
| --- | --- |
| `stock_strategies/data.py::fetch_finmind` | **沿用、不改簽名**。新增一層 `fetch_finmind_cached(...)` 包在它外面（快取 + 限流 + `end_date` 支援）。所有新 loader 都呼叫 cached 版。`get_price_history` / `get_fundamental` 改為內部呼叫 cached 版（純加速，行為不變）。 |
| `stock_strategies/config.py::FINMIND_URL` | 沿用。新增快取 / 限流相關常數（見 §6）。 |
| `stock_strategies/market.py::_fetch_taiex`（`TAIEX_IDS = ["TAIEX","TWII"]`） | TAIEX 抓取邏輯**抽到新的 `get_index_history`**（含快取、回 OHLC + 漲跌家數欄位），`market.py` 改為呼叫它（向後相容：`get_market_state` 簽名不變）。regime 判定本章不做（屬「地基二」），但本章保證提供 `get_index_history` 餵料。 |
| `stock_strategies/indicators.py::add_indicators` | 不改。本章產出的 `price_df` 欄位契約（`date/open/high/low/close/volume`）與它完全相容，可直接 `add_indicators(ctx.price_df)`。 |
| `stock_strategies/evaluate.py` | 本章不改 `evaluate`（屬固化層章節）。本章只**新增** `build_context()`，供新因子層 / 回測引擎使用；舊 pipeline 不受影響。 |
| `pyproject.toml` | 新增依賴 `pyarrow>=15`（parquet 快取）；`requests` / `pandas` 已有。用 `uv add pyarrow` 安裝。 |

**新增檔案**：`stock_strategies/datasources.py`（各 dataset loader）、`stock_strategies/cache.py`（快取 + 限流）、`stock_strategies/context.py`（`FactorContext` 與 `build_context`）。

---

### 1. 資料源總表（7 個 dataset → 流派對應）

> 欄位名標「★需以 FinMind 實際回傳驗證」者：FinMind 各 dataset 欄位偶有大小寫 / 命名差異，loader 內一律做 `rename` 正規化並用 `_require_cols()` 斷言（缺欄即記 warning 並回中性，不可整支 crash）。

| dataset（FinMind 名稱） | 頻率 | 正規化後關鍵欄位 | 用途 / 餵養流派 |
| --- | --- | --- | --- |
| `TaiwanStockPrice`（個股） | 日 | `date, open, high, low, close, volume` | 全部流派的基準價量；動能 / 突破 / 技術反轉直接用 |
| `TaiwanStockPrice`（`data_id=TAIEX`） | 日 | `date, open, high, low, close` | 大盤 regime（地基二）、相對強弱（動能） |
| `TaiwanStockInstitutionalInvestorsBuySell` | 日 | `date, name(法人別), buy, sell, net(=buy-sell)` ★ | **籌碼**：外資 / 投信 / 自營連買、法人同買 |
| `TaiwanStockMonthRevenue` | 月 | `date(發布切片用), revenue_year, revenue_month, revenue` ★ | **營收動能 / 成長**：MoM、YoY、累計 YoY、連續成長月數 |
| `TaiwanStockPER` | 日 | `date, PER, PBR, dividend_yield` ★ | **價值**：本益比 / 股價淨值比 / 殖利率分位 |
| `TaiwanStockMarginPurchaseShortSale` | 日 | `date, MarginPurchaseTodayBalance, ShortSaleTodayBalance, ...` ★ | **籌碼**：融資增減（散戶過熱）、券資比、軋空 |
| `TaiwanStockShareholding` | 週 / 不定期 ★ | `date, ForeignInvestmentSharesRatio` ★ | **籌碼**：外資持股比例趨勢 |
| `TaiwanStockInfo` | 靜態（全市場一次抓） | `stock_id, stock_name, industry_category, type` ★ | universe 過濾、產業別、相對強弱分母；股本由下表補 |
| `TaiwanStockFinancialStatements` | 季 / 年（已用） | EPS / ROE（沿用 `get_fundamental`），另解析股本 | **價值 / 成長**：EPS、ROE；股本算市值 |

> **股本來源裁決（open question 之一）**：`TaiwanStockInfo` 不一定含股本。優先方案：以 `TaiwanStockFinancialStatements` 內 `type in {"CommonStocksAndOrdinaryShares", "OrdinaryShare", ...}`（★需驗證確切 type 名）取得普通股股本，市值 = `股本/10 × 收盤價`（股本以「元」計、每股面額 10 元 → 股數 = 股本/10）。若該 type 缺失，市值因子回 `None`（中性 0.5），不可猜。

---

### 2. 快取與限流層 `stock_strategies/cache.py`

回測期會對「同一檔、同一 dataset、同一起訖」重複抓上百次，必須本地快取。採 **parquet 檔快取（一檔一 dataset 一檔案）**，理由：pandas 原生 `to_parquet/read_parquet`、保留 dtype、無 schema migration 負擔、檔案層級易刪重抓。

#### 2.1 快取鍵與路徑

```
快取根目錄：env FINMIND_CACHE_DIR，預設 <repo>/.cache/finmind
檔名：{dataset}__{data_id}.parquet         # 例：TaiwanStockMonthRevenue__2330.parquet
sidecar meta：{dataset}__{data_id}.meta.json  # {"fetched_at": ISO, "min_date":..., "max_date":..., "rows":N}
```

一檔一個 dataset 全歷史（不以日期切檔），讀取時在記憶體做日期過濾。增量更新：若快取 `max_date` 距今 < `CACHE_FRESH_DAYS`（日頻=1、月頻=20、靜態=7）視為新鮮，直接回快取；否則只抓 `start_date = max_date - overlap` 的增量再 `concat` 去重。

#### 2.2 簽名

```python
# stock_strategies/cache.py
from __future__ import annotations
import pandas as pd

def fetch_finmind_cached(
    dataset: str,
    data_id: str,
    start_date: str,
    end_date: str | None = None,     # ★ 新增：FinMind 支援 end_date，回測切片必填以避免抓進未來
    *,
    fresh_days: int | None = None,   # None → 依 dataset 類型自動推定
    force_refresh: bool = False,
    timeout: int = 30,
    max_retries: int = 2,
) -> pd.DataFrame:
    """帶 parquet 快取 + 限流退避的 FinMind 取數。
    - 命中新鮮快取 → 直接回（不打 API）
    - 過期 → 增量抓 max_date 之後並合併去重
    - 冷啟動 → 全抓並寫快取
    回傳已正規化 date 欄（datetime64）、依 date 升冪排序、去重後的 DataFrame。
    """

def cache_path(dataset: str, data_id: str) -> Path: ...
def clear_cache(dataset: str | None = None, data_id: str | None = None) -> int: ...
def _rate_limited_get(params: dict, timeout: int, max_retries: int) -> dict:
    """在 fetch_finmind 的 retry 之外，額外處理 FinMind 限流（HTTP 402/429 +
    回傳 body status!=200 的 'request limit' 訊息）：指數退避 + 全域節流。"""
```

#### 2.3 限流（FinMind 免費版約 600 req/hr）

`fetch_finmind` 目前**只 retry 連線層例外，不處理 402/429 限流**。新增 `_rate_limited_get`：

- **全域最小間隔**：模組級 `time.monotonic()` 記上次請求時間，強制相鄰請求間隔 `>= FINMIND_MIN_INTERVAL`（預設 0.12s ≈ 8 req/s 上限，避免瞬間爆量）。
- **限流偵測**：FinMind 限流常以 HTTP 200 但 body `status != 200` 且 `msg` 含 `"request"` 回傳；同時兼容 HTTP 402/429。命中 → 退避 `min(2**attempt * 5, 120)` 秒後重試，最多 `RATE_LIMIT_MAX_RETRIES=4` 次。
- 仍失敗 → raise `FinMindRateLimitError`，由上層 loader 接住回「資料缺漏」中性結果（見 §5）。

---

### 3. 各 dataset 的 loader（`stock_strategies/datasources.py`）

> 通則：每個 loader 都 (a) 呼叫 `fetch_finmind_cached`；(b) `rename` 正規化欄位；(c) `pd.to_datetime(date)`、數值欄 `pd.to_numeric(errors="coerce")`；(d) 依 `as_of`/`end_date` 切片；(e) 空資料回空 `DataFrame`（不 raise），讓因子層判中性。所有函式都接受 `as_of: str | None`，內部轉成 `end_date` 傳給快取層 —— **這是避免 look-ahead 的單一機制**。

```python
# stock_strategies/datasources.py
def get_institutional(stock_id: str, start: str, as_of: str | None = None) -> pd.DataFrame:
    """三大法人買賣超（日）。回欄位:
       date, foreign_net, trust_net, dealer_net, total_net （單位：股；除 1000 得張）
    FinMind 原始 name 欄為 'Foreign_Investor'/'Investment_Trust'/'Dealer_self'/'Dealer_Hedging'…
    ★需驗證 name 列舉值；loader 內以 startswith('Foreign')/('Investment_Trust')/('Dealer') 分桶，
    自營商合併 self+Hedging。net = buy - sell。再 pivot 成寬表。"""

def get_month_revenue(stock_id: str, start: str, as_of: str | None = None) -> pd.DataFrame:
    """月營收。回欄位:
       avail_date(資料可得日=次月10日), revenue_year, revenue_month, revenue,
       mom, yoy, yoy_cum  （mom/yoy 以 pct，0.123 = +12.3%）
    ★關鍵防 look-ahead：FinMind 的 date 是『營收所屬月』(如 2024-03-01)，
    但該資料約在『次月10日』才公布。avail_date = 所屬月 + 1 月又 9 天（保守設次月 10 日）。
    as_of 切片用 avail_date <= as_of，不是用所屬月！"""

def get_valuation(stock_id: str, start: str, as_of: str | None = None) -> pd.DataFrame:
    """估值（日）。回欄位: date, per, pbr, dividend_yield（殖利率以 pct）。
    FinMind 欄位 PER/PBR/dividend_yield ★需驗證大小寫。per<=0 視為虧損→存 NaN。"""

def get_margin(stock_id: str, start: str, as_of: str | None = None) -> pd.DataFrame:
    """融資融券（日）。回欄位:
       date, margin_balance, short_balance, margin_chg, short_chg, short_margin_ratio
    來源欄: MarginPurchaseTodayBalance / ShortSaleTodayBalance ★需驗證。
    short_margin_ratio = short_balance / margin_balance（券資比）。"""

def get_shareholding(stock_id: str, start: str, as_of: str | None = None) -> pd.DataFrame:
    """外資持股比例（週/不定期）。回欄位: date, foreign_ratio（以 pct）。
    來源欄 ForeignInvestmentSharesRatio ★需驗證。頻率不規則 → 因子層用 as-of 取最近一筆。"""

def get_stock_info(refresh: bool = False) -> pd.DataFrame:
    """全市場靜態資料（一次抓、長快取 fresh_days=7）。回欄位:
       stock_id, stock_name, industry_category, market_type(twse/tpex)
    呼叫 fetch_finmind_cached('TaiwanStockInfo', '', '1990-01-01')。"""

def get_index_history(index_id: str = "TAIEX", start: str | None = None,
                      as_of: str | None = None) -> pd.DataFrame:
    """大盤指數（日）。回 date, open, high, low, close。
    沿用 market.py 的 TAIEX/TWII 試法（依序 fallback）。market.py 改呼叫本函式。"""

def get_capital_and_industry(stock_id: str, as_of: str | None = None) -> dict:
    """回 {industry, shares_outstanding, market_cap_at(as_of)}；缺則對應值 None。"""
```

#### 3.1 月營收 look-ahead 防護（最關鍵）

月營收是最容易踩雷的資料：3 月營收約 4/10 才公布，回測時若在 3/15 就用到 3 月營收 = 嚴重未來資訊。處理：

```python
# 在 get_month_revenue 內
df["period"] = pd.to_datetime(
    df["revenue_year"].astype(int).astype(str) + "-" +
    df["revenue_month"].astype(int).astype(str).str.zfill(2) + "-01")
# 公布日保守估：所屬月底 + 10 天（次月10日）。法規上限為次月10日。
df["avail_date"] = df["period"] + pd.offsets.MonthEnd(0) + pd.Timedelta(days=10)
if as_of:
    df = df[df["avail_date"] <= pd.to_datetime(as_of)]
```

同理，**季財報（EPS/ROE）也要 as-of**：Q1 財報約 5/15、Q2 約 8/14、Q3 約 11/14、Q4(年報) 約次年 3/31 公布。`build_context` 取財報時以這套發布日表（見 §4.2）切片，不可直接用財報所屬期。

---

### 4. `FactorContext` 與建構器（`stock_strategies/context.py`）

#### 4.1 資料結構

```python
# stock_strategies/context.py
from dataclasses import dataclass, field
import pandas as pd

@dataclass
class FactorContext:
    stock_id: str
    as_of: pd.Timestamp                 # 評估時點 t（含當日）
    price_df: pd.DataFrame              # 個股日 K（到 t），欄位同 add_indicators 契約
    index_df: pd.DataFrame              # TAIEX 日 K（到 t），給相對強弱/regime
    inst: pd.DataFrame                  # 三大法人（到 t）
    revenue: pd.DataFrame              # 月營收（avail_date <= t）
    valuation: pd.DataFrame            # PER/PBR/殖利率（到 t）
    margin: pd.DataFrame               # 融資券（到 t）
    shareholding: pd.DataFrame         # 外資持股比（到 t）
    fundamentals: dict                 # {"eps":{year:val}, "roe":{year:val}}（發布日<=t）
    industry: str | None               # 產業別
    shares_outstanding: float | None   # 普通股股本（元）
    market_cap: float | None           # t 日市值（元）
    meta: dict = field(default_factory=dict)  # {"warnings":[...], "missing":[...], "rows":{...}}

    def latest_price(self) -> pd.Series | None: ...
    def asof_row(self, df_name: str) -> pd.Series | None:
        """對不規則頻率資料（shareholding/valuation）取 date<=as_of 的最後一筆。"""
```

> `market_regime` 標籤**不存進 context**（由地基二的 `regime_classify(index_df)` 在回測時逐日算），但 context 提供 `index_df` 作為其輸入，介面契約一致。

#### 4.2 `build_context` 組裝流程

```python
def build_context(
    stock_id: str,
    as_of_date: str,                 # "YYYY-MM-DD"，回測逐日推進時傳當日
    *,
    lookback_years: int = 5,         # 回測窗 3~5 年；近況判斷由因子自取近 1~2 年
    info_df: pd.DataFrame | None = None,  # 可預先 get_stock_info() 傳入，省重抓
    strict: bool = False,            # True=資料缺漏直接 raise；False(預設)=記 warning 回中性
) -> FactorContext:
    ...
```

組裝步驟（全部以 `as_of` 為硬上界）：

1. `as_of = pd.to_datetime(as_of_date)`；`start = (as_of - lookback_years 年 - 60 天)`。
2. **價格**：`price_df = add_indicators-ready 的 get_price_history 風格`，但走快取 + 切 `date <= as_of`。**新股保護**：`len(price_df) < MIN_PRICE_ROWS(=60)` → `meta["missing"].append("price_history_insufficient")`，仍回 context（讓因子各自判中性），回測層會跳過該檔該日。
3. **指數**：`index_df = get_index_history("TAIEX", start, as_of)`。
4. **法人 / 估值 / 融資券**：日頻，直接 `date <= as_of` 切片。
5. **月營收**：`get_month_revenue(... as_of)`（已用 `avail_date <= as_of`）。
6. **持股比**：不規則頻率，全抓後 `date <= as_of`，因子用 `asof_row` 取最近。
7. **財報（EPS/ROE）**：沿用 `get_fundamental` 但**加發布日過濾**——以 §4.3 發布日表把「發布日 > as_of」的年度剔除（回測早期年度才正確）。
8. **產業 / 股本 / 市值**：`get_capital_and_industry(stock_id, as_of)`。
9. 每一步包 `try/except`：失敗時 `strict=False` 記 `meta["warnings"]` 並塞空 DataFrame；`strict=True` raise。回傳 `FactorContext`。

#### 4.3 財報發布日對照（寫死常數，避免 look-ahead）

```python
# context.py
QUARTER_PUBLISH = {1: ("05-15"), 2: ("08-14"), 3: ("11-14"), 4: ("03-31_next_year")}
# 年度 EPS/ROE 的可用日 = 該年度 Q4(年報) 發布日 ≈ 次年 3/31。
# build_context 取 fundamentals 時：only include year y where date(y+1, 3, 31) <= as_of
```

---

### 5. 邊界與錯誤處理（逐情境給做法）

| 情境 | 做法 |
| --- | --- |
| **FinMind 限流（402/429/body status!=200）** | `_rate_limited_get` 退避重試（§2.3）；最終失敗 raise `FinMindRateLimitError`，`build_context(strict=False)` 接住 → 該資料塊空、記 `meta["warnings"]`，**不污染快取**（限流回傳不寫 parquet）。 |
| **某 dataset 對該股無資料**（如 ETF 無月營收） | loader 回空 DataFrame；因子層 `required_data` 宣告缺項時回中性 0.5；`meta["missing"]` 留痕。 |
| **停牌 / 當日無報價** | `price_df` 該日不存在 → `latest_price()` 回 `date <= as_of` 的最後一筆（即停牌前最後成交），回測層另判「停牌日不進場」。 |
| **新股上市不足 N 天** | `len(price_df) < 60` → 標 `price_history_insufficient`；動能 / 突破 / 回測類因子回 None（中性），避免用 5 根 K 算 60MA。 |
| **欄位名與假設不符（★項）** | `_require_cols(df, [...])` 找不到欄位 → 記 warning + 回空，**絕不 KeyError crash 整個 pipeline**；於 §7 用實機測試逐一校正 rename 表。 |
| **快取檔損毀**（parquet 讀失敗） | catch → 刪損毀檔 + 重抓一次；再失敗才往上拋。 |
| **survivorship bias** | universe 取自 `get_stock_info()` 的**當前**清單會偏存活者；回測層需傳入「該時點存在的股票池」。本章對策：`get_stock_info` 保留 `market_type` 與 `stock_name`，回測逐日選股時用「該日 `price_df` 有資料」作為『當時可交易』的代理判據，不靠未來才上市/已下市的名單；下市股仍保留其歷史 parquet（不因今日抓不到而刪）。 |

---

### 6. 新增設定常數（`config.py`）

```python
# 快取
FINMIND_CACHE_DIR = os.environ.get("FINMIND_CACHE_DIR",
                                   str(Path(__file__).resolve().parent.parent / ".cache" / "finmind"))
CACHE_FRESH_DAYS = {"daily": 1, "monthly": 20, "weekly": 5, "static": 7}
# 限流
FINMIND_MIN_INTERVAL = 0.12          # 相鄰請求最小間隔秒
RATE_LIMIT_BACKOFF_BASE = 5          # 限流退避基數秒
RATE_LIMIT_MAX_RETRIES = 4
# context
MIN_PRICE_ROWS = 60
```

`.gitignore` 追加 `.cache/`（快取不進版控）。`.env.example` 加註 `FINMIND_CACHE_DIR`（選填）。

---

### 7. 可測試性（關鍵單元測試點）

> 用 `pytest`，FinMind 一律 mock（不打真 API）；另留一支 `-m live` 的整合測試對單檔（2330）跑一次校正 ★ 欄位。

1. **快取命中**：先 `fetch_finmind_cached` 寫檔，第二次呼叫斷言 `requests.get` 未被呼叫（mock 計數=0）。
2. **增量更新**：快取 `max_date=昨日`，新呼叫只用 `start_date≈max_date`，結果含舊+新且 date 無重複。
3. **限流退避**：mock 連回兩次 body `status=402` 再回 200，斷言退避次數正確、最終成功；連回 5 次斷言 raise `FinMindRateLimitError`。
4. **月營收 look-ahead**：造 3 月營收（period=2024-03-01），`as_of="2024-04-05"` 時**不**含 3 月（avail=2024-04-10 > as_of），`as_of="2024-04-10"` 才含。
5. **財報 as-of**：`as_of="2024-03-30"` 不含 2023 年度 EPS（發布日 2024-03-31），`2024-03-31` 才含。
6. **法人 pivot**：mock 4 種 name → 斷言 `foreign_net/trust_net/dealer_net/total_net` 數值與單位（股）正確、自營合併 self+Hedging。
7. **新股保護**：`price_df` 只給 30 列 → `meta["missing"]` 含 `price_history_insufficient`，`build_context` 不 raise。
8. **欄位缺失韌性**：mock 回傳缺 `PER` 欄 → `get_valuation` 回空 + warning，不 KeyError。
9. **build_context as-of 一致性**：對同一 `stock_id` 給兩個 `as_of`（t1<t2），斷言 t1 的所有 DataFrame `max(date) <= t1`、且為 t2 的子集（point-in-time 單調）。
10. **TAIEX fallback**：mock `TAIEX` 回空、`TWII` 回有值 → `get_index_history` 成功回 TWII 資料。

---

### 8. 實作順序（給工程師）

1. `uv add pyarrow` → `cache.py`（`fetch_finmind_cached` + `_rate_limited_get`）→ 跑測試 1~3。
2. `datasources.py` 七個 loader（先 mock 測，再 `-m live` 對 2330 校正 ★ 欄位 rename 表）。
3. 把 `market.py::_fetch_taiex` 重構為呼叫 `get_index_history`（行為回歸測試 `get_market_state`）。
4. `context.py`（`FactorContext` + `build_context`）→ 測試 4~9。
5. 將 `data.py::get_price_history/get_fundamental` 內部改走 `fetch_finmind_cached`（純加速，跑現有 `evaluate` 回歸）。


---

## §7 地基二：因子庫（把專家判斷量化成可回測因子）

> 本章定義 `stock_strategies/factors/` 模組：把七大流派的「專家判斷」固化成一組純函式因子，每個因子 `compute(ctx, params) -> float ∈ [0,1]`（1 = 最看多）。所有因子可在歷史任一日 `t` 計算且無 look-ahead，供地基三回測引擎 `backtest_v2` 逐日呼叫，亦供 runtime `evaluate` 即時計算。

### 1. 設計總則（先讀，後面所有因子都遵守）

這樣做，不要妥協：

1. **純函式、無 IO**：因子內部「絕對不」打 FinMind。所有資料由上游一次抓好、切片成 `FactorContext` 傳入。抓資料是地基一（`data.py` 擴充）的事。
2. **值域硬約束 0..1**：每個因子最後一行一定是 `clip01(x)`。1 = 最看多、0 = 最看空、0.5 = 中性 / 資料不足。
3. **缺資料回中性 0.5（不是 None、不是 0）**：回測引擎對「缺資料」與「看空」必須能區分，但對外統一用 0.5 當「無意見」。理由：回 None 會讓加權平均要處理特例；回 0 會把「沒資料」誤判成「強烈看空」造成系統性偏空。內部可在 `details` 標 `available=False` 供除錯。
4. **No look-ahead 是第一守則**：任何因子在計算日 `t` 只能看到「在 `t` 收盤後、實際已公開」的資料。重點對齊規則（見 §3.6 月營收、§3.2 財報）：**月營收用「公告日」對齊、不是「所屬月份」**；季財報用「財報實際公布日」對齊、不是「季度結束日」。
5. **正規化方法二選一，寫死在因子裡**：
   - **歷史自身百分位**（適合「相對自己便宜/貴」「相對自己強弱」）：`rank_pct(series_up_to_t, value)`，回 0..1。
   - **z-score clip**（適合「加速度 / 偏離度」）：`zscore_clip(value, mean, std, lo=-2, hi=2)` 再線性映到 0..1。
   - 嚴禁用「全樣本 min-max」做正規化（會偷看未來的極值 → look-ahead）。百分位與 z-score 的統計量都只用 `≤ t` 的資料。

### 2. 模組結構與對外介面

```
stock_strategies/factors/
├── __init__.py          # 匯出 registry、FactorContext、build_context
├── context.py           # FactorContext dataclass + build_context()（地基一交界）
├── base.py              # Factor 基類/協定、clip01/rank_pct/zscore_clip 等工具
├── registry.py          # FACTOR_REGISTRY: dict[str, Factor]；register 裝飾器
├── value.py             # 價值派 3 因子
├── growth.py            # 成長派 3 因子
├── momentum.py          # 動能派 4 因子
├── chips.py             # 籌碼派 4 因子
├── revenue.py           # 營收動能派 3 因子
├── reversal.py          # 技術反轉派 3 因子
├── breakout.py          # 突破派 3 因子
└── legacy.py            # 把舊 tech_score_at 四訊號 + volume 型態包成因子（向後相容）
```

#### 2.1 `FactorContext`（context.py）

延續介面契約草案，落地成 dataclass。**所有欄位都是「截至 t、已公開」的切片**，由 `build_context(stock_id, as_of, raw_bundle)` 產生。

```python
# stock_strategies/factors/context.py
from __future__ import annotations
from dataclasses import dataclass, field
import pandas as pd

@dataclass
class FactorContext:
    stock_id: str
    as_of: pd.Timestamp                 # 計算日 t（含當日收盤，視為已知）
    price: pd.DataFrame                  # index=date(≤t)；欄位 open/high/low/close/volume + 已 add_indicators
    inst: pd.DataFrame | None = None     # 三大法人；index=date(≤t)；foreign_net/trust_net/dealer_net（張）
    revenue: pd.DataFrame | None = None  # 月營收；欄位見 §3.6（用 announce_date 對齊，已濾 ≤t）
    valuation: pd.DataFrame | None = None# PER/PBR/殖利率；index=date(≤t)；per/pbr/dividend_yield
    margin: pd.DataFrame | None = None   # 融資券；index=date(≤t)；margin_balance/short_balance（張）
    shareholding: pd.DataFrame | None = None # 外資持股比率；index=date(≤t)；foreign_ratio(%)
    fundamentals: dict = field(default_factory=dict) # {"eps":{year:val}, "roe":{year:val}, "eps_q":{(y,q):val,...}}
    industry: str | None = None          # 產業別字串（例 "半導體業"）
    market_regime: str | None = None     # 該日大盤 regime 標籤 bull/range/bear（地基三填）

    def has(self, attr: str, min_rows: int = 1) -> bool:
        df = getattr(self, attr, None)
        return isinstance(df, pd.DataFrame) and len(df) >= min_rows
```

> `price` 進來前**必須已過 `add_indicators`**（沿用 `stock_strategies/indicators.py:add_indicators`），所以因子可直接讀 `ma5/ma20/ma60/bb_*/k/d/dif/dea/macd_hist/atr`，不重算。

#### 2.2 因子協定（base.py）

```python
# stock_strategies/factors/base.py
from typing import Protocol, Callable
import numpy as np
import pandas as pd
from .context import FactorContext

class Factor(Protocol):
    name: str               # 唯一鍵，registry 用它引用，例 "value.cheap_pb"
    school: str             # value/growth/momentum/chips/revenue/reversal/breakout/legacy
    required_data: list[str]# 依賴的 ctx 欄位名，例 ["valuation"]；缺則回中性
    def __call__(self, ctx: FactorContext, params: dict) -> float: ...

def clip01(x: float) -> float:
    if x is None or (isinstance(x, float) and (np.isnan(x) or np.isinf(x))):
        return 0.5
    return float(min(1.0, max(0.0, x)))

def rank_pct(series: pd.Series, value: float) -> float:
    """value 在 series（只含 ≤t 資料）中的百分位 0..1。空/全 NaN → 0.5。
    高 value 想對應高分時直接用；想反向（越低越好）用 1 - rank_pct。"""
    s = pd.to_numeric(series, errors="coerce").dropna()
    if len(s) < 2 or pd.isna(value):
        return 0.5
    return float((s <= value).mean())

def zscore_clip(value, mean, std, lo=-2.0, hi=2.0) -> float:
    """回 0..1：z=lo→0, z=0→0.5, z=hi→1（線性）。std=0 或缺 → 0.5。"""
    if std is None or std == 0 or pd.isna(std) or pd.isna(value) or pd.isna(mean):
        return 0.5
    z = max(lo, min(hi, (value - mean) / std))
    return (z - lo) / (hi - lo)

NEUTRAL = 0.5
```

> **重要：required_data 的缺料判定統一在 registry 包一層**，因子本體假設「至少基本資料在」，但仍要對「逐欄位 NaN / 列數不足」做防呆（見各因子）。

#### 2.3 註冊表（registry.py）

```python
# stock_strategies/factors/registry.py
from .base import NEUTRAL
from .context import FactorContext

FACTOR_REGISTRY: dict[str, "FactorEntry"] = {}

class FactorEntry:
    def __init__(self, fn, name, school, required_data, description, lookback_min):
        self.fn, self.name, self.school = fn, name, school
        self.required_data = required_data
        self.description = description
        self.lookback_min = lookback_min  # price 至少幾根才算得準；不足回中性

    def __call__(self, ctx: FactorContext, params: dict) -> float:
        # 統一缺料防護：required_data 任一缺 → 中性
        for need in self.required_data:
            if need == "price":
                if not ctx.has("price", self.lookback_min):
                    return NEUTRAL
            elif not ctx.has(need):
                return NEUTRAL
        try:
            return self.fn(ctx, params)
        except Exception:
            return NEUTRAL  # runtime/回測逐日呼叫，單因子掛掉不可拖垮整檔

def register(name, school, required_data, description="", lookback_min=60):
    def deco(fn):
        FACTOR_REGISTRY[name] = FactorEntry(fn, name, school, required_data, description, lookback_min)
        fn.factor_name = name
        return fn
    return deco

def compute_factor(name: str, ctx: FactorContext, params: dict) -> float:
    entry = FACTOR_REGISTRY.get(name)
    if entry is None:
        return NEUTRAL  # 策略引用了不存在的因子 → 中性 + 上層記 warning
    return entry(ctx, params)

def list_factors(school: str | None = None) -> list[dict]:
    return [
        {"name": e.name, "school": e.school, "required_data": e.required_data,
         "description": e.description}
        for e in FACTOR_REGISTRY.values()
        if school is None or e.school == school
    ]
```

策略 JSON 用 `name` 引用因子（對齊升級版 schema 的 `factors: [{name, weight}]`）：

```json
"factors": [
  {"name": "value.cheap_pb", "weight": 0.4},
  {"name": "chips.foreign_buy_streak", "weight": 0.3},
  {"name": "revenue.yoy_accel", "weight": 0.3}
]
```

### 3. 七派因子定義（共 23 個 + legacy 包裝）

每個因子規格表欄位：**公式**（原始資料 → 0..1）、**required_data**、**缺料中性處理**、**no look-ahead 對齊**、**params 預設**。

下文所有「百分位視窗」預設取 `price` / 對應序列「最近 `window` 筆且 ≤ t」，window 預設值寫在各因子 params。

#### 3.1 價值派（value.py，3 因子）— 「便宜相對自身歷史」

| 因子 name | 公式（→0..1） | required_data | 缺料中性 | look-ahead 對齊 |
|---|---|---|---|---|
| `value.cheap_pb` | 取近 `pb_window`（預設 756 交易日≈3y）PBR 序列；`score = 1 - rank_pct(pbr_hist, pbr_t)`。PBR 越低（相對自身越便宜）分越高 | valuation | valuation 缺 / `per`,`pbr` 全 NaN → 0.5 | valuation 每筆有 date，只取 `date ≤ as_of` |
| `value.cheap_pe` | 同上但用 PER；`per_t ≤ 0`（虧損）→ 回 0.5（PER 無意義，交給成長/營收因子判） | valuation | 同上 | 同上 |
| `value.high_yield` | `dividend_yield` 越高相對自身越便宜；`score = rank_pct(yield_hist, yield_t)`；`yield_t == 0`（不配息）→ 0.3（輕度偏空，非中性） | valuation | 同上 | 同上 |

```python
# stock_strategies/factors/value.py
from .registry import register
from .base import clip01, rank_pct, NEUTRAL

@register("value.cheap_pb", "value", ["valuation"],
          "PBR 相對自身 3 年歷史越低越便宜", lookback_min=1)
def cheap_pb(ctx, params):
    w = params.get("pb_window", 756)
    s = ctx.valuation["pbr"].dropna().iloc[-w:]
    if len(s) < 20:                       # 樣本太少，百分位不可靠
        return NEUTRAL
    pbr_t = s.iloc[-1]
    if pbr_t <= 0:
        return NEUTRAL
    return clip01(1.0 - rank_pct(s, pbr_t))
```

> 正規化選百分位而非 z-score：估值分布長尾、右偏，z-score 會被極端高估值拉爆，百分位穩。

#### 3.2 成長派（growth.py，3 因子）— 「EPS / 營收 YoY 加速」

| 因子 name | 公式 | required_data | 缺料中性 | look-ahead 對齊 |
|---|---|---|---|---|
| `growth.eps_yoy` | 用 `fundamentals.eps_q`（單季 EPS，鍵 `(year,quarter)`），取最新「已公布」季 vs 去年同季：`yoy = (eps_q_t - eps_q_t_1y)/abs(eps_q_t_1y)`；`zscore_clip` 用該股近 8 季 yoy 的 mean/std；正成長偏 >0.5 | fundamentals | `eps_q` 不足 2 年資料 → 0.5 | `eps_q` 只放「財報公布日 ≤ as_of」的季（見 §3.10 對齊細節） |
| `growth.eps_accel` | 加速度＝近 2 季 yoy 連續放大：`accel = yoy_t - yoy_{t-1}`；`>0` 表加速；map `zscore_clip(accel, 近6季 mean/std)` | fundamentals | 同上 | 同上 |
| `growth.rev_yoy` | 月營收 YoY：`(rev_t - rev_{t-12m})/rev_{t-12m}`；`rank_pct` 對該股近 36 個月 YoY 序列 | revenue | revenue 缺 → 0.5 | **用 announce_date 對齊（§3.6）** |

> EPS 用「單季」不是「年度累計」：年度 EPS 會把舊季度混進來、鈍化加速度訊號。`get_fundamental` 目前只回年度，**需地基一擴充回單季**（見 §5 對接）。

#### 3.3 動能派（momentum.py，4 因子）— 「相對強弱 / 距 52 週高 / MA 斜率」

| 因子 name | 公式 | required_data | 缺料中性 | look-ahead 對齊 |
|---|---|---|---|---|
| `momentum.rs_self` | 自身相對強弱：`ret_N = close_t/close_{t-N} - 1`（N=`rs_window` 預設 60）；`rank_pct` 對該股近 `rank_window`(預設 252) 筆「N 日報酬率」序列 | price | price < N+rank_window → 用現有筆數，<2 回 0.5 | 只用 `close.iloc[:t+1]` |
| `momentum.dist_52w_high` | 距 52 週高：`ratio = close_t / max(high_{t-251..t})`；越接近高點動能越強；`score = clip01((ratio - 0.7)/0.3)`（0.7→0，1.0→1） | price | price<60 → 0.5；不足 252 用現有最高 | rolling max 只到 t |
| `momentum.ma_slope` | MA20 斜率：`slope = (ma20_t - ma20_{t-slope_n})/ma20_{t-slope_n}`（slope_n=10）；`zscore_clip(slope, 近120日斜率 mean/std)` | price | ma20 NaN（<20根）→ 0.5 | ma20 由 add_indicators 算，rolling 不偷看未來 |
| `momentum.above_mas` | 多頭排列強度：`close>ma5>ma20>ma60` 全中=1.0；逐段給分（4 條件各 0.25，缺 NaN 條件視為未達成） | price | <60 根 → 0.5 | 同上 |

```python
# stock_strategies/factors/momentum.py
@register("momentum.dist_52w_high", "momentum", ["price"],
          "距 52 週高點，越接近動能越強", lookback_min=60)
def dist_52w_high(ctx, params):
    c = ctx.price["close"]
    h = ctx.price["high"]
    n = min(252, len(c))
    high_n = h.iloc[-n:].max()
    if high_n <= 0:
        return NEUTRAL
    ratio = c.iloc[-1] / high_n
    return clip01((ratio - 0.7) / 0.3)
```

#### 3.4 籌碼派（chips.py，4 因子）— 「法人連買 / 外資加碼 / 融資退場」

| 因子 name | 公式 | required_data | 缺料中性 | look-ahead 對齊 |
|---|---|---|---|---|
| `chips.foreign_buy_streak` | 外資連續買超天數：從 t 往回數 `foreign_net>0` 連續天數 `k`；`score = clip01(k / streak_cap)`（cap=5，連 5 天以上=1.0） | inst | inst 缺 → 0.5 | inst 每筆有 date，≤t |
| `chips.inst_net_strength` | 三大法人合計近 `n`(預設 5) 日淨買 / 近 `n` 日成交量：`ratio = sum(net_5d)/sum(vol_5d)`；`zscore_clip(ratio, 近60日 ratio mean/std)` | inst, price | 任一缺 → 0.5 | 兩序列都 ≤t，date 對齊 |
| `chips.foreign_holding_up` | 外資持股比率變化：`d = foreign_ratio_t - foreign_ratio_{t-20d}`；`zscore_clip(d, 近120日 d mean/std)` | shareholding | 缺 → 0.5 | shareholding ≤t |
| `chips.margin_retreat` | 散戶退場（看多）：融資餘額下降＝籌碼沉澱；`chg = (margin_bal_t - margin_bal_{t-20d})/margin_bal_{t-20d}`；融資減→分高：`score = clip01(0.5 - zcenter(chg))`，實作為 `1 - zscore_clip(chg, mean, std)` | margin | 缺 → 0.5 | margin ≤t |

> 籌碼資料 FinMind 常有「T+1 才更新」特性：抓資料時用「資料日」(date 欄) 對齊，不要用「執行日」。回測逐日切片時 `inst[inst.date <= as_of]` 自然安全。

#### 3.5 營收動能派（revenue.py，3 因子）— 「MoM/YoY 轉強 / 連續創高」

| 因子 name | 公式 | required_data | 缺料中性 | look-ahead 對齊（最關鍵） |
|---|---|---|---|---|
| `revenue.yoy_accel` | YoY 連續轉強：近 3 個月 YoY 是否遞增 `yoy_{m} > yoy_{m-1} > yoy_{m-2}` 給 1.0；2 個月遞增 0.7；單月轉正 0.55；皆否依最新 YoY `zscore_clip` | revenue | <14 個月 → 0.5 | **announce_date** |
| `revenue.mom_turn` | MoM：`(rev_m - rev_{m-1})/rev_{m-1}`；但排除過年季節性：MoM 與「去年同期 MoM」相比的差；`zscore_clip` | revenue | <14 個月 → 0.5 | announce_date |
| `revenue.new_high_streak` | 月營收連續創高：從最新月往回，`rev_m == max(rev_前12月)` 連續次數 `k`；`clip01(k/3)`（連 3 月新高=1.0） | revenue | <12 個月 → 0.5 | announce_date |

**§3.6 月營收 no look-ahead 對齊（務必照做）**：台灣月營收依規定「次月 10 日前」公告，但實務各家公告日不一。
- FinMind `TaiwanStockMonthRevenue` 回傳欄位含 `revenue_month`（所屬月份，例 2024-03）、`revenue_year`、`revenue`、以及 `date`（**FinMind 的 date 是公告日**）。
- 對齊規則：建 context 時 `revenue = raw[raw["date"] <= as_of]`（用公告日 date 過濾），**不可**用 `revenue_month` 過濾。
- 因子內部「最新可用月」＝ `revenue.sort_values("date").iloc[-1]`，其 `revenue_month` 可能比 `as_of` 落後 1～2 月，這是正確的（反映真實資訊延遲）。
- 若 FinMind 該檔無 `date` 欄（極少數舊資料），地基一 fallback：`announce_date = revenue_month + 月底 + 10 天` 當保守公告日。

#### 3.7 技術反轉派（reversal.py，3 因子）— 「超賣 KD 背離 / 布林下軌 / 跌深量縮」

| 因子 name | 公式 | required_data | 缺料中性 | look-ahead |
|---|---|---|---|---|
| `reversal.kd_oversold` | KD 超賣回升：`k_t < 30` 且 `k_t > k_{t-1}`（自低檔翹頭）→ 基準 0.8；再看 `k_t > d_t`（金叉）+0.2；非超賣 → 0.5 起按 `(30-k)/30` 比例 | price | k/d NaN → 0.5 | k/d 由 add_indicators，ewm 不偷看未來 |
| `reversal.bb_lower_bounce` | 布林下軌反彈：`dist = (close_t - bb_lower_t)/bb_lower_t`；`0 < dist < 0.03` 剛離下軌且向上（`close_t>close_{t-1}`）→ 0.85；`close < bb_lower` 仍在軌下 → 0.6（醞釀）；`close > bb_mid` → 0.35 | price | bb NaN → 0.5 | 同上 |
| `reversal.washout_low_vol` | 跌深反彈量縮：近 20 日跌幅 `dd = close_t/max(close_前20)−1 < -0.1`（跌深）且 `vol_t < 0.7 * vol_20ma`（量縮）→ 0.8；只滿足一個 0.6；皆否 0.4 | price | <20 根 → 0.5 | rolling 到 t |

> 注意 `reversal.bb_lower_bounce` 的核心邏輯刻意對齊舊 `tech_score_at` 的「布林下軌反彈」門檻（`0<dist<0.03`），確保新舊一致、可交叉驗證。

#### 3.8 突破派（breakout.py，3 因子）— 「帶量突破箱頂 / 創波段高」

| 因子 name | 公式 | required_data | 缺料中性 | look-ahead |
|---|---|---|---|---|
| `breakout.box_break` | 整理區間箱頂突破：箱頂 `box_top = max(high_{t-box_n..t-1})`（box_n=20，**不含當日**避免自我參照）；`close_t > box_top` 且突破幅度 `(close_t/box_top−1)` map：剛突破 0~3% → 0.7~1.0；未破 → `clip01(close_t/box_top * 0.6)` 接近箱頂給部分分 | price | <box_n+1 → 0.5 | 箱頂用 `iloc[-box_n-1:-1]`，嚴格不含 t |
| `breakout.vol_confirm_break` | 帶量確認：在 box_break 成立（`close_t>box_top`）前提下，`vol_t / vol_20ma`：≥2 → 1.0、≥1.5 → 0.8、<1.0（假突破無量）→ 0.3；未突破 → 0.5 | price | <20 → 0.5 | vol_20ma 用 `iloc[-20:-1]` 不含當日量自身偏誤可選；此處含 t 影響小 |
| `breakout.swing_new_high` | 創波段新高：`high_t == max(high_{t-swing_n..t})`（swing_n=60）→ 1.0；距波段高 `<2%` → 0.75；否則 `rank_pct` of close in 近 swing_n | price | <60 → 0.5 | rolling 到 t |

```python
# stock_strategies/factors/breakout.py
@register("breakout.box_break", "breakout", ["price"],
          "突破近 20 日整理箱頂", lookback_min=21)
def box_break(ctx, params):
    n = params.get("box_n", 20)
    h = ctx.price["high"]; c = ctx.price["close"]
    if len(h) < n + 1:
        return NEUTRAL
    box_top = h.iloc[-n-1:-1].max()        # 嚴格不含當日
    if box_top <= 0:
        return NEUTRAL
    ratio = c.iloc[-1] / box_top
    if ratio > 1.0:
        return clip01(0.7 + (ratio - 1.0) / 0.03 * 0.3)
    return clip01(ratio * 0.6)
```

#### 3.9 legacy 包裝（legacy.py）— 向後相容舊四訊號 + 量價型態

把舊 `tech_score_at` 四訊號與 `detect_patterns` 包成因子，**讓既有策略不改 JSON 也能跑、新策略也能混用**：

```python
# stock_strategies/factors/legacy.py
from .registry import register
from .base import clip01, NEUTRAL
from ..indicators import tech_score_at
from ..volume import detect_patterns

@register("legacy.tech_score", "legacy", ["price"],
          "舊四訊號技術分(0-100)→0..1", lookback_min=60)
def legacy_tech(ctx, params):
    # 直接複用舊函式；params 透傳四個 use_* 開關
    res = tech_score_at(ctx.price.iloc[-1], params)
    return clip01(res["score"] / 100.0)

@register("legacy.ma_alignment", "legacy", ["price"], "舊均線多頭", lookback_min=60)
def legacy_ma(ctx, params):
    return clip01(tech_score_at(ctx.price.iloc[-1],
        {"use_ma_alignment": True, "use_bollinger_bounce": False,
         "use_kd_golden_cross": False, "use_macd_bullish": False})["score"] / 100.0)

# 同理 legacy.bollinger_bounce / legacy.kd_golden_cross / legacy.macd_bullish
# 各自只開一個 use_* 開關，score/100 → 0..1

@register("legacy.volume_bonus", "legacy", ["price"],
          "舊量價型態 bonus(-20..+18)→0..1", lookback_min=21)
def legacy_volume(ctx, params):
    vp = detect_patterns(ctx.price, idx=-1)
    # bonus 區間約 [-20, +18]；線性映到 0..1，0 分→0.53 中性偏上
    return clip01((vp["bonus"] + 20) / 38.0)
```

**向後相容轉接**：地基四 loader 升級時提供 `legacy_params_to_factors(params: dict) -> list[dict]`：把舊扁平 params 的五個 `use_*` 開關翻成 `factors` 清單，例如 `use_ma_alignment=True` → `{"name":"legacy.ma_alignment","weight":1}`，`use_volume_patterns=True` → 加 `legacy.volume_bonus`，全部等權。這樣**沒有 `factors` 欄的舊策略**自動退化成「舊四訊號等權平均」，分數與舊 `tech_score` 路徑近似（差異僅在量價從 ±bonus 變因子權重，需在測試標註容差）。

#### 3.10 EPS 單季對齊細節（補 §3.2）

`fundamentals.eps_q` 的鍵 `(year, quarter)` 必須只放「在 as_of 之前實際公布」的季。財報公布日規則（保守）：
- Q1 → 5/15 前、Q2 → 8/14 前、Q3 → 11/14 前、Q4(年報) → 隔年 3/31 前。
- 地基一建 context 時：`eps_q[(y,q)]` 只在 `as_of >= deadline(y,q)` 才放入。FinMind `TaiwanStockFinancialStatements` 回傳的 `date` 是財報「所屬期末日」，**不是公布日**，故必須用上述 deadline 推算公布日來過濾（不可直接 `date<=as_of`，否則季末日早於公布日 → look-ahead）。

### 4. 因子在引擎中的使用（與地基三/四交界）

地基三 `backtest_v2` 逐日呼叫流程（本章只定義被呼叫的介面，不實作回測）：

```python
from stock_strategies.factors.registry import compute_factor
from stock_strategies.factors.context import build_context

def strategy_score_at(strategy_def: dict, raw_bundle: dict, as_of) -> dict:
    ctx = build_context(strategy_def["id_stock"], as_of, raw_bundle)  # 切片到 as_of
    scores, total_w, used = 0.0, 0.0, []
    for f in strategy_def.get("factors", []):
        s = compute_factor(f["name"], ctx, strategy_def.get("factor_params", {}))
        w = float(f.get("weight", 1.0))
        scores += s * w; total_w += w
        used.append({"name": f["name"], "score": round(s, 3), "weight": w})
    composite = (scores / total_w) if total_w > 0 else 0.5   # 0..1
    return {"composite": composite, "factors": used}         # composite*100 可餵舊 min_score
```

> `composite ∈ [0,1]`，乘 100 後與舊 `min_total_score_for_buy`（0..100）量綱一致，地基四 `evaluate` 可平滑接管。

### 5. 與現有 code 的對接點（明確改哪裡）

| 動作 | 檔案 | 內容 |
|---|---|---|
| 新增 | `stock_strategies/factors/*`（見 §2 樹狀） | 全新模組，不動既有檔行為 |
| **擴充** | `stock_strategies/data.py` | 新增 `get_institutional(stock_id, years)`（`TaiwanStockInstitutionalInvestorsBuySell`）、`get_monthly_revenue(stock_id, years)`（`TaiwanStockMonthRevenue`，**保留 announce date 欄**）、`get_valuation(stock_id, years)`（`TaiwanStockPER`，含 per/pbr/dividend_yield）、`get_margin(stock_id, years)`（`TaiwanStockMarginPurchaseShortSale`）、`get_shareholding(stock_id, years)`（`TaiwanStockShareholding` 外資持股比率）。全部沿用既有 `fetch_finmind`（retry/timeout 不重造）。 |
| **擴充** | `stock_strategies/data.py:get_fundamental` | 新增回傳 `eps_q`（單季 EPS dict）。保留原 `eps`/`roe` 年度欄位不破壞 `evaluate`。 |
| 新增 | `stock_strategies/factors/context.py:build_context` | 把上述抓到的 raw 切片成 `FactorContext`（含 add_indicators、各 df 依 date/announce_date 濾 ≤ as_of）。 |
| **不改** | `indicators.py` / `volume.py` | legacy 因子直接 import 複用，零修改。 |
| 沿用 | `loader.py` | 升級 schema 時新增 `factors` 欄；本章提供 `legacy_params_to_factors` 供退化相容（實際接線在地基四）。 |

**FinMind 限流 / 缺漏處理**（沿用 `fetch_finmind` 的 retry，再加一層）：
- `build_context` 對每個 `get_*` 包 try/except，任一資料源失敗 → 該欄設 `None`（因子自動回中性 0.5），**不讓整檔評估中斷**。
- 回測「一次抓全期間、之後逐日切片」：每檔每資料源只打 1 次 FinMind（不是逐日打），把限流風險降到最低；切片用 `df[df.date <= as_of]` 純記憶體操作。
- 對 `fetch_finmind` 回 402/429（FinMind 額度用罄）的情況，地基一應在 `data.py` 升級時把 `requests.HTTPError` 也納入 retry 並指數退避；本章僅依賴其回 DataFrame，空 DataFrame → 中性。

### 6. 邊界與錯誤處理（逐項照做）

- **新股上市不足 N 天**：`lookback_min`（registry 每因子宣告，預設 60）擋住 → 不足回 0.5。動能/突破類 `lookback_min` 設較大（60），價值/營收類靠各自 `<20 樣本→中性` 內檢。
- **停牌 / 缺交易日**：price 用 FinMind 既有交易日序列（停牌日本就不在），不要 reindex 補日，否則 rolling 視窗會被污染。
- **除權息跳空**：因子用「相對自身百分位 / 報酬率」為主，除權息造成的一次性跳空對百分位影響有限；若地基一能提供還原股價則優先用還原價算 `momentum.*`（標 open_question）。
- **PER 為負（虧損股）**：`value.cheap_pe`/`value.cheap_pb` 對 `≤0` 回 0.5，不污染。
- **營收/財報延遲公告**：靠 announce_date / deadline 對齊（§3.6、§3.10），延遲是「正確的資訊延遲」不是 bug。
- **registry 引用不存在因子**：`compute_factor` 回 0.5 並由上層 log warning，策略不崩。
- **單因子例外**：`FactorEntry.__call__` 已 try/except 兜底回中性，保證回測 5 年逐日呼叫不會因單點 NaN 中斷。
- **survivorship bias**：本章因子層不選股池；但提醒地基三 universe 必須用「該歷史日實際存在/上市的清單」，不可用今日成分股回測歷史（標 open_question 給 universe 章）。

### 7. 關鍵單元測試點（給構造資料 → 預期分數）

測試檔：`tests/factors/test_*.py`（pytest，uv 跑：`uv run pytest tests/factors`）。每個因子至少 3 例：看多極端、看空極端、缺料中性。

**通用工具測試（base.py）**
1. `clip01(1.5)==1.0`、`clip01(-0.2)==0.0`、`clip01(float('nan'))==0.5`、`clip01(None)==0.5`。
2. `rank_pct(pd.Series([1,2,3,4]), 4)==1.0`、`rank_pct(..., 1)==0.25`、空序列 → 0.5。
3. `zscore_clip(value=mean, mean, std)==0.5`、`value=mean+2std → 1.0`、`std=0 → 0.5`。

**價值派**
4. `value.cheap_pb`：構造 PBR 序列 `[5,5,...,1]`（當日最低）→ 期望 ≈1.0（接近 1，因 rank_pct→0）；PBR 序列當日最高 → ≈0.0；valuation=None → 0.5；`pbr_t≤0` → 0.5。
5. `value.high_yield`：殖利率序列當日最高 → ≈1.0；`yield_t==0` → 0.3。

**成長派**
6. `growth.eps_yoy`：`eps_q` 最新季 = 去年同季 2 倍且歷史 yoy 平穩 → >0.5（明顯看多）；最新季腰斬 → <0.5；只有 1 年資料 → 0.5。
7. `growth.eps_accel`：yoy 連 2 季放大 → >0.5；yoy 連 2 季縮小 → <0.5。

**動能派**
8. `momentum.dist_52w_high`：`close == 252日最高` → 1.0；`close == 0.7*高` → 0.0；`close == 0.85*高` → 0.5。
9. `momentum.above_mas`：構造 `close>ma5>ma20>ma60` → 1.0；全部跌破 → 0.0；price<60 根 → 0.5。
10. `momentum.ma_slope`：ma20 單調上升序列 → >0.5；下降 → <0.5。

**籌碼派**
11. `chips.foreign_buy_streak`：inst 末 5 日 `foreign_net` 全正 → 1.0；末日為負 → 0.0；inst=None → 0.5。
12. `chips.margin_retreat`：融資餘額近 20 日大幅下降 → >0.5（看多）；大幅上升 → <0.5。

**營收動能派**
13. `revenue.new_high_streak`：最近 3 月營收逐月創 12 月新高 → 1.0；無創高 → ≈0.0/中性；<12 月 → 0.5。
14. `revenue.yoy_accel`：近 3 月 YoY `[10%,20%,35%]` 遞增 → 1.0；遞減 → <0.55。
15. **look-ahead 對齊測試（最重要）**：構造 revenue，其中一筆 `revenue_month=2024-03` 但 `date(announce)=2024-04-12`；以 `as_of=2024-04-05` 建 context → 該筆**不可**進入 ctx.revenue（驗證用公告日過濾，不是所屬月）。

**技術反轉派**
16. `reversal.bb_lower_bounce`：構造 `0<dist<0.03` 且 `close_t>close_{t-1}` → ≈0.85；`close<bb_lower` → 0.6；`close>bb_mid` → 0.35。對齊舊 `tech_score_at` 同 row 的「布林下軌反彈」訊號是否同時觸發（交叉驗證）。
17. `reversal.kd_oversold`：`k=20 且 k>k_prev` → ≈0.8+；`k=85` → 中性偏下。

**突破派**
18. `breakout.box_break`：close 剛突破近 20 日箱頂 1% → ≈0.8；未突破在箱內 → <0.6；**驗證箱頂不含當日**（當日 high 設超大也不影響 box_top）。
19. `breakout.vol_confirm_break`：突破且量 ≥2x 20ma → 1.0；突破但量 <1x（假突破）→ 0.3。

**legacy 向後相容**
20. `legacy.tech_score`：同一 row 餵 `legacy.tech_score(ctx,params)*100` 應 `== tech_score_at(row,params)["score"]`（容差 ±1，浮點 round）。
21. `legacy_params_to_factors`：舊預設 params（五 `use_*` 全 True）→ 產出含 5 個 legacy 因子等權；全 False → 空清單（上層回中性）。

**registry**
22. `compute_factor("不存在的因子", ctx, {})==0.5`；`list_factors("value")` 回 3 筆。
23. `compute_factor` 在因子內部丟例外時回 0.5（注入會丟錯的假因子驗 try/except 兜底）。

### 8. 因子總表（供地基三/四 / AI 生成器引用）

| school | 因子 name | required_data | 方向 |
|---|---|---|---|
| value | value.cheap_pb, value.cheap_pe, value.high_yield | valuation | 越便宜越高 |
| growth | growth.eps_yoy, growth.eps_accel, growth.rev_yoy | fundamentals/revenue | 加速越高 |
| momentum | momentum.rs_self, momentum.dist_52w_high, momentum.ma_slope, momentum.above_mas | price | 越強越高 |
| chips | chips.foreign_buy_streak, chips.inst_net_strength, chips.foreign_holding_up, chips.margin_retreat | inst/shareholding/margin | 法人買、散戶退越高 |
| revenue | revenue.yoy_accel, revenue.mom_turn, revenue.new_high_streak | revenue | 轉強越高 |
| reversal | reversal.kd_oversold, reversal.bb_lower_bounce, reversal.washout_low_vol | price | 超賣回升越高 |
| breakout | breakout.box_break, breakout.vol_confirm_break, breakout.swing_new_high | price | 帶量突破越高 |
| legacy | legacy.tech_score, legacy.ma_alignment, legacy.bollinger_bounce, legacy.kd_golden_cross, legacy.macd_bullish, legacy.volume_bonus | price | 相容舊系統 |

AI 生成器（`api/services/ai_generator.py`）的 system prompt 應改為「從上表 name 中挑因子並給 weight」，而非舊的 `use_*` 開關（標 open_question：何時切換 prompt）。


---

## §8 地基三：回測引擎升級（讓「準」可被驗證）

> 目標：用 `backtest_v2` 取代/擴充現有 `stock_strategies/backtest.py` 只算勝率、固定持有的 `backtest()`。新引擎要能：**分市況回測**、給出**整體 CAGR / 最大回撤 / 夏普 / Sortino**、做**樣本外驗證（IS/OOS）**、跑**多持有週期變體**，並把**交易成本、look-ahead、survivorship** 處理乾淨。本章是純 Python 確定性地基的一部分，研發層 workflow 與 runtime 每日 pipeline 共用同一套引擎。

### 1. 範圍與不可推翻的前提

- 進出場可執行性沿用現有慣例：**訊號日 = 第 i 天收盤產生**，**進場 = 第 i+1 天開盤**（與 `backtest.py` 第 38 行、`performance.py` 的 T+1 進場一致）。本章只是把結算邏輯做厚，不改這個時序。
- 因子值只能用到 t（含 t）的資料；進場價來自 t+1 開盤；結算用 t+2 起的 K 線（與舊 `backtest.py` 的 `df.iloc[idx+2:...]` 一致）。**避免 look-ahead 的鐵律寫死在引擎裡，不靠呼叫端自律**。
- `backtest_v2` 是**純函式**：吃「已經算好因子/regime 的 DataFrame」+ 策略定義，回統計 dict。它**不自己抓資料**（抓資料、算因子、算 regime 由地基一/二負責），這樣才好做單元測試（合成資料即可驗證）。
- 向後相容：保留舊 `backtest(df, params)` 不刪，內部改成呼叫 `backtest_v2` 的 thin wrapper（見 §9），讓現有 `evaluate.py` 不立刻爆。

---

### 2. 新增檔案與職責

| 檔案 | 職責 |
|---|---|
| `stock_strategies/regime.py` | `regime_classify(taiex_df)`、`get_regime_series_for(price_df, taiex_df)`：算大盤 regime 標籤序列 |
| `stock_strategies/backtest_v2.py` | `backtest_v2(strategy_def, price_df, regime_series, costs=...)` 主引擎 + 內部結算器 |
| `stock_strategies/costs.py` | 台股交易成本模型（手續費 / 證交稅 / 滑價）常數與 `apply_costs()` |
| `stock_strategies/stats.py` | 報酬序列 → CAGR/MDD/Sharpe/Sortino/勝率 + 樣本顯著性標註 |
| `tests/test_regime.py`、`tests/test_backtest_v2.py`、`tests/test_stats.py` | 合成資料單元測試（見 §11） |

修改：`stock_strategies/backtest.py`（改成 wrapper）、`stock_strategies/config.py`（加成本與 regime 常數）、`stock_strategies/evaluate.py`（遷移呼叫，§10）。

---

### 3. 資料契約：`price_df` 與 `regime_series`

`backtest_v2` 吃的 `price_df` 是「**一檔股票**、已 `add_indicators()`、且已附上因子欄位」的時間序，schema：

```
price_df: pd.DataFrame  # index = RangeIndex，已 sort by date asc、reset_index
  必要欄位:
    date    : datetime64    # 交易日（日線）
    open    : float         # 進場用，沿用現有欄名
    high    : float
    low     : float
    close   : float
    volume  : float
  地基一/二附加（缺則對應因子回 None，不致命）:
    factor__<name> : float in [0,1]   # 每個因子一欄，命名前綴 factor__
  地基二可選（停牌/新股偵測用）:
    is_tradable : bool                 # 該日是否可成交（見 §7 停牌處理）
```

`regime_series`：與 `price_df` **同長度、同 index** 的 `pd.Series`，值域 `{"bull","range","bear"}`，每個交易日一個標籤，代表「**該日大盤所處的 regime**」。由 §4 的 `get_regime_series_for()` 產生。引擎用「**進場日 t+1 當天**」的 regime 標籤把這筆交易歸到某個 regime 桶。

> 為什麼用進場日而非訊號日歸桶：交易在哪個市況「開倉」，就用那個市況評它的表現，符合直覺且無 look-ahead（t+1 的大盤收盤其實要到 t+1 收盤才知道——所以歸桶用的是**進場日的前一日（即訊號日 t）已知的 regime 標籤**，見 §4 的對齊規則）。

---

### 4. 大盤 regime 判定演算法（`regime.py`）

#### 4.1 `regime_classify(taiex_df) -> pd.Series`

輸入加權指數日 K（欄位至少 `date, close`，沿用 `market.py::_fetch_taiex()` 的 rename 慣例 `max→high/min→low`）。輸出與輸入**同 index** 的 `Series[str]`，每日標 `bull/range/bear`。**可在歷史任一日計算**（rolling，不看未來）。

演算法（確定性，三因子投票）：

```python
def regime_classify(taiex_df: pd.DataFrame,
                    ma_fast: int = 20,   # 月線
                    ma_slow: int = 60,   # 季線
                    slope_win: int = 20, # 月線斜率觀察窗
                    vol_win: int = 20,   # 波動率窗
                    slope_eps: float = 0.0) -> pd.Series:
    df = taiex_df.sort_values("date").reset_index(drop=True).copy()
    c = pd.to_numeric(df["close"], errors="coerce")
    ma_f = c.rolling(ma_fast).mean()
    ma_s = c.rolling(ma_slow).mean()
    # 月線斜率：用 (今日月線 / slope_win 日前月線 - 1)，避免價格絕對值影響
    slope = ma_f / ma_f.shift(slope_win) - 1.0
    # 年化波動率（輔助，過濾盤整）：日報酬 std * sqrt(252)
    ret = c.pct_change()
    vol = ret.rolling(vol_win).std() * (252 ** 0.5)

    out = pd.Series("range", index=df.index, dtype=object)
    bull = (c > ma_s) & (slope > slope_eps) & ma_f.notna() & ma_s.notna()
    bear = (c < ma_s) & (slope < -slope_eps) & ma_f.notna() & ma_s.notna()
    out[bull] = "bull"
    out[bear] = "bear"
    # 其餘（含站上季線但斜率走平、或跌破季線但斜率轉正）= range
    # 暖機期（ma_slow 尚未成形）一律 range，避免拿半成品判斷
    warmup = ma_s.isna() | slope.isna()
    out[warmup] = "range"
    return out
```

判定規則白話：

| 條件 | 標籤 |
|---|---|
| 收盤 > 季線(MA60) **且** 月線斜率為正 | `bull` |
| 收盤 < 季線(MA60) **且** 月線斜率為負 | `bear` |
| 其餘（含暖機期、訊號矛盾、走平） | `range` |

- `slope` 用「月線相對 20 日前的變化率」量化斜率正負，比直接 diff 穩定（對指數絕對值不敏感）。
- `vol`（年化波動率）目前**保留為輔助欄位**，預設不參與分類；若日後要把「高波動但無趨勢」更明確劃進 range，可加 `(vol > vol_high) & ~bull & ~bear → range`（已是 range，無需改）。先不過度設計，門檻留 `open_questions`。
- **無 look-ahead**：全部 rolling 到當日為止；`shift(slope_win)` 取過去值；暖機期標 range。

#### 4.2 `get_regime_series_for(price_df, taiex_df) -> pd.Series`

把大盤 regime「對齊到個股交易日」並做 **t→t+1 歸桶對齊**：

```python
def get_regime_series_for(price_df, taiex_df) -> pd.Series:
    reg = regime_classify(taiex_df)                      # index 對 taiex
    reg.index = pd.to_datetime(taiex_df["date"]).values  # 改用日期當 key
    # 用「訊號日 t（即進場日 t+1 的前一日）已知的 regime」歸桶 → 對個股日期 asof 對齊
    s = pd.Series(price_df["date"].values, name="date")
    aligned = reg.reindex(pd.to_datetime(price_df["date"]), method="ffill")
    aligned.index = price_df.index
    return aligned.fillna("range")
```

- `method="ffill"`：個股某日若大盤該日無資料（理論上不會，但防個股/大盤交易日不一致），用**最近一個過去**的 regime，**絕不用未來**。
- 缺值補 `range`（中性），不讓 NaN 流進結算。

---

### 5. 主引擎介面：`backtest_v2`

```python
def backtest_v2(
    strategy_def: dict,            # 升級版策略 schema（見地基契約），需含 entry/exit/factors/regime_overrides/backtest
    price_df: pd.DataFrame,        # §3 schema，已含 factor__* 欄
    regime_series: pd.Series,      # §4，與 price_df 同 index
    costs: dict | None = None,     # §6，None → 用 config.COSTS
    hold_variants: list[int] | None = None,  # 多持有週期，None → 用 exit.hold_days 單一值
) -> dict:
    ...
```

回傳結構（**這是對外契約，其他章與審查官對齊用**）：

```python
{
  "overall": {
    "winrate": 0.58, "avg_return": 0.021, "cagr": 0.143,
    "max_drawdown": -0.182, "sharpe": 1.12, "sortino": 1.66,
    "samples": 134, "profit_factor": 1.7,
    "significance": "OK"  # OK / WEAK / INSUFFICIENT，見 §8
  },
  "by_regime": {
    "bull":  {"winrate":..,"avg_return":..,"sharpe":..,"sortino":..,"max_drawdown":..,"samples":..,"significance":..},
    "range": {...},
    "bear":  {...}
  },
  "oos": {                  # 樣本外驗證，見 §8.1
    "in_sample":  {"period":["2021-06","2023-12"], "winrate":..,"avg_return":..,"sharpe":..,"samples":..},
    "out_sample": {"period":["2024-01","2025-06"], "winrate":..,"avg_return":..,"sharpe":..,"samples":..},
    "degradation": {"winrate_drop": 0.07, "sharpe_drop": 0.4, "verdict": "ROBUST"}  # ROBUST/OVERFIT/INSUFFICIENT
  },
  "hold_period_variants": {  # 多持有週期，僅當 hold_variants 給多個值
    "5":  {"winrate":..,"avg_return":..,"sharpe":..,"samples":..},
    "10": {...}, "20": {...}
  },
  "trades": [               # 可選明細（debug / 前端畫權益曲線），預設回，量大可關
    {"signal_date":"2024-03-04","entry_date":"2024-03-05","entry":..,"exit_date":..,
     "exit":..,"exit_reason":"target|stop|expire|trailing","regime":"bull",
     "gross_return":0.10,"net_return":0.094,"hold_days":7}
  ],
  "meta": {"costs": {...}, "universe_note": "...", "engine": "v2"}
}
```

#### 5.1 主流程（pseudo）

```python
def backtest_v2(strategy_def, price_df, regime_series, costs=None, hold_variants=None):
    costs = costs or CONFIG_COSTS
    entry_cfg = strategy_def.get("entry", {})
    exit_cfg  = strategy_def.get("exit", {})
    overrides = strategy_def.get("regime_overrides", {})
    base_hold = int(exit_cfg.get("hold_days", 20))
    hold_variants = hold_variants or [base_hold]

    # 1) 逐日算策略分數（純函式，只用到 t 的 factor__ 欄 + regime[t]）
    signals = _signal_days(strategy_def, price_df, regime_series)  # -> list[int]（訊號日 iloc i）

    # 2) 主結算用 base_hold；變體各自再結算一遍（共用 signals）
    main_trades = [_settle_one(price_df, regime_series, i, base_hold, exit_cfg, overrides, costs)
                   for i in signals]
    main_trades = [t for t in main_trades if t is not None]

    result = {
        "overall": _agg(main_trades),
        "by_regime": {r: _agg([t for t in main_trades if t["regime"] == r])
                      for r in ("bull","range","bear")},
        "oos": _oos_split(main_trades, oos_cfg=strategy_def.get("backtest", {})),
        "meta": {"costs": costs, "engine": "v2",
                 "universe_note": price_df.attrs.get("universe_note","")},
    }
    if len(hold_variants) > 1:
        result["hold_period_variants"] = {
            str(h): _agg([_settle_one(price_df, regime_series, i, h, exit_cfg, overrides, costs)
                          for i in signals if _settle_one(...) is not None])
            for h in hold_variants
        }
    result["trades"] = main_trades
    return result
```

#### 5.2 訊號日判定 `_signal_days`

把舊的「`tech_score_at >= min_score`」升級成「**因子加權分數 >= entry.min_score**」，並套 regime_override：

```python
def _signal_days(strategy_def, price_df, regime_series) -> list[int]:
    factors = strategy_def.get("factors", [])   # [{name, weight}, ...]
    entry = strategy_def.get("entry", {})
    overrides = strategy_def.get("regime_overrides", {})
    base_min = float(entry.get("min_score", 60))
    out = []
    n = len(price_df)
    max_hold = max(int(strategy_def.get("exit",{}).get("hold_days",20)), 1)
    # 暖機：前 60 天指標未成形（與舊 backtest 的 range(60,...) 一致）
    for i in range(60, n - max_hold - 1):
        reg = regime_series.iloc[i]
        ov = overrides.get(reg, {})
        if ov.get("stop_entry"):           # 該 regime 停止進場（如 bear 不買）
            continue
        min_score = float(ov.get("min_score", base_min))
        score = _weighted_factor_score(price_df.iloc[i], factors)  # 0..100，缺因子用 0.5 中性
        if score is None:                  # 全因子缺 → 跳過
            continue
        if score >= min_score:
            out.append(i)
    return out

def _weighted_factor_score(row, factors) -> float | None:
    num = den = 0.0
    for f in factors:
        col = f"factor__{f['name']}"
        w = float(f.get("weight", 0))
        if col not in row or pd.isna(row[col]):
            val = 0.5            # 缺資料中性（契約規定因子缺回 None/0.5）
        else:
            val = float(row[col])
        num += w * val; den += w
    if den <= 0:
        return None
    return (num / den) * 100.0
```

> 與舊四開關相容：地基二會把舊的 `use_ma_alignment` 等開關映射成 `factor__ma_alignment` 等因子欄；若 `strategy_def` 沒有 `factors`，wrapper（§9）會退回舊 `tech_score_at`。

---

### 6. 交易成本與滑價模型（`costs.py`）

台股實際成本：**買賣各付手續費 0.1425% × 券商折扣**，**賣出再付證交稅 0.3%**（一般股票；當沖另計，本系統波段不做當沖，用 0.3%）。再加**滑價**（進出各吃一點）。

```python
# config.py 追加
COSTS = {
    "fee_rate": 0.001425,      # 單邊手續費率（法定上限）
    "fee_discount": 0.30,      # 券商折扣（3 折），可被策略/全域覆寫
    "tax_rate": 0.003,         # 證交稅，賣出單邊
    "slippage": 0.0015,        # 單邊滑價（0.15%），保守估，進出各吃
    "min_fee": 20.0,           # 最低手續費 20 元（小資金部位會放大成本，見 note）
}
```

淨報酬結算（以「買進成本價」對「賣出實收價」算）：

```python
def apply_costs(entry_px, exit_px, costs) -> float:
    fee = costs["fee_rate"] * costs["fee_discount"]
    # 進場：開盤價 + 滑價（買在更貴一點）；手續費
    buy_px  = entry_px * (1 + costs["slippage"])
    buy_cost = buy_px * (1 + fee)
    # 出場：成交價 - 滑價（賣在更便宜一點）；手續費 + 證交稅
    sell_px = exit_px * (1 - costs["slippage"])
    sell_recv = sell_px * (1 - fee - costs["tax_rate"])
    return sell_recv / buy_cost - 1.0   # 淨報酬率
```

- 來回總摩擦 ≈ 滑價 0.15%×2 + 手續費 0.04275%×2 + 稅 0.3% ≈ **0.685%**（3 折下）。每筆交易都吃，短週期高頻策略會被這個成本咬掉，正是要讓「準」可被驗證的關鍵。
- `min_fee` 預設**不套用在報酬率計算**（因為報酬率與部位大小無關），僅在 `meta` 標註提醒前端「小於約 14 萬的部位手續費會被 20 元低消放大」。要不要把低消折算進報酬率列入 `open_questions`。
- `target_return` / `stop_loss` 命中時，**用觸發價結算後再扣成本**（不是用理想 target 直接當報酬），見 §7。

---

### 7. 單筆交易結算 `_settle_one`（停利/停損/到期/移動停利）

```python
def _settle_one(price_df, regime_series, i, hold, exit_cfg, overrides, costs):
    """i = 訊號日 iloc。進場 = i+1 開盤。結算逐日掃 i+2..i+1+hold。"""
    n = len(price_df)
    if i + 1 >= n:
        return None
    entry_row = price_df.iloc[i + 1]
    entry = entry_row.get("open")
    if entry is None or pd.isna(entry) or entry <= 0:
        return None
    if not bool(entry_row.get("is_tradable", True)):   # 進場日停牌 → 該訊號作廢
        return None

    # regime override（用進場日所屬 regime 取出該市況的停利停損）
    reg = regime_series.iloc[i + 1]
    ov = overrides.get(reg, {})
    target = float(ov.get("target_return", exit_cfg.get("target_return", 0.10)))
    stop   = float(ov.get("stop_loss",     exit_cfg.get("stop_loss",     0.08)))
    trail  = ov.get("trailing", exit_cfg.get("trailing"))   # None 或 float（如 0.05 表回落 5% 出場）

    hi_target = entry * (1 + target)
    lo_stop   = entry * (1 - stop)
    peak = entry
    exit_px = exit_reason = exit_date = None

    last = min(i + 1 + hold, n - 1)
    for j in range(i + 2, last + 1):
        row = price_df.iloc[j]
        if not bool(row.get("is_tradable", True)):
            continue   # 停牌日跳過，不結算也不計入持有天數耗盡（保守）
        hi, lo, cl = row["high"], row["low"], row["close"]
        # 7.1 同日先停損後停利（保守，避免高估）：先看 low 是否破停損
        if lo <= lo_stop:
            exit_px, exit_reason, exit_date = lo_stop, "stop", row["date"]; break
        # 7.2 移動停利：peak 更新後，若回落超過 trail 比例則出場
        if trail:
            peak = max(peak, hi)
            trail_stop = peak * (1 - float(trail))
            if lo <= trail_stop and peak > entry:   # 只有獲利過才啟動移動停利
                exit_px, exit_reason, exit_date = trail_stop, "trailing", row["date"]; break
        # 7.3 固定停利
        if hi >= hi_target:
            exit_px, exit_reason, exit_date = hi_target, "target", row["date"]; break

    if exit_px is None:   # 7.4 持有到期 → 用最後一個可成交日收盤
        last_row = price_df.iloc[last]
        exit_px, exit_reason, exit_date = last_row["close"], "expire", last_row["date"]

    if exit_px is None or pd.isna(exit_px):
        return None
    gross = exit_px / entry - 1.0
    net = apply_costs(entry, exit_px, costs)
    return {
        "signal_date": str(price_df.iloc[i]["date"].date()),
        "entry_date": str(entry_row["date"].date()),
        "entry": round(float(entry), 2),
        "exit_date": str(pd.Timestamp(exit_date).date()),
        "exit": round(float(exit_px), 2),
        "exit_reason": exit_reason,
        "regime": reg,
        "gross_return": round(float(gross), 4),
        "net_return": round(float(net), 4),
        "hold_days": hold,
    }
```

關鍵設計：

- **同日停損優先於停利**（§7.1 在 §7.3 之前）：日線無法知道盤中誰先到，採保守假設先停損，避免系統性高估勝率。這點與舊 `backtest.py`「`hit_target and not hit_stop`」的樂觀假設不同，**新引擎刻意更悲觀**，這正是「讓準可被驗證」的態度。
- **移動停利**只在「已獲利（peak > entry）」後啟動，避免一進場就被洗掉。
- 命中價用**觸發價**（`lo_stop`/`hi_target`/`trail_stop`）而非理想報酬，再扣成本，貼近真實成交。
- 持有到期用最後**可成交日**收盤，停牌日不結算（§7 內 `is_tradable` 檢查）。

---

### 8. 統計、樣本外、顯著性（`stats.py`）

#### 8.1 樣本外（IS/OOS）時間切分 `_oos_split`

- **按時間切，不按隨機切**（金融時序，隨機切會洩漏未來）。預設 `oos_split=0.30`：**前 70% 訊號日當 in-sample（設計用），後 30% 當 out-of-sample（驗證用）**。
- 切點以**訊號日日期排序後的第 70 百分位日期**為界，前者進 IS、後者進 OOS（避免一筆交易跨界：以訊號日歸屬）。
- 退化判定 `degradation.verdict`：
  - `OVERFIT`：OOS 勝率比 IS 掉 > 0.15 **或** OOS sharpe 掉 > 0.6（且 OOS 樣本 ≥ 20）。
  - `ROBUST`：OOS 勝率掉 ≤ 0.08 且 OOS 仍 > 0.5。
  - `INSUFFICIENT`：OOS 樣本 < 20，不下結論。

```python
def _oos_split(trades, oos_cfg):
    ratio = float(oos_cfg.get("oos_split", 0.30))
    if len(trades) < 20:
        return {"in_sample": _agg(trades), "out_sample": None,
                "degradation": {"verdict": "INSUFFICIENT"}}
    ts = sorted(trades, key=lambda t: t["signal_date"])
    cut = int(len(ts) * (1 - ratio))
    is_t, oos_t = ts[:cut], ts[cut:]
    is_agg, oos_agg = _agg(is_t), _agg(oos_t)
    ...verdict 邏輯如上...
```

#### 8.2 績效指標公式（全部用 `net_return` 算）

每筆交易報酬 `r_k = net_return`，持有 `h_k` 個交易日。

- **winrate** = #{r_k > 0} / N
- **avg_return** = mean(r_k)
- **profit_factor** = Σ(r_k where r_k>0) / |Σ(r_k where r_k<0)|（分母 0 → `inf`，標 None 給前端）
- **每筆年化**：把每筆報酬折成日報酬再年化彙總。用「**交易等權、以平均持有天數年化**」：
  - 平均日報酬 `mu_d = mean(r_k) / mean(h_k)`
  - 日報酬 std `sigma_d`：把每筆報酬除以其持有天數得近似日報酬序列 `{r_k/h_k}`，取 std
  - **Sharpe** = (mu_d / sigma_d) × √252（無風險利率設 0，台股慣例可忽略；列 `open_questions`）
  - **Sortino** = (mu_d / sigma_d_downside) × √252，其中 `sigma_d_downside` 只用 `r_k/h_k < 0` 的樣本算 std
- **CAGR**：用**權益曲線**算，不是平均。把交易**按進場日排序、串成單一部位的權益序列**（假設全壓、序列複利，無重疊；重疊交易處理見 §8.3），
  - `equity_end = Π(1 + r_k)`，總跨年 `years = (last_exit_date - first_entry_date).days / 365.25`
  - `CAGR = equity_end ** (1/years) - 1`（years < 0.5 → 標 None，期間太短）
- **max_drawdown**：對權益曲線 `E_t = Π_{k≤t}(1+r_k)`，`MDD = min_t (E_t / cummax(E_t) - 1)`（負值，越深越糟）。

```python
def max_drawdown(equity: list[float]) -> float:
    peak = -inf; mdd = 0.0
    for v in equity:
        peak = max(peak, v)
        mdd = min(mdd, v/peak - 1.0)
    return mdd
```

#### 8.3 重疊交易與權益曲線

- 同一檔在持有期內可能又出新訊號 → **預設不重複進場**（一檔同時只持一張）：`_signal_days` 之後做一次過濾，丟掉「進場日落在前一筆持有期內」的訊號（`next_entry_iloc <= prev_exit_iloc`）。這讓權益曲線可串接、MDD/CAGR 有意義，也貼近單檔單部位的實務。
- by_regime 的 MDD：對該 regime 子集合的交易**各自串權益曲線**算 MDD（不同 regime 的交易不混在一條曲線，因為中間有空檔）。

#### 8.4 樣本顯著性標註 `significance`

| 條件 | 標籤 | 用途 |
|---|---|---|
| `samples >= 30` | `OK` | 可信 |
| `10 <= samples < 30` | `WEAK` | 前端/報告標「樣本偏少，僅供參考」 |
| `samples < 10` | `INSUFFICIENT` | 不可作為買進依據（沿用 evaluate.py 既有 <8 警語精神，門檻提到 10/30） |

每個 regime 桶、IS/OOS 各自標。`_agg()` 一律附 `significance` 與 95% 勝率信賴區間（Wilson interval，給前端顯示誤差帶）：

```python
def wilson_ci(wins, n, z=1.96):
    if n == 0: return (None, None)
    p = wins/n; d = 1 + z*z/n
    c = p + z*z/(2*n); m = z*((p*(1-p)+z*z/(4*n))/n)**0.5
    return ((c-m)/d, (c+m)/d)
```

---

### 9. 向後相容：舊 `backtest()` 改成 wrapper

`backtest.py` 不刪，改成把扁平 `params` 轉成最小 `strategy_def` 後呼叫 `backtest_v2`，並退化成「無因子欄時用 `tech_score_at`」：

```python
# backtest.py（新）
from .backtest_v2 import backtest_v2
from .regime import get_regime_series_for
from .market import _fetch_taiex  # 內部用；失敗則全 range

def backtest(df, params=None):
    """相容層：保留舊 winrate 介面，內部走 v2（但用舊 tech_score 當單因子）。"""
    params = params or {}
    strat = _params_to_min_strategy_def(params)  # entry.min_score = min_tech_score_for_signal 等
    # 無大盤資料就全 range（單檔回測時 evaluate 沒帶 taiex）
    try:
        regime = get_regime_series_for(df, _fetch_taiex())
    except Exception:
        regime = pd.Series("range", index=df.index)
    # 若 df 無 factor__ 欄，v2 的 _signal_days 會走 tech_score fallback（見下）
    res = backtest_v2(strat, df, regime)
    o = res["overall"]
    return {"winrate": o["winrate"], "samples": o["samples"], "avg_return": o["avg_return"]}
```

`_weighted_factor_score` 增加 fallback：當 `factors` 為空（舊路徑），改呼叫 `tech_score_at(row, params)["score"]` 當分數。這樣**舊 strategies/*.json（扁平 params）零改動仍可跑**，只是內部結算換成更嚴謹（含成本、悲觀停損）的 v2，勝率數字會比舊版低一點——這是預期且正確的方向（舊版高估）。需在 PR 說明「勝率口徑變更」並列 `open_questions`：是否要保留一個 `legacy=True` 開關讓 evaluate 短期沿用舊樂觀算法以免分數斷層。

---

### 10. `evaluate.py` 遷移

現況：`evaluate.py` 第 9、46 行 `from .backtest import backtest` → `bt = backtest(px, params)`，只用 `bt["winrate"]`。

遷移做法（**最小破壞**）：

1. 第一階段（相容期）：**不動 `evaluate.py`**。因 §9 wrapper 保持同簽名同回傳鍵，`evaluate` 透明享受到「含成本的悲觀勝率」。
2. 第二階段（升級期，地基二/四完成後）：`evaluate` 改吃升級版 `strategy_def`，呼叫 `backtest_v2` 拿完整 dict，把 `by_regime`、`oos.degradation.verdict`、`overall.max_drawdown` 寫進 `components`，並用 `significance` 取代現有 `if bt.samples < 8` 的硬編碼警語：

```python
bt = backtest_v2(strategy_def, px_with_factors, regime_series)
o = bt["overall"]
winrate = o["winrate"] or 0.5
result["components"].update({
    "backtest_winrate": winrate,
    "backtest_samples": o["samples"],
    "backtest_significance": o["significance"],   # OK/WEAK/INSUFFICIENT
    "backtest_mdd": o["max_drawdown"],
    "backtest_sharpe": o["sharpe"],
    "by_regime": bt["by_regime"],
    "oos_verdict": bt["oos"]["degradation"]["verdict"],
})
if o["significance"] != "OK":
    result["risk_notes"].append(f"回測樣本 {o['samples']} 筆（{o['significance']}），統計強度不足")
if bt["oos"]["degradation"]["verdict"] == "OVERFIT":
    result["risk_notes"].append("樣本外退化明顯，疑似過擬合，謹慎看待")
# 用「當前大盤 regime」對應的勝率而非整體勝率餵分數（runtime regime 自適應）
cur_reg = regime_series.iloc[-1]
reg_stats = bt["by_regime"].get(cur_reg) or {}
if reg_stats.get("significance") == "OK" and reg_stats.get("winrate") is not None:
    winrate = reg_stats["winrate"]   # 用當前市況的歷史勝率，更貼近現在
```

> runtime regime 自適應的精神：當下大盤是 bear，就用 bear 桶的勝率/停損來評這檔，而不是用混合三市況的平均。這把架構決策第 3 點（市場 regime 自適應）真正落到分數上。

---

### 11. 避免偏誤的具體做法（鐵律）

#### 11.1 Look-ahead bias
- 因子欄 `factor__*` 由地基二保證「只用到 t 的資料」（截面截斷），引擎再加一層保護：訊號分數只讀 `price_df.iloc[i]`（t 當行），進場讀 `i+1`，結算讀 `i+2..`。
- regime 用 `ffill`（過去值）對齊，暖機期標 range。
- 指標暖機：`range(60, ...)` 跳過前 60 天（MA60/季線未成形）。
- 結算上限 `min(i+1+hold, n-1)`，未來資料不足（接近序列末端）時 `_signal_days` 已用 `n - max_hold - 1` 排除，**不會用半截未來**。

#### 11.2 Survivorship bias（回測樣本宇宙怎麼定）
- **問題**：若只用「今天還在交易的股票」回測，會系統性高估（下市的爛股被剔除）。
- **做法**：回測宇宙以「**回測起始日當時存在的上市櫃清單**」為準，而非今天的清單。具體：
  - 地基二建立 `universe/listing_history.json`（或抓 FinMind `TaiwanStockInfo` 含上市日，下市股需另補來源），記錄每檔的 `listed_date` / `delisted_date`。
  - `backtest_v2` 本身是單檔引擎，**survivorship 的責任在「挑哪些股票進回測」的上層（研發 workflow / batch runner）**：上層必須把「回測期間內曾下市的標的」也納入，下市日之後該檔的交易自然因無價格而終止。
  - 引擎側保障：`price_df` 若在某日後沒資料（下市），`_settle_one` 持有期撞到序列尾端 → 用最後可成交日收盤結算並標 `exit_reason="delisted_or_end"`（在 §7.4 expire 分支加判斷：若 `last < i+1+hold` 是因資料不足，標 delisted）。
  - `meta.universe_note` 記錄「本回測樣本宇宙的建構方式」，可審計。
- **新股不足 N 天**：序列長度 `< 60 + max_hold + 緩衝`（建議 `< 252`，至少一年）→ `backtest_v2` 回 `overall.samples` 偏少並標 `INSUFFICIENT`；上層應在 universe 篩掉上市未滿 1 年者，或明確標註。

#### 11.3 交易成本（§6 已實作）
- 每筆都扣，勝率/CAGR/Sharpe 全用 `net_return`。`gross_return` 同時保留供對照（看成本咬掉多少）。

#### 11.4 樣本顯著性（§8.4 已實作）
- `significance` + Wilson CI，IS/OOS/各 regime 桶都標。

---

### 12. 邊界與錯誤處理

| 情境 | 處理 |
|---|---|
| `price_df` 為空 / 長度 < 60+hold | 回 `{"overall":{"samples":0,"significance":"INSUFFICIENT", ...None}, "by_regime":{三桶皆 0}, "oos":None, "trades":[]}`，不丟例外 |
| 某 regime 桶 0 筆 | 該桶各指標回 None、`samples:0`、`significance:"INSUFFICIENT"` |
| `regime_series` 長度不符 | 引擎開頭 `assert len(regime_series)==len(price_df)`；wrapper 端對不上就重建全 range（防呆） |
| 進場日 open 缺/≤0/停牌 | 該筆作廢（`_settle_one` 回 None），不計入 |
| 持有期全停牌 | 跳過停牌日；若無任何可成交日 → 用進場後第一個可成交日收盤兜底，仍無 → 作廢 |
| 因子欄全缺 | `_weighted_factor_score` 回 None → 該日不產生訊號（不是回 0 硬買） |
| 大盤(TAIEX)抓不到 | `regime_classify` 上游失敗 → 全序列標 `range`（中性，等同關掉 regime 分桶，by_regime 全進 range） |
| FinMind 限流（抓資料層） | 不在本引擎；地基一 `fetch_finmind` 已有 retry+backoff。batch runner 對多檔回測要做**串行+sleep 或併發上限**，並 cache 價格 df（避免同檔重抓）。本章只要求引擎吃 df，不發 request |
| `years < 0.5` | CAGR 回 None（期間太短年化無意義），其餘指標照算 |
| profit_factor 分母為 0（無虧損筆） | 回 None（前端顯示「∞ / 無虧損樣本」） |

---

### 13. 升級版 `strategy_def` 中本章用到的欄位（與契約對齊）

`backtest_v2` 只依賴 `strategy_def` 的這些子欄（其餘欄位由別章用）：

```jsonc
{
  "factors": [ {"name": "ma_alignment", "weight": 0.3}, {"name": "kd_golden", "weight": 0.2} ],
  "entry":  { "min_score": 60 },
  "exit":   { "target_return": 0.10, "stop_loss": 0.08, "hold_days": 20, "trailing": 0.05 },
  "regime_overrides": {
    "bull":  { "min_score": 55, "target_return": 0.12 },
    "range": { "min_score": 65 },
    "bear":  { "stop_entry": true }      // 空頭不進場
  },
  "backtest": { "years": 4, "oos_split": 0.30 }
}
```

- `trailing` 缺省 = 無移動停利。
- `regime_overrides.<r>` 任一欄缺省 = 沿用 `exit`/`entry` 基準值。
- `backtest.years` 由上層決定抓幾年資料（3–5），引擎不抓資料但用它做 OOS 切分的合理性檢查（年數太短時 OOS 標 INSUFFICIENT）。

---

### 14. 關鍵單元測試點（合成資料）

放 `tests/`，用 `uv run pytest`。**全部用合成 df，不打 FinMind**。

**`test_regime.py`**
1. 造一段「線性上漲」收盤序列（每日 +0.3%）→ `regime_classify` 在暖機後應**全 bull**（站上季線、月線斜率正）。
2. 造「線性下跌」→ 暖機後**全 bear**。
3. 造「正弦/橫盤震盪」（均值回歸、無趨勢）→ 多數 `range`。
4. 暖機期（前 60 天）→ 全 `range`，且不得有 NaN。
5. 無 look-ahead：把序列尾端某天的值改掉，**不應影響該天之前任何一天的 regime 標籤**（逐日重算一致性測試）。

**`test_backtest_v2.py`**
6. **進場時序正確**：造 5 天序列，訊號在 i，驗證 `entry_date == df.iloc[i+1].date`、`entry == df.iloc[i+1].open`。
7. **停利觸發**：構造進場後第 3 天 high 剛好 ≥ target → `exit_reason=="target"`、exit==觸發價。
8. **停損觸發**：第 2 天 low ≤ stop → `exit_reason=="stop"`。
9. **同日停損優先**：某天 high≥target **且** low≤stop → 必須回 `stop`（驗證悲觀假設）。
10. **移動停利**：先漲 10%（peak 拉高）再回落超過 trail% → `exit_reason=="trailing"`，且只有獲利後才啟動（一進場就跌不觸發 trailing 而是 stop）。
11. **到期結算**：整段都沒碰 target/stop → `exit_reason=="expire"`、exit==最後可成交日 close。
12. **交易成本**：gross=+10% 的單筆，net 應 < gross 且差額 ≈ 來回摩擦（用 `apply_costs` 反推驗證，誤差 < 1e-6）。
13. **regime 分桶**：餵一段前半 bull 後半 bear 的 regime_series + 對應交易，驗證 `by_regime.bull.samples + by_regime.bear.samples == overall.samples`，且各桶交易確實落在對的市況。
14. **MDD 計算**：手造一條已知權益曲線（如 1→1.2→0.9→1.1），`max_drawdown` 必須 == -0.25（從 1.2 跌到 0.9）。**這是最容易寫錯的點，必測**。
15. **CAGR**：已知 equity_end 與跨年數，反推 CAGR 一致（誤差 < 1e-6）；years<0.5 回 None。
16. **OOS 切分**：30 筆交易按日期，前 21 進 IS、後 9 進 OOS（<20 → verdict INSUFFICIENT）；50 筆時切 35/15 且 verdict 走正常邏輯。
17. **不重疊進場**：構造連續訊號，驗證持有期內的後續訊號被過濾（單檔單部位）。
18. **停牌**：某日 `is_tradable=False`，驗證該日不被當結算日、不誤觸停利停損。
19. **空輸入 / 過短序列**：回 samples=0、significance=INSUFFICIENT，不丟例外。
20. **向後相容 wrapper**：無 `factor__` 欄的舊 df + 扁平 params → `backtest()` 回 `{winrate, samples, avg_return}` 三鍵齊全且型別正確。

**`test_stats.py`**
21. Wilson CI：wins=5,n=10 的區間落在已知值（對照公式手算）。
22. significance 門檻邊界：samples=9/10/29/30 各落對標籤。
23. Sortino ≥ 0 且當無虧損樣本時 downside std=0 → 回 None（不除以零）。

---

### 15. 與既有 code 對接點摘要

| 既有 | 對接方式 |
|---|---|
| `backtest.py::backtest()` | 改 wrapper，內部呼 `backtest_v2`，回傳鍵不變（向後相容） |
| `indicators.py::add_indicators/tech_score_at` | `add_indicators` 仍是 v2 的前置；`tech_score_at` 當「無因子欄時」的 fallback 分數 |
| `evaluate.py` | 第一階段不動；第二階段改吃 `strategy_def` + 寫入 by_regime/significance/oos（§10） |
| `market.py::_fetch_taiex()` | regime 取大盤資料沿用它，避免重寫抓 TAIEX 邏輯 |
| `data.py::get_price_history` | 上層 batch runner 用它抓多年價，餵進引擎前先 `add_indicators` + 地基二加因子欄 |
| `config.py` | 追加 `COSTS` 與 regime 參數常數（`ma_fast/ma_slow/...`），集中可調 |
| `performance.py` | runtime 成績單仍走 T+1/T+5/10/20 既有邏輯；可選把 `hit_target/hit_stop` 的判定改成共用 `_settle_one` 的「同日停損優先」口徑以一致（列 open_question） |
| `loader.py` | 升級 schema 的驗證在地基二/loader 改造；本章只消費 `strategy_def`，不負責存讀 |


---

## §9 地基四：策略 Schema 升級 + Regime 自適應

> 目標：把現行「扁平 20 格 params dict」升級成「分層 v2 schema（factors 加權 + entry/exit + regime_overrides + period + universe）」，並讓 `loader.py` **同時吃新舊兩種格式**，舊策略零改動仍能跑。本章只動 `stock_strategies/loader.py`（升級）、新增 `stock_strategies/schema.py`（schema 常數與型別）、新增 `strategies/v2/*.json`（範例），不碰 `evaluate.py` / `backtest.py` 的呼叫端 —— v2 透過 `merge_params()` 編譯回扁平 dict，對下游完全透明。

---

### 0. 設計原則（為什麼這樣切）

1. **下游無感**：`evaluate.py`、`backtest.py`、`market.py`、`main.py` 全部只認 `merge_params(strategy) -> 扁平 dict`。v2 的所有新欄位最終都要能 **編譯（compile）回現有扁平 keys**，這樣地基一～三（因子引擎 / 回測 v2 / regime）就能逐步接手，而不需要一次大改。
2. **版本顯式化**：頂層加 `version` 欄位。`version: 1`（或缺）= 舊扁平；`version: 2` = 分層。偵測規則見 §4。
3. **regime 是「覆寫層」不是「平行策略」**：base 欄位是底，`regime_overrides[regime]` 只放「跟 base 不同的差異」。runtime 拿到當日 regime（地基三 `regime_classify` 給的 `bull`/`range`/`bear`），把 override 蓋上 base，再編譯成扁平 dict。
4. **向後相容是硬約束**：`validate_strategy()` 對 v1 的輸出 byte-for-byte 不變（現有兩支 `default.json` / `conservative.json` 存檔後內容一致），API `StrategyIn` / 前端不需改。

---

### 1. v2 JSON Schema 完整定義

檔案存放：v1 續用 `strategies/<id>.json`；v2 建議放 `strategies/v2/<id>.json`（`STRATEGY_DIR` 仍掃 `*.json`，遞迴見 §6）。

#### 1.1 頂層欄位

| 欄位 | 型別 | 必填 | 值域 / 預設 | 說明 |
|---|---|---|---|---|
| `version` | int | ✅(v2) | `2` | 沒有此欄或 `1` → 走 v1 舊邏輯 |
| `id` | string | ✅ | slug，`^[a-z0-9_-]{3,40}$` | 缺則由 `name` slugify |
| `name` | string | ✅ | 1–60 字 | 顯示名稱 |
| `description` | string |  | ≤500 字 | 給人看 |
| `source` | enum |  | `default`\|`manual`\|`ai`，預設 `manual` | 前端徽章 |
| `school` | enum |  | 見 §1.2 七派，預設 `technical_reversal` | 流派標籤 |
| `period` | enum | ✅(v2) | `short`\|`swing`\|`long` | 決定 exit 預設，見 §3 |
| `created_at`/`updated_at` | ISO8601 |  | UTC | 時間戳 |
| `universe` | object |  | 見 §1.3 | 選股範圍 |
| `factors` | array |  | 見 §1.4 | 因子加權清單 |
| `entry` | object | ✅(v2) | 見 §1.5 | 進場門檻 |
| `exit` | object | ✅(v2) | 見 §1.6 | 出場規則 |
| `fundamental` | object |  | 見 §1.7 | 基本面門檻 |
| `regime_overrides` | object |  | 見 §2 | 三 regime 覆寫 |
| `backtest` | object |  | 見 §1.8 | 回測設定 |
| `legacy_signals` | object |  | 見 §1.9 | v1 四開關橋接（過渡期） |

#### 1.2 `school` enum（流派七派，對齊架構決策三軸）

```
"value"            價值       (低 PER/PB、高殖利率)
"growth"           成長       (EPS/營收年增、ROE)
"momentum"         動能       (相對強度、創高、均線多頭)
"chip"             籌碼       (三大法人連買、外資增持、融資券)
"revenue_momentum" 營收動能   (月營收 MoM/YoY 加速)
"technical_reversal" 技術反轉 (布林下軌、KD 超賣金叉、底背離)
"breakout"         突破       (帶量突破前高/箱型上緣)
```

`school` 不直接影響計算，只是標籤 + 給研發 workflow 分類；真正的差異落在 `factors`。

#### 1.3 `universe`（選股範圍，full）

| 欄位 | 型別 | 必填 | 預設 | 說明 |
|---|---|---|---|---|
| `industries` | string[] |  | `[]`=不限 | 產業白名單，比對 `FactorContext.industry`（FinMind `TaiwanStockInfo.industry_category`）|
| `exclude_industries` | string[] |  | `[]` | 產業黑名單（如金融/F股）|
| `market_cap_min` | number\|null |  | `null` | 最小市值（億台幣），缺資料時不過濾 |
| `price_min` | number\|null |  | `null` | 最低股價，濾雞蛋水餃股 |
| `min_listed_days` | int |  | `60` | 上市未滿 N 日剔除（防新股資料不足，見 §7）|

> universe 在 v2 是「軟過濾」：`main.py` 仍以 watchlist 為主清單，universe 只在某檔 **明確不符** 時把 action 設 `SKIP` 並記 `risk_notes`，不主動擴張清單（擴張屬地基外）。

#### 1.4 `factors`（因子加權，full）

對齊介面契約「因子 = 純函式 `compute(ctx, params) -> 0..1`」。每個元素：

| 欄位 | 型別 | 必填 | 值域 | 說明 |
|---|---|---|---|---|
| `name` | string | ✅ | 已註冊因子名（地基一 registry）| 如 `ma_alignment`、`inst_net_buy_streak` |
| `weight` | number | ✅ | `0..1` | 該因子在技術/因子總分的權重 |
| `params` | object |  | `{}` | 傳給該因子的參數（如 `{"window":20}`）|
| `enabled` | bool |  | `true` | false = 暫時關閉，不參與正規化 |

語意：`factor_score = Σ(weightᵢ × fᵢ) / Σ(weightᵢ)`（只計 `enabled && fᵢ≠None`），值域 `0..1`，編譯時 ×100 對齊現行 `tech_score`（0–100）。`fᵢ` 回 `None`（缺資料）時該因子退出當次正規化分母（不是當 0，避免懲罰缺資料）。若全部 `None` → `factor_score = 0.5`（中性）。

#### 1.5 `entry`（進場門檻，full）

| 欄位 | 型別 | 必填 | 值域 | 編譯到 v1 key | 說明 |
|---|---|---|---|---|---|
| `min_score` | int | ✅ | 0–100 | `min_total_score_for_buy` | 總分門檻 |
| `min_factor_score` | int |  | 0–100，預設 50 | `min_tech_score_for_buy` | 因子分門檻 |
| `require_fundamental_pass` | bool |  | 預設 `true` | `fundamental_pass_required` | 基本面必過才 BUY |
| `min_signal_score` | int |  | 0–100，預設 60 | `min_tech_score_for_signal` | 回測時算一次訊號的門檻 |
| `paused` | bool |  | 預設 `false` | （新）| true = 此策略/此 regime 暫停進場，BUY→SKIP |

#### 1.6 `exit`（出場規則，full）

| 欄位 | 型別 | 必填 | 值域 | 編譯到 v1 key | 說明 |
|---|---|---|---|---|---|
| `target_return` | number | ✅ | 0.01–0.5 | `target_return` | 停利 % |
| `stop_loss` | number | ✅ | 0.01–0.5 | `stop_loss` | 停損 % |
| `hold_days` | int | ✅ | 1–250 | `hold_days` | 最長持有 |
| `trailing` | object\|null |  | `{"activate":0.06,"giveback":0.03}` | （新，回測 v2 用）| 移動停利：獲利達 activate 後回吐 giveback 出場 |

#### 1.7 `fundamental`（基本面門檻，full）

| 欄位 | 型別 | 必填 | 編譯到 v1 key |
|---|---|---|---|
| `eps_threshold` | number | | `eps_threshold` |
| `roe_threshold` | number | | `roe_threshold` |

#### 1.8 `backtest`

| 欄位 | 型別 | 值域 | 編譯到 v1 key | 說明 |
|---|---|---|---|---|
| `years` | int | 3–5 | `backtest_years` | 回測窗（架構決策四：切多/盤/空需 3–5 年）|
| `oos_split` | number\|null | 0.6–0.9 | （回測 v2）| 樣本內/外切點（時間序，前段 in-sample）|

#### 1.9 `legacy_signals`（過渡期橋接，optional）

在地基一因子引擎完成前，v2 仍需驅動現行 `tech_score_at` 的四開關。此區塊一對一映射：

```json
"legacy_signals": {
  "use_ma_alignment": true,
  "use_bollinger_bounce": true,
  "use_kd_golden_cross": true,
  "use_macd_bullish": true,
  "use_volume_patterns": true
}
```

編譯規則（§5）：**若 v2 有非空 `factors` → 因子引擎接手，`legacy_signals` 被忽略；若 `factors` 為空 → fallback 用 `legacy_signals`（沒給就四開關全 true）**。這讓 v2 在因子引擎未上線前也能跑。

---

### 2. `regime_overrides` 語意（覆寫層）

```json
"regime_overrides": {
  "bull":  { ... 差異欄位 ... },
  "range": { ... },
  "bear":  { ... }
}
```

- key 固定三個：`bull` / `range` / `bear`（對齊地基三 `regime_classify` 回傳）。可只給其中一兩個，缺的 regime = 用 base 不覆寫。
- **可覆寫的白名單欄位**（其餘欄位禁止 override，validator 會剔除並警告）：

| 路徑 | 可 override | 典型用途 |
|---|---|---|
| `entry.min_score` | ✅ | 空頭拉高門檻 |
| `entry.min_factor_score` | ✅ | 同上 |
| `entry.require_fundamental_pass` | ✅ | 空頭強制基本面過 |
| `entry.paused` | ✅ | **空頭直接暫停進場**（最重要）|
| `exit.target_return` | ✅ | 盤整縮停利 |
| `exit.stop_loss` | ✅ | 空頭縮停損 |
| `exit.hold_days` | ✅ | 空頭縮短持有 |
| `factor_weight_multipliers` | ✅ | dict：`{"factor_name": 1.3}` 乘到對應因子 weight 後再正規化 |
| `factors[].weight` | ❌ | 用 `factor_weight_multipliers` 代替（避免整段 factors 重寫）|

#### 2.1 套用演算法（runtime）

```
effective = deep_copy(base)
ov = regime_overrides.get(regime, {})
for path in WHITELIST:
    if path in ov: set(effective, path, ov[path])
# 因子權重乘數
mult = ov.get("factor_weight_multipliers", {})
for f in effective.factors:
    f.weight *= mult.get(f.name, 1.0)
# paused 短路
if effective.entry.paused: -> 該策略當日所有候選 action 降為 SKIP（記 risk_note "regime=bear 暫停進場"）
return effective
```

`factor_weight_multipliers` 乘完後 weight 不重新 clamp 到 1（正規化會處理），但 `weight<0` 視為 0。

#### 2.2 regime 從哪來

runtime 由地基三提供：`regime_classify(taiex_df) -> Series`，`main.py` 取「當日標籤」。**回測時** 用 `regime_series` 對齊每一根 K 棒的 regime，逐日套不同 effective params（這部分交回測 v2，本章只保證 schema 能表達）。`market.py:get_market_state` 是現行二元（站上/跌破月線）的退化版，過渡期 `bullish=True→{bull or range}`、`False→bear`（見 §8 open question）。

---

### 3. `period` 三檔合理預設（exit 缺省值）

`validate_strategy` 對 v2 策略，若 `exit` 某欄缺漏，依 `period` 補：

| period | hold_days | target_return | stop_loss | trailing | backtest.years | 典型流派 |
|---|---|---|---|---|---|---|
| `short`（短線 5–10 日）| 7 | 0.08 | 0.05 | `{"activate":0.05,"giveback":0.025}` | 3 | 動能突破 / 技術反轉 |
| `swing`（波段 1–3 月）| 30 | 0.15 | 0.08 | `{"activate":0.10,"giveback":0.04}` | 4 | 籌碼 / 營收動能 |
| `long`（中長線 半年+）| 120 | 0.30 | 0.12 | `null` | 5 | 價值 / 成長 |

> 這些只是「缺省」，策略明確給值就用策略的。`PERIOD_DEFAULTS` 常數放 `schema.py`。

---

### 4. 新舊格式偵測

`detect_version(data: dict) -> int`：

```
1. data.get("version") == 2                      -> 2
2. data.get("version") in (1, None) 且有 "params" -> 1   # 舊扁平
3. 沒 "params" 但有 "factors"/"entry"/"exit"/"period" 任一 -> 2  # AI 可能漏給 version
4. 其他                                            -> 1   # 保守視為舊格式，走寬鬆驗證
```

規則 3 是防呆：AI 生成或手寫可能漏 `version`，但只要出現 v2 專屬結構就判 v2。

---

### 5. `loader.py` 升級方案

> **不破壞既有簽名**。`validate_strategy` / `merge_params` / `save_strategy` / `param_defaults` 對外簽名不變，只是內部分流。

#### 5.1 `validate_strategy(data) -> dict`

```
def validate_strategy(data):
    if not isinstance(data, dict): raise StrategyError(...)
    v = detect_version(data)
    if v == 1:
        return _validate_v1(data)     # ← 現有邏輯原封不動搬進來
    return _validate_v2(data)
```

- `_validate_v1`：**就是現在 lines 104–154 的程式**，一字不改，確保舊策略存檔結果 byte-identical。
- `_validate_v2`：驗證 §1 各欄位、clamp 值域、補 `period` 預設、剔除 `regime_overrides` 白名單外欄位（記到回傳的 `_warnings`）、保留 `version:2`、寫回 `updated_at`。回傳「乾淨 v2 dict」（仍是分層結構，原樣存檔；**不在存檔時壓扁**，壓扁只發生在 `merge_params`）。

`_validate_v2` 關鍵 clamp（重用 v1 的夾擠常數，集中到 `schema.py` 的 `CLAMPS`）：
```
exit.target_return  ∈ [0.01, 0.5]
exit.stop_loss      ∈ [0.01, 0.5]
exit.hold_days      ∈ [1, 250]
entry.min_score / min_factor_score / min_signal_score ∈ [0,100]
backtest.years      ∈ [3,5]    # 注意 v2 下限是 3（v1 是 1）
factors[].weight    ∈ [0,1]
```

#### 5.2 `merge_params(strategy, regime=None) -> dict`（**核心：v2→扁平編譯器**）

新增可選參數 `regime`（預設 `None` = base，向後相容，現有呼叫 `merge_params(strategy)` 不變）。

```
def merge_params(strategy, regime=None):
    if not strategy: return dict(_PARAM_DEFAULTS)
    v = detect_version(strategy)
    if v == 1:
        return _merge_v1(strategy)          # ← 現有 lines 84–101 邏輯
    return _compile_v2_to_flat(strategy, regime)
```

`_compile_v2_to_flat(strategy, regime)` 步驟：

```
1. eff = apply_regime_overrides(strategy, regime)     # §2.1
2. flat = dict(_PARAM_DEFAULTS)                        # 先全預設
3. # exit/entry/fundamental/backtest → 對應 v1 key（§1.5/1.6/1.7/1.8 的「編譯到 v1 key」欄）
   flat["target_return"]            = eff.exit.target_return
   flat["stop_loss"]                = eff.exit.stop_loss
   flat["hold_days"]                = eff.exit.hold_days
   flat["min_total_score_for_buy"]  = eff.entry.min_score
   flat["min_tech_score_for_buy"]   = eff.entry.min_factor_score
   flat["min_tech_score_for_signal"]= eff.entry.min_signal_score
   flat["fundamental_pass_required"]= eff.entry.require_fundamental_pass
   flat["eps_threshold"]            = eff.fundamental.eps_threshold (or 預設)
   flat["roe_threshold"]            = eff.fundamental.roe_threshold (or 預設)
   flat["backtest_years"]           = eff.backtest.years
4. # 因子/技術訊號：
   if eff.factors 非空:
       flat["_factors"] = normalize(eff.factors)   # 給地基一因子引擎；evaluate 之後讀
       # 過渡期：四開關全 True，讓現行 tech_score_at 仍可運作（或由因子引擎覆蓋）
   else:
       legacy = eff.legacy_signals or {四開關預設 True}
       flat.update(legacy)                          # use_ma_alignment 等
5. # paused 旗標傳下去
   flat["_paused"] = eff.entry.paused
   flat["_regime"] = regime
   flat["_trailing"] = eff.exit.trailing
   return flat
```

> 底線開頭的 key（`_factors`/`_paused`/`_trailing`/`_regime`）是「v2 擴充通道」，現行 `evaluate.py` 不認識會自動忽略（它只讀白名單 key）。地基一/二接手後再讀它們。`evaluate.py` 唯一要加的一行（屬地基一/二，本章只標註對接點）：在算完 action 後 `if params.get("_paused"): action = "SKIP"`。

#### 5.3 `param_defaults()`、`save_strategy()`、`list_strategies()`

- `param_defaults()`：不變（前端建 v1 仍可用）。可加 `schema_v2_defaults()` 新函式回 v2 骨架供前端建 v2（前端升級屬另一章）。
- `save_strategy()`：不變（內部呼叫 `validate_strategy` 已分流）。
- `list_strategies()`：不變，但建議回傳時帶上 `version`（v1 沒有就補 `1`）供前端區分渲染。

---

### 6. 目錄掃描與遞迴

現行 `STRATEGY_DIR.glob("*.json")` 不含子目錄。改為：
```
paths = list(STRATEGY_DIR.glob("*.json")) + list(STRATEGY_DIR.glob("v2/*.json"))
```
（或 `rglob("*.json")` 但排除 `SCHEMA.md` 等非策略；用顯式兩段較安全，避免誤掃。）id 唯一性：若 v1 與 v2 同 id，v2 優先（記 warning）。

---

### 7. 邊界與錯誤處理（具體做法）

| 情境 | 做法 |
|---|---|
| **新股上市不足 `min_listed_days`** | `evaluate` 已有 `len(px)<100 → SKIP`；v2 universe 再加：`len(px) < universe.min_listed_days → SKIP` 並記 `risk_note "上市未滿{N}日"`。避免回測 look-ahead（樣本太短）。|
| **停牌 / 缺 K 棒** | 不前向填補（forward-fill 會造 look-ahead）；缺資料的因子回 `None` 退出正規化分母。連續缺 >5 交易日 → universe `SKIP`。|
| **factor name 未註冊** | `_validate_v2` 警告但不 raise（記 `_warnings`），編譯時該因子被 skip（weight 不計）。全部未註冊 → fallback `legacy_signals`。|
| **regime_overrides 含白名單外欄位** | 剔除 + warning，不 raise。|
| **`factors` weight 全 0 或全 disabled** | normalize 後分母為 0 → factor_score=0.5（中性），記 warning。|
| **FinMind 限流（429/超時）** | 沿用 `data.fetch_finmind` 既有 retry+backoff；schema 層不重試。因子 ctx 取資料失敗 → 該因子 None。|
| **regime=None（取不到大盤）** | `merge_params(strategy, None)` = 用 base，等同無 regime 自適應，安全降級。|
| **AI 生成漏 `version`** | `detect_version` 規則 3 兜底判 v2。|
| **v2 缺 `exit`/`entry`/`period`** | 缺 `period` → 預設 `swing`；缺 `exit`/`entry` 整塊 → 用 `period` 預設 + `_PARAM_DEFAULTS` 補。**不 raise**（寬鬆，利於 AI 產出）。只有 `name` 空才 raise。|

**避免 look-ahead / survivorship 的硬規則**：
- 編譯出的 params 不含任何「未來資訊」；regime 套用必須用 **該 K 棒當日** 的 regime 標籤（回測 v2 用 `regime_series[t]`，不可用最終 regime）。
- universe 的市值/產業過濾用 **歷史快照**，不可用今天的成分判斷過去（survivorship）。FinMind `TaiwanStockInfo` 無歷史成分時，回測階段 universe 過濾「只在 runtime 選股套用，回測階段放寬」並在報告標註此限制（§8 open question）。

---

### 8. 三份完整 v2 策略範例（不同流派 + 週期，皆含 regime_overrides）

#### 8.1 籌碼波段（chip / swing）— `strategies/v2/chip_swing.json`

```json
{
  "version": 2,
  "id": "chip_swing",
  "name": "外資籌碼波段",
  "description": "三大法人連買 + 外資增持 + 帶量，波段持有 30 日。空頭暫停進場。",
  "source": "manual",
  "school": "chip",
  "period": "swing",
  "created_at": "2026-06-13T00:00:00Z",
  "updated_at": "2026-06-13T00:00:00Z",
  "universe": {
    "industries": [],
    "exclude_industries": ["金融保險"],
    "market_cap_min": 100,
    "price_min": 15,
    "min_listed_days": 120
  },
  "factors": [
    { "name": "inst_net_buy_streak", "weight": 0.35, "params": { "min_days": 3 } },
    { "name": "foreign_holding_increase", "weight": 0.30, "params": { "window": 20 } },
    { "name": "volume_breakout", "weight": 0.20, "params": { "ratio": 1.5 } },
    { "name": "ma_alignment", "weight": 0.15 }
  ],
  "fundamental": { "eps_threshold": 1.0, "roe_threshold": 8.0 },
  "entry": {
    "min_score": 65, "min_factor_score": 55,
    "require_fundamental_pass": false, "min_signal_score": 60, "paused": false
  },
  "exit": {
    "target_return": 0.15, "stop_loss": 0.08, "hold_days": 30,
    "trailing": { "activate": 0.10, "giveback": 0.04 }
  },
  "regime_overrides": {
    "bull":  { "entry": { "min_score": 60 } },
    "range": { "exit": { "target_return": 0.10 }, "factor_weight_multipliers": { "volume_breakout": 1.3 } },
    "bear":  { "entry": { "paused": true } }
  },
  "backtest": { "years": 4, "oos_split": 0.75 }
}
```

#### 8.2 價值中長線（value / long）— `strategies/v2/value_long.json`

```json
{
  "version": 2,
  "id": "value_long",
  "name": "低估值殖利率中長線",
  "description": "低 PER/PB、高殖利率、ROE 穩定，持有半年。空頭縮停損、提高基本面要求。",
  "source": "manual",
  "school": "value",
  "period": "long",
  "created_at": "2026-06-13T00:00:00Z",
  "updated_at": "2026-06-13T00:00:00Z",
  "universe": {
    "industries": [],
    "exclude_industries": ["生技醫療"],
    "market_cap_min": 200,
    "price_min": 10,
    "min_listed_days": 250
  },
  "factors": [
    { "name": "low_per", "weight": 0.30, "params": { "max_per": 15 } },
    { "name": "low_pb", "weight": 0.20, "params": { "max_pb": 2.0 } },
    { "name": "dividend_yield", "weight": 0.30, "params": { "min_yield": 0.04 } },
    { "name": "roe_stability", "weight": 0.20, "params": { "years": 3 } }
  ],
  "fundamental": { "eps_threshold": 3.0, "roe_threshold": 12.0 },
  "entry": {
    "min_score": 60, "min_factor_score": 45,
    "require_fundamental_pass": true, "min_signal_score": 50, "paused": false
  },
  "exit": {
    "target_return": 0.30, "stop_loss": 0.12, "hold_days": 120, "trailing": null
  },
  "regime_overrides": {
    "bull":  { "exit": { "target_return": 0.35 } },
    "range": {},
    "bear":  { "entry": { "min_score": 70, "require_fundamental_pass": true },
               "exit": { "stop_loss": 0.08 },
               "factor_weight_multipliers": { "dividend_yield": 1.4, "low_pb": 1.3 } }
  },
  "backtest": { "years": 5, "oos_split": 0.7 }
}
```

#### 8.3 動能突破短線（breakout / short）— `strategies/v2/momentum_breakout_short.json`

```json
{
  "version": 2,
  "id": "momentum_breakout_short",
  "name": "動能突破短打",
  "description": "帶量突破 60 日新高 + 相對強度 + KD 金叉，5–10 日短打。盤整減碼、空頭暫停。",
  "source": "manual",
  "school": "breakout",
  "period": "short",
  "created_at": "2026-06-13T00:00:00Z",
  "updated_at": "2026-06-13T00:00:00Z",
  "universe": {
    "industries": [],
    "exclude_industries": [],
    "market_cap_min": 50,
    "price_min": 20,
    "min_listed_days": 90
  },
  "factors": [
    { "name": "breakout_60d_high", "weight": 0.35, "params": { "lookback": 60 } },
    { "name": "relative_strength", "weight": 0.25, "params": { "window": 20, "vs": "TAIEX" } },
    { "name": "volume_breakout", "weight": 0.25, "params": { "ratio": 2.0 } },
    { "name": "kd_golden_cross", "weight": 0.15 }
  ],
  "fundamental": { "eps_threshold": 0.0, "roe_threshold": 0.0 },
  "entry": {
    "min_score": 62, "min_factor_score": 60,
    "require_fundamental_pass": false, "min_signal_score": 65, "paused": false
  },
  "exit": {
    "target_return": 0.08, "stop_loss": 0.05, "hold_days": 7,
    "trailing": { "activate": 0.05, "giveback": 0.025 }
  },
  "regime_overrides": {
    "bull":  { "entry": { "min_score": 58 },
               "factor_weight_multipliers": { "breakout_60d_high": 1.2 } },
    "range": { "entry": { "min_score": 70 }, "exit": { "target_return": 0.06, "hold_days": 5 } },
    "bear":  { "entry": { "paused": true } }
  },
  "backtest": { "years": 3, "oos_split": 0.8 }
}
```

---

### 9. Migration（v1 → v2，可選工具）

提供 `migrate_v1_to_v2(v1: dict) -> dict`（放 `loader.py`，CLI: `uv run python -m stock_strategies.loader migrate <id>`）。**非破壞**：產生新檔 `strategies/v2/<id>.json`，保留原 v1 檔。映射：

| v1 key | → v2 路徑 |
|---|---|
| `eps_threshold`/`roe_threshold` | `fundamental.*` |
| `backtest_years` | `backtest.years`（clamp 進 3–5）|
| `hold_days` | `exit.hold_days` |
| `target_return`/`stop_loss` | `exit.*` |
| `min_total_score_for_buy` | `entry.min_score` |
| `min_tech_score_for_buy` | `entry.min_factor_score` |
| `min_tech_score_for_signal` | `entry.min_signal_score` |
| `fundamental_pass_required` | `entry.require_fundamental_pass` |
| `use_*`（四開關 + volume）| `legacy_signals.*`（factors 留空 → 過渡期沿用四開關）|
| `market_filter_*` | 丟棄（regime 取代；記在 description）|
| `weight_fundamental/technical/backtest` | 丟棄（v2 改用 factor 加權 + 固定三段，記 warning）|

`period` 由 `hold_days` 推斷：`≤10→short`、`11–60→swing`、`>60→long`。`school` 預設 `technical_reversal`（人工再標）。`regime_overrides` 預設空 dict（migrate 不臆測，由研發層補）。

---

### 10. 關鍵單元測試點（`tests/test_schema_v2.py`）

**向後相容（最高優先）**
1. `validate_strategy(default.json內容)` 輸出與現行 byte-identical（snapshot test）。
2. `validate_strategy(conservative.json內容)` 同上。
3. `merge_params(v1_strategy)` 結果與升級前完全一致（用現有 `_PARAM_DEFAULTS` 比對）。
4. `merge_params(None)` 回純 `_PARAM_DEFAULTS`。

**版本偵測**
5. `detect_version({"version":2,...})==2`；`{"params":{...}}==1`；`{"factors":[],"period":"short"}==2`（漏 version 兜底）；`{}==1`。

**v2 編譯**
6. v2 base（regime=None）→ 扁平 dict，各 `entry/exit/fundamental` 正確映射到 v1 key。
7. `factors` 非空 → flat 含 `_factors` 且 normalize 後 weight 和=1（容差 1e-6）。
8. `factors` 為空 → fallback `legacy_signals`，四開關正確帶出。

**regime overrides**
9. `merge_params(chip_swing, "bear")` → `_paused==True`。
10. `merge_params(value_long, "bear")` → `stop_loss==0.08`、`min_score(min_total_score_for_buy)==70`、`require_fundamental_pass==True`。
11. `factor_weight_multipliers`：`value_long` bear 下 `dividend_yield` weight ×1.4 後正規化，其他因子相對下降。
12. regime_overrides 含白名單外欄位（如 `school`）→ 被剔除 + `_warnings` 非空，不 raise。
13. `merge_params(s, "nonexistent")` → 等同 base（無 override，不報錯）。

**clamp / 邊界**
14. v2 `backtest.years=2` → clamp 到 3；`=8` → clamp 到 5。
15. v2 `exit.target_return=0.9` → clamp 0.5；`stop_loss=0` → clamp 0.01。
16. v2 缺 `period` → 預設 `swing`；缺整塊 `exit` → 用 swing 預設。
17. factors weight 全 0 → factor_score 編譯路徑得中性、有 warning。

**migration**
18. `migrate_v1_to_v2(default.json)` 產出合法 v2（`validate_strategy` 不 raise），`hold_days=20→period=swing`。
19. migrate 後 `merge_params(migrated)` 的 exit/entry 與原 v1 數值一致（語意等價）。

**目錄掃描**
20. `list_strategies()` 同時列出 `strategies/*.json` 與 `strategies/v2/*.json`；同 id 時 v2 優先。

---

### 11. 與現有 code 的對接點總表

| 檔案 | 動作 | 內容 |
|---|---|---|
| `stock_strategies/schema.py` | **新增** | `PERIOD_DEFAULTS`、`CLAMPS`、`REGIME_OVERRIDE_WHITELIST`、`SCHOOLS`、`detect_version`、型別常數 |
| `stock_strategies/loader.py` | **改** | `validate_strategy` 分流；`_validate_v1`(搬現有)/`_validate_v2`；`merge_params(strategy, regime=None)`；`_compile_v2_to_flat`；`apply_regime_overrides`；`migrate_v1_to_v2`；目錄遞迴掃描 |
| `strategies/v2/*.json` | **新增** | 三份範例 |
| `strategies/SCHEMA.md` | **改** | 補 v2 章節 |
| `stock_strategies/evaluate.py` | **標註（地基一/二實作）** | 加一行 `if params.get("_paused"): action="SKIP"`；讀 `_factors` 走因子引擎 |
| `api/main.py` `StrategyIn` | **不改（建議擴）** | 目前只認 `params`；v2 可加 optional `version/factors/entry/exit/...` 透傳，但屬前端章 |
| `api/services/ai_generator.py` | **標註** | SYSTEM_PROMPT 改為產 v2（屬 AI 章），本章只保證 `validate_strategy` 吃得下 v2 |


---

## §10 研發層：多專家 Workflow 腳本

> 本章定義「研發層」唯一一支可執行腳本 `research/workflows/strategy_factory.workflow.js`，用 Claude Code Workflow tool 編排 6 類 agent，**跑一次、人工挑、產出 `strategies/*.json`（v2 schema）+ 研發報告 `research/reports/<run_id>.md`**。
>
> 鐵律（不可違反，審查官會檢查）：
> 1. **回測數字一律來自確定性 CLI**：回測工程師 agent 不准自己「估」勝率/夏普，只能用 `Bash` 呼叫 `uv run python -m stock_strategies.research.backtest_cli ...`，把回傳的 JSON 讀回原樣引用。任何 agent output 裡的回測數字都必須能在 `research/runs/<run_id>/backtests/<draft_id>.json` 找到對應原檔。
> 2. **所有專家判斷必須落地成「因子名 + 權重 + 門檻」**：流派分析師產出的是 v2 schema draft（引用因子庫的 `factors[].name`），不是散文。
> 3. **腳本是純 JS，meta 只准字面量**：不准 `Date.now()`、`Math.random()`、不准讀環境變數當參數、不准在 meta 內放運算式。`run_id` 由腳本頂部寫死的字面量常數提供（每次研發手動改）。

### 0. 本章在整體架構中的位置

```
┌─ 研發層（本章）─────────────────────────────────────────────┐
│  strategy_factory.workflow.js                                │
│    phase A: parallel(資料專家, regime專家)   ── 產出規格/規則 │
│    phase B: parallel(7 流派分析師)           ── 產出 v2 draft │
│    phase C: 對每個 draft  pipeline(回測→批判) ── 真實回測+質疑 │
│    phase D: 首席策略長                        ── 篩選+組裝     │
│         │                                                    │
│         ▼ 寫檔                                               │
│  strategies/*.json (v2)  +  research/reports/<run_id>.md     │
└────────────────────────────┬─────────────────────────────────┘
                             │（人工挑選）
                             ▼
┌─ 固化層（第 6 章 main.py pipeline）─ 讀同一批 *.json 每日跑 ─┐
└──────────────────────────────────────────────────────────────┘
```

研發層與固化層**共用同一個純 Python 地基**：因子庫（第 2 章 `stock_strategies/factors/`）、regime 判定（第 3 章 `stock_strategies/regime.py`）、回測引擎 `backtest_v2`（第 4 章 `stock_strategies/research/backtest_engine.py`）。本章只新增「Workflow 腳本 + 一支把這些地基包成 CLI 的薄殼 `backtest_cli.py`」，不重寫任何計算邏輯。

---

### 1. 新增檔案總覽

```
research/
  workflows/
    strategy_factory.workflow.js     ← 本章主角（Workflow tool 腳本）
  agents/                            ← 每個 agent 的 system prompt（純文字，腳本 import 進來）
    data_expert.prompt.md
    regime_expert.prompt.md
    school_analyst.prompt.md         ← 7 流派共用同一份，靠 input.school 區分
    backtest_engineer.prompt.md
    risk_critic.prompt.md
    chief_strategist.prompt.md
  schemas/
    v2_strategy.schema.json          ← v2 策略 draft 的 JSON Schema（agent 結構化輸出強制）
    backtest_result.schema.json      ← backtest_v2 回傳結構（與第 4 章對齊）
  runs/                              ← 每次研發的產物（gitignore，除了報告）
    <run_id>/
      drafts/<draft_id>.json
      backtests/<draft_id>.json      ← CLI 真實輸出（可信數字來源）
      critiques/<draft_id>.json
  reports/<run_id>.md                ← 研發報告（commit 進 repo）

stock_strategies/research/
  __init__.py
  backtest_cli.py                    ← 薄殼 CLI：吃 draft json → 呼叫 backtest_v2 → 印 JSON
  universe.py                        ← 研發股池快取（避免 survivorship bias，見 §7）
```

> `research/` 目錄要在 `.gitignore` 加 `research/runs/`（產物太大、含快取），但 `research/reports/` 與 `research/workflows/`、`research/agents/`、`research/schemas/` 要 commit。

---

### 2. Workflow 腳本骨架（完整可貼）

`research/workflows/strategy_factory.workflow.js`：

```javascript
// ============================================================================
// 台股策略工廠 · 研發層多專家 Workflow
// 用法：在 Claude Code 內執行此 workflow。跑一次、人工挑、產出 strategies/*.json
// 鐵律：回測數字一律來自 Bash 呼叫 uv run python -m stock_strategies.research.backtest_cli
//       agent 不准自己估數字。meta 純字面量，禁止 Date.now()/Math.random()。
// ============================================================================

// ── run_id：每次研發手動改這個字面量（禁止用 Date.now 生成）──────────────
const RUN_ID = "2026-06-13-a";              // ← 每次跑前手改
const RUN_DIR = `research/runs/${RUN_ID}`;
const REPORT_PATH = `research/reports/${RUN_ID}.md`;

// ── 研發期固定參數（字面量）──────────────────────────────────────────────
const BACKTEST_YEARS = 5;                    // 回測窗 3~5 年，取 5 才切得出多/盤/空
const RECENCY_YEARS = 2;                     // 近況判斷窗（不影響回測，傳給報告用）
const AS_OF_DATE = "2026-06-13";             // 研發基準日；回測一律截到此日，禁止未來資料
const UNIVERSE_TAG = "twse_listed_2021_q2";  // 凍結股池快照（防 survivorship，見 §7）

// ── 7 大流派（流派/因子軸）──────────────────────────────────────────────
const SCHOOLS = [
  "value",            // 價值
  "growth",           // 成長
  "momentum",         // 動能
  "chips",            // 籌碼（三大法人/融資券/外資持股）
  "revenue_momentum", // 營收動能（月營收 YoY/MoM）
  "tech_reversal",    // 技術反轉
  "breakout",         // 突破
];

// 每個流派要產幾份候選 draft（控制總量，避免一次太多）
const DRAFTS_PER_SCHOOL = 2;

// ── 淘汰準則（風控否決閥值，集中一處，報告也引用同一份）────────────────────
const REJECT = {
  oos_sharpe_min: 0.5,        // OOS 夏普 < 0.5 否決
  max_drawdown_max: 0.25,     // 最大回撤 > 25% 否決
  min_samples: 30,            // 總樣本 < 30 否決
  bear_avg_return_min: -0.02, // 空頭段平均報酬 < -2% 否決（不能空頭全虧）
  overall_winrate_min: 0.45,  // 整體勝率 < 45% 否決
  is_oos_sharpe_gap_max: 0.6, // IS 夏普 − OOS 夏普 > 0.6 視為過擬合否決
};

// ── 載入各 agent 的 system prompt（純文字檔）──────────────────────────────
const P_DATA     = read("research/agents/data_expert.prompt.md");
const P_REGIME   = read("research/agents/regime_expert.prompt.md");
const P_ANALYST  = read("research/agents/school_analyst.prompt.md");
const P_BACKTEST = read("research/agents/backtest_engineer.prompt.md");
const P_CRITIC   = read("research/agents/risk_critic.prompt.md");
const P_CHIEF    = read("research/agents/chief_strategist.prompt.md");

// 載入 schema（給 agent 結構化輸出 / 給 chief 驗證）
const SCHEMA_V2     = read("research/schemas/v2_strategy.schema.json");
const SCHEMA_BT     = read("research/schemas/backtest_result.schema.json");
const FACTOR_CATALOG = read("stock_strategies/factors/CATALOG.md"); // 第 2 章因子庫清單

// ============================================================================
// PHASE A：地基規格（資料專家 + regime 專家 並行）
// ============================================================================
const phaseA = phase("foundation", () => {
  return parallel({
    data_spec: agent({
      name: "data_expert",
      system: P_DATA,
      input: {
        as_of_date: AS_OF_DATE,
        universe_tag: UNIVERSE_TAG,
        backtest_years: BACKTEST_YEARS,
        existing_data_layer: "stock_strategies/data.py: fetch_finmind/get_price_history/get_fundamental",
        factor_catalog: FACTOR_CATALOG,
      },
      output_schema: "research/schemas/data_spec.schema.json",
      tools: ["Read"],   // 只准讀 repo，不准抓網路
    }),
    regime_spec: agent({
      name: "regime_expert",
      system: P_REGIME,
      input: {
        as_of_date: AS_OF_DATE,
        backtest_years: BACKTEST_YEARS,
        regime_module: "stock_strategies/regime.py: regime_classify(taiex_df)->Series",
      },
      output_schema: "research/schemas/regime_spec.schema.json",
      tools: ["Read", "Bash"], // 允許跑 regime_cli 看歷史分段統計
    }),
  });
});

// ============================================================================
// PHASE B：7 流派分析師 並行，各產出 DRAFTS_PER_SCHOOL 份 v2 draft
// ============================================================================
const phaseB = phase("draft_generation", (ctx) => {
  return parallel(
    SCHOOLS.map((school) =>
      agent({
        name: `analyst_${school}`,
        system: P_ANALYST,
        input: {
          school: school,
          drafts_to_produce: DRAFTS_PER_SCHOOL,
          as_of_date: AS_OF_DATE,
          backtest_years: BACKTEST_YEARS,
          data_spec: ctx.foundation.data_spec,
          regime_spec: ctx.foundation.regime_spec,
          factor_catalog: FACTOR_CATALOG,
          v2_schema: SCHEMA_V2,
          run_dir: RUN_DIR,
        },
        // 結構化輸出：一個 drafts 陣列，每筆是合法 v2 draft
        output_schema: "research/schemas/drafts_envelope.schema.json",
        // 分析師必須把每份 draft 寫成檔，後續 phase 靠檔名串接
        tools: ["Read", "Write"],
      })
    )
  );
});

// ============================================================================
// PHASE C：對每個 draft 跑 pipeline(回測 → 批判)
//   回測工程師：只准用 Bash 呼叫真實 CLI，把 JSON 讀回
//   風控批判：3 lens 並行質疑（過擬合 / 樣本穩健 / 市況依賴）
// ============================================================================
function evaluateDraft(draft) {
  return pipeline([
    // ── C1 回測（確定性 CLI，真實數字）──────────────────────────────────
    agent({
      name: `backtest_${draft.id}`,
      system: P_BACKTEST,
      input: {
        draft_id: draft.id,
        draft_path: `${RUN_DIR}/drafts/${draft.id}.json`,
        result_path: `${RUN_DIR}/backtests/${draft.id}.json`,
        as_of_date: AS_OF_DATE,
        backtest_years: BACKTEST_YEARS,
        universe_tag: UNIVERSE_TAG,
        cli_contract: [
          "uv run python -m stock_strategies.research.backtest_cli",
          "  --strategy <draft_path>",
          "  --as-of <as_of_date>",
          "  --years <backtest_years>",
          "  --universe <universe_tag>",
          "  --out <result_path>",
          "stdout 會印出與 backtest_result.schema.json 相符的 JSON；",
          "agent 必須原樣讀回，禁止竄改/估算任何數字。",
        ].join("\n"),
        bt_schema: SCHEMA_BT,
      },
      output_schema: "research/schemas/backtest_agent_out.schema.json",
      tools: ["Bash", "Read"], // 一定要有 Bash 才能跑回測
    }),
    // ── C2 對抗式風控批判（3 lens 並行）──────────────────────────────────
    (btOut) =>
      agent({
        name: `critic_${draft.id}`,
        system: P_CRITIC,
        input: {
          draft: draft,
          backtest: btOut.result, // 來自 C1 的真實 JSON
          reject_rules: REJECT,
          lenses: ["overfitting", "sample_robustness", "regime_dependence"],
          critique_path: `${RUN_DIR}/critiques/${draft.id}.json`,
        },
        output_schema: "research/schemas/critique.schema.json",
        tools: ["Read", "Write"],
      }),
  ]);
}

const phaseC = phase("backtest_and_critique", (ctx) => {
  // 把 7 個分析師吐出的所有 draft 攤平
  const allDrafts = SCHOOLS.flatMap(
    (s) => ctx.draft_generation[`analyst_${s}`].drafts
  );
  // 每個 draft 各自跑一條 回測→批判 pipeline，彼此獨立 → parallel
  return parallel(allDrafts.map((d) => evaluateDraft(d)));
});

// ============================================================================
// PHASE D：首席策略長彙整（篩選 + 組裝 strategies/*.json + 寫報告）
// ============================================================================
const phaseD = phase("chief_assembly", (ctx) => {
  return agent({
    name: "chief_strategist",
    system: P_CHIEF,
    input: {
      run_id: RUN_ID,
      as_of_date: AS_OF_DATE,
      backtest_years: BACKTEST_YEARS,
      recency_years: RECENCY_YEARS,
      reject_rules: REJECT,
      evaluations: ctx.backtest_and_critique, // [{draft, backtest, critique}, ...]
      v2_schema: SCHEMA_V2,
      strategies_dir: "strategies",
      report_path: REPORT_PATH,
      loader_contract: "通過者寫成 strategies/<id>.json，需能被 stock_strategies/loader.py 讀（v2 向後相容，見第 5 章）",
    },
    output_schema: "research/schemas/chief_summary.schema.json",
    tools: ["Read", "Write"], // 寫 strategies/*.json + 報告
  });
});

// ============================================================================
// 主管線
// ============================================================================
pipeline([phaseA, phaseB, phaseC, phaseD], {
  meta: {
    run_id: RUN_ID,
    purpose: "台股多專家策略研發一輪",
    deterministic_backtest: true,
    artifacts: [RUN_DIR, REPORT_PATH, "strategies/*.json"],
  },
});
```

> **語法註記**：`agent()` / `parallel()` / `pipeline()` / `phase()` 是 Workflow tool 內建。`read()` 在 meta 階段讀檔字面量（提示詞、schema）允許，因為它回傳的是純字串常數，不是運算式副作用。`ctx.<phaseName>.<agentName>` 是上一階段結果的存取慣例；`output_schema` 指向一份 JSON Schema，Workflow tool 會強制該 agent 結構化輸出符合 schema。

---

### 3. 各 agent 的 system prompt 設計 + output schema

下面每個小節給：**職責 / 輸入 / 產出要求**（即 prompt 主體）與 **output schema 欄位定義**。prompt 全文存在 `research/agents/*.prompt.md`，這裡給可直接落地的內容。

#### 3.1 資料專家 `data_expert`

**職責**：產出「FactorContext 抓取與組裝規格」，把第 2 章因子庫所需的每一種原始資料對應到 FinMind dataset、欄位 rename、時間對齊規則，並標出每種資料的缺漏退路。**不抓網路、不算因子**，只產規格文件供回測工程師與分析師對齊。

**prompt 主體（節錄，存檔用全文）**：

```
你是資料工程專家。你的唯一產出是一份「FactorContext 資料規格」JSON，描述如何在不引入未來資訊的前提下，為任一檔股票在任一基準日 t 組出 FactorContext。

已有地基（必須沿用，不可另起爐灶）：
- stock_strategies/data.py 的 fetch_finmind(dataset, stock_id, start_date, timeout=30, max_retries=2)
- get_price_history(stock_id, years) → 欄位 open/high/low/close/volume（已 rename max→high/min→low/Trading_Volume→volume）
- get_fundamental(stock_id) → {"eps":{year:val}, "roe":{year:val}}（近 3 完整年度）

你要為 FactorContext 的每個欄位（price_df / inst / revenue / valuation / margin / shareholding / industry / market_regime / fundamentals）指定：
1. 來源 FinMind dataset 名稱（例：三大法人=TaiwanStockInstitutionalInvestorsBuySell；月營收=TaiwanStockMonthRevenue；估值=TaiwanStockPER；融資券=TaiwanStockMarginPurchaseShortSale；外資持股=TaiwanStockShareholding）
2. 需要的欄位與 rename
3. 「point-in-time 對齊規則」：每筆資料在哪一天才算「市場已知」（關鍵防 look-ahead）。例如月營收 10 號才公布上月，valuation 用收盤後資料 → t 當天可用，財報用公告日非結算日。
4. 缺漏退路：缺資料時該欄位回 None，因子端要能吃 None 回中性 0.5。
5. 新股不足 N 天的處理：price_df 長度 < 須求最小值時整檔標 insufficient_history=true。

只輸出 JSON，不要散文。
```

**output schema（`data_spec.schema.json` 關鍵欄位）**：

| 欄位 | 型別 | 說明 / 範例 |
|---|---|---|
| `fields[]` | array | 每個 FactorContext 欄位一筆 |
| `fields[].name` | string | `"revenue"` |
| `fields[].dataset` | string | `"TaiwanStockMonthRevenue"` |
| `fields[].rename` | object | `{"revenue":"rev"}` |
| `fields[].point_in_time_rule` | string | `"當月營收於次月10號公布；t 日僅可見 publish_date<=t 的資料"` |
| `fields[].publish_lag_days` | int | `10`（用於回測對齊；無延遲填 0） |
| `fields[].on_missing` | enum | `"neutral_0.5"` \| `"none"` \| `"skip_stock"` |
| `fields[].min_history_days` | int | `60` |
| `global.min_listed_days` | int | `120`（新股門檻，見 §7） |
| `global.as_of_date` | string | `"2026-06-13"` |

#### 3.2 市場 Regime 專家 `regime_expert`

**職責**：產出 `regime_classify(taiex_df) -> Series of {bull/range/bear}` 的**可在歷史任一日計算**的規則（純規則、無未來資訊），並用 Bash 跑一次 regime CLI 確認三段在回測窗內的占比合理（不能某段為 0）。

**prompt 主體（節錄）**：

```
你是市場狀態判定專家。產出一份「regime 判定規則」JSON + 一段驗證結論。

規則必須只用截至當日的資料（月線斜率、季線位置、近 60 日漲跌家數比、或波動率），不可前視。建議三分法：
- bull：收盤 > 季線(60MA) 且 月線(20MA) 斜率(近20日)>0
- bear：收盤 < 季線 且 月線斜率<0
- range：其餘
門檻寫成可調參數（slope_window、ma_short、ma_long、vol_window）。

接著用 Bash 執行：
  uv run python -m stock_strategies.research.regime_cli --as-of {as_of_date} --years {backtest_years}
讀回它印出的 regime 占比與分段切點，確認 bull/range/bear 三段在窗內占比都 >= 10%（否則調門檻重試）。
只把「最終規則 + CLI 實測占比」放進輸出，數字一律來自 CLI，不可自己猜。
```

**output schema（`regime_spec.schema.json`）**：

| 欄位 | 型別 | 範例 |
|---|---|---|
| `rule.method` | string | `"ma_slope_quarterline"` |
| `rule.params` | object | `{"ma_short":20,"ma_long":60,"slope_window":20,"vol_window":20}` |
| `rule.definition` | object | `{"bull":"close>ma60 and slope20(ma20)>0", "bear":"...", "range":"else"}` |
| `validation.source` | string | `"regime_cli"`（強制標明數字來源） |
| `validation.regime_share` | object | `{"bull":0.41,"range":0.37,"bear":0.22}`（CLI 實測） |
| `validation.segments[]` | array | `[{"regime":"bear","start":"2022-01-05","end":"2022-10-25"}]` |

#### 3.3 流派分析師 `school_analyst`（7 份並行，共用 prompt，靠 `input.school` 分流）

**職責**：給定一個流派與因子庫，產出 `DRAFTS_PER_SCHOOL` 份**可量化 v2 策略 draft**（引用因子庫的因子名 + 權重 + 門檻 + 三個 regime override），並**把每份寫成 `research/runs/<run_id>/drafts/<draft_id>.json`**。分析師只設計、不回測（回測在 phase C）。

**prompt 主體（節錄）**：

```
你是「{school}」流派的策略設計師。產出 {drafts_to_produce} 份互不重複的 v2 策略 draft，每份是一個自洽的、可被確定性回測引擎執行的策略。

可用因子只能從 factor_catalog 挑（用 name 引用），禁止發明不存在的因子。每份 draft 必須：
1. school 固定為 {school}；period 從 short(5-10日)/swing(1-3月)/long(半年+) 三選一，並讓兩份 draft 至少在 period 或因子組合上有差異（多樣性）。
2. factors[] 至少 3 個、至多 6 個，weight 加總=1.0（小數）。因子要與流派一致（如 value 用 per_percentile/pb_percentile/dividend_yield；momentum 用 ret_60d/ma_alignment/rs_rank；chips 用 foreign_net_5d/trust_net_5d；revenue_momentum 用 rev_yoy/rev_mom_3m）。
3. entry/exit/regime_overrides 填好（schema 見 v2_schema）。exit 的 hold_days 要與 period 對齊（short~5-10、swing~20-60、long~120+）。
4. regime_overrides 必填 bull/range/bear 三段；至少要對 bear 做一件防禦事（拉高 min_score、縮 stop_loss、或 stop_entry=true）。
5. universe 可選用 industries 或 market_cap_min 聚焦該流派擅長的標的。

draft.id 命名：{school}-{period}-{兩位序號}，例如 value-swing-01。把每份 draft 用 Write 寫到 {run_dir}/drafts/<id>.json，並在最終結構化輸出的 drafts[] 回傳同樣內容。
為每份 draft 寫 50 字內 rationale：為什麼這組因子在這個流派/週期有 edge。
禁止寫任何回測數字（你還沒回測）。
```

**output schema（`drafts_envelope.schema.json`）**：頂層 `{ "school": string, "drafts": [ <v2 draft>, ... ] }`，每個 draft 必須通過 `v2_strategy.schema.json`（見 §4）。

#### 3.4 回測工程師 `backtest_engineer`（**真實數字的把關點**）

**職責**：對單一 draft，用 **Bash 呼叫確定性回測 CLI**，把回傳 JSON 原樣讀回並轉成結構化輸出。**絕對不准自行估算、修飾、或在 LLM 內「心算」任何數字**。

**prompt 主體（全文，重要）**：

```
你是回測執行工程師。你不評價策略好壞，你只負責「正確地跑回測並如實回報數字」。

步驟（嚴格照做）：
1. 確認 draft 檔存在：Read {draft_path}。
2. 用 Bash 執行確定性回測（這是唯一合法的數字來源）：
   uv run python -m stock_strategies.research.backtest_cli \
     --strategy {draft_path} \
     --as-of {as_of_date} \
     --years {backtest_years} \
     --universe {universe_tag} \
     --out {result_path}
3. 若 exit code != 0 或 stdout 不是合法 JSON：把 status 設為 "error"，error 欄填 stderr 末 300 字，result 設為 null。不要重試超過 1 次，不要捏造數字。
4. 成功時：Read {result_path}，把整份 JSON 放進 output 的 result 欄，原封不動。
5. 在 output 的 verification 欄填 {"source":"backtest_cli","result_path":"{result_path}","cmd":"<你實際跑的指令>"}，讓審查官可追溯。

鐵律：output.result 內每一個數字都必須等於 {result_path} 檔案內的值。你沒有權限改任何一位數。若你想加註解，只能放在 notes 欄，且不得與 result 矛盾。
```

**output schema（`backtest_agent_out.schema.json`）**：

| 欄位 | 型別 | 說明 |
|---|---|---|
| `draft_id` | string | |
| `status` | enum | `"ok"` \| `"error"` |
| `result` | object\|null | 完整 `backtest_result`（見 §4 與第 4 章 schema），錯誤時 null |
| `verification.source` | const | 固定 `"backtest_cli"` |
| `verification.result_path` | string | `research/runs/.../backtests/<id>.json` |
| `verification.cmd` | string | 實際執行的 uv 指令字串 |
| `error` | string\|null | stderr 摘要 |
| `notes` | string | 可選，不得與 result 矛盾 |

#### 3.5 對抗式風控批判 `risk_critic`（3 lens 並行質疑）

**職責**：拿到 draft + **真實回測 result**，用三個 lens 對抗式找碴，逐條對照 `REJECT` 閥值，輸出 verdict（pass / reject）與否決理由。**只能引用回測工程師回報的真實數字**，不得重算。

**prompt 主體（節錄）**：

```
你是對抗式風控官，預設立場是「這策略大概有問題，請你說服我它沒問題」。
你拿到 draft 與一份確定性回測 result（數字可信，直接引用，不要重算）。

用三個 lens 並行質疑，每個 lens 各給 0-100 的風險分（越高越危險）與具體證據：

[lens 1 過擬合 overfitting]
- 看 is（樣本內）與 oos（樣本外）落差：若 oos.sharpe < {reject_rules.oos_sharpe_min} → 直接 reject。
- 若 is.sharpe - oos.sharpe > {reject_rules.is_oos_sharpe_gap_max} → 過擬合 reject。
- 因子數 vs 樣本數：factors 太多但 samples 偏少 → 警示。

[lens 2 樣本穩健 sample_robustness]
- overall.samples < {reject_rules.min_samples} → reject（統計不可信）。
- 報酬是否被少數極端交易撐起：看 avg_return 與 winrate 是否背離（高 avg 但低 winrate 代表靠少數大贏，脆弱）→ 警示。
- max_drawdown > {reject_rules.max_drawdown_max} → reject。

[lens 3 市況依賴 regime_dependence]
- by_regime.bear.avg_return < {reject_rules.bear_avg_return_min} → reject（空頭全虧）。
- 報酬是否只來自單一 regime（bull 大賺、range/bear 皆負）→ 警示，要求 regime_overrides 是否真能防禦。
- overall.winrate < {reject_rules.overall_winrate_min} → reject。

最後彙整：任一 lens 觸發 reject 條件 → verdict=reject，並列出 triggered_rules（規則名）。否則 verdict=pass。
務必把每個 reject 的數字證據寫進 evidence，方便首席與報告引用。把整份寫到 {critique_path}。
```

**output schema（`critique.schema.json`）**：

| 欄位 | 型別 | 範例 |
|---|---|---|
| `draft_id` | string | `"value-swing-01"` |
| `verdict` | enum | `"pass"` \| `"reject"` |
| `triggered_rules` | array | `["oos_sharpe_min","max_drawdown_max"]` |
| `lenses[].name` | enum | `overfitting` \| `sample_robustness` \| `regime_dependence` |
| `lenses[].risk_score` | int(0-100) | `78` |
| `lenses[].evidence[]` | array | `["oos.sharpe=0.31 < 0.5","is.sharpe=1.9 gap=1.59"]` |
| `summary` | string | 一句話結論 |

#### 3.6 首席策略長 `chief_strategist`

**職責**：彙整所有 `{draft, backtest, critique}`，**剔除 critique.verdict=reject 的**，把通過者組裝成最終 `strategies/<id>.json`（v2，向後相容 loader），寫研發報告 `research/reports/<run_id>.md`，並把跨流派的多樣性、各 regime 表現排名整理成表。

**prompt 主體（節錄）**：

```
你是首席策略長。輸入是本輪所有 draft 的 {draft, backtest(真實), critique} 三元組。

1. 篩選：critique.verdict=="reject" 一律淘汰。pass 者進候選池。
2. 二次把關（你自己再檢一遍，防 critic 漏判）：對候選池逐筆對照 reject_rules，發現 critic 漏掉的觸發條件就補淘汰，並在報告標註 "chief_override"。
3. 去重/限額：同流派最多保留 2 份；若兩份因子重疊度過高（factors name 交集>=80%）只留 oos.sharpe 高者。
4. 組裝：把通過者寫成 strategies/<id>.json，欄位用 v2_schema。為向後相容，必須同時帶一個 params 扁平區塊（把 v2 的 entry/exit 映射回 loader.py 認得的鍵：hold_days/target_return/stop_loss/min_total_score_for_buy 等），讓固化層舊 loader 也能讀（見第 5 章相容映射表）。source 填 "research"。
5. 報告 research/reports/<run_id>.md：列出本輪 run_id、as_of_date、回測窗、universe_tag、reject_rules 全文；一張「全 draft 結果總表」（school/period/oos_sharpe/max_dd/by_regime 報酬/verdict/採用否）；一張「採用清單」；每個被否決者一句否決理由（引用 critique.triggered_rules）。報告所有數字必須能對應到 research/runs/<run_id>/backtests/*.json。

只採用、不發明數字。
```

**output schema（`chief_summary.schema.json`）**：

| 欄位 | 型別 | 說明 |
|---|---|---|
| `run_id` | string | |
| `accepted[]` | array | `[{ "id":"value-swing-01","path":"strategies/value-swing-01.json","oos_sharpe":0.9 }]` |
| `rejected[]` | array | `[{ "id":"...","reason":"oos_sharpe_min, max_drawdown_max" }]` |
| `report_path` | string | `research/reports/2026-06-13-a.md` |
| `diversity` | object | `{"schools_covered":6,"periods":{"short":2,"swing":3,"long":1}}` |

---

### 4. v2 策略 draft schema 與回測結果 schema（本章固定）

`research/schemas/v2_strategy.schema.json`（draft 與最終策略共用；與介面契約草案一致，並補 `params` 相容區塊）：

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "required": ["id","name","school","period","factors","entry","exit","regime_overrides","backtest"],
  "properties": {
    "id":   { "type": "string", "pattern": "^[a-z0-9_-]+$" },
    "name": { "type": "string" },
    "description": { "type": "string" },
    "source": { "type": "string", "enum": ["default","manual","ai","research"] },
    "school": { "type": "string",
      "enum": ["value","growth","momentum","chips","revenue_momentum","tech_reversal","breakout"] },
    "period": { "type": "string", "enum": ["short","swing","long"] },
    "universe": {
      "type": "object",
      "properties": {
        "industries": { "type": "array", "items": { "type": "string" } },
        "market_cap_min": { "type": ["number","null"] }
      }
    },
    "factors": {
      "type": "array", "minItems": 3, "maxItems": 6,
      "items": {
        "type": "object", "required": ["name","weight"],
        "properties": {
          "name":   { "type": "string" },
          "weight": { "type": "number", "minimum": 0, "maximum": 1 }
        }
      }
    },
    "entry": {
      "type": "object", "required": ["min_score"],
      "properties": {
        "min_score": { "type": "number", "minimum": 0, "maximum": 1 },
        "require_fundamental_pass": { "type": "boolean" }
      }
    },
    "exit": {
      "type": "object", "required": ["target_return","stop_loss","hold_days"],
      "properties": {
        "target_return": { "type": "number" },
        "stop_loss":     { "type": "number" },
        "hold_days":     { "type": "integer" },
        "trailing":      { "type": ["number","null"] }
      }
    },
    "regime_overrides": {
      "type": "object", "required": ["bull","range","bear"],
      "properties": {
        "bull":  { "$ref": "#/definitions/override" },
        "range": { "$ref": "#/definitions/override" },
        "bear":  { "$ref": "#/definitions/override" }
      }
    },
    "backtest": {
      "type": "object", "required": ["years"],
      "properties": {
        "years":     { "type": "integer", "minimum": 3, "maximum": 5 },
        "oos_split": { "type": ["number","null"] }
      }
    },
    "params": { "type": "object", "description": "向後相容 loader.py 的扁平鍵（chief 組裝時填）" }
  },
  "definitions": {
    "override": {
      "type": "object",
      "properties": {
        "min_score":     { "type": ["number","null"] },
        "stop_loss":     { "type": ["number","null"] },
        "stop_entry":    { "type": ["boolean","null"] },
        "weight_scale":  { "type": ["object","null"] }
      }
    }
  }
}
```

> **factors weight 校驗**：JSON Schema 無法表達「加總=1」，因此 `backtest_cli` 載入時會把 weight 正規化（除以總和），並在 stdout 的 `meta.weight_normalized=true` 標記；chief 與 critic 看到此旗標就知道權重已被引擎正規化。

`research/schemas/backtest_result.schema.json`（與第 4 章 `backtest_v2` 回傳對齊；**本章只消費，定義權在第 4 章，這裡列出本章依賴的最小欄位集**）：

```json
{
  "type": "object",
  "required": ["overall","by_regime","oos","is","meta"],
  "properties": {
    "overall": { "type": "object", "required": ["winrate","avg_return","cagr","max_drawdown","sharpe","samples"] },
    "by_regime": {
      "type": "object", "required": ["bull","range","bear"],
      "additionalProperties": { "type": "object",
        "required": ["winrate","avg_return","sharpe","samples","max_drawdown"] }
    },
    "is":  { "type": "object", "required": ["sharpe","samples"] },
    "oos": { "type": "object", "required": ["winrate","avg_return","sharpe","max_drawdown","samples"] },
    "hold_period_variants": { "type": ["object","null"] },
    "meta": { "type": "object", "required": ["strategy_id","as_of_date","years","universe_tag","weight_normalized"] }
  }
}
```

---

### 5. 回測工程師如何透過 Bash 呼叫確定性 CLI（核心可信機制）

這是本章最關鍵的落地點：**LLM 不算數字，數字全部由 repo 內的 Python CLI 產生。**

#### 5.1 `backtest_cli` 介面合約

```
uv run python -m stock_strategies.research.backtest_cli \
  --strategy research/runs/<run_id>/drafts/<draft_id>.json \
  --as-of   2026-06-13 \
  --years   5 \
  --universe twse_listed_2021_q2 \
  --out     research/runs/<run_id>/backtests/<draft_id>.json \
  [--max-stocks 200]   # 研發期可限縮股池跑快一點，預設用整個 universe
```

行為：
1. 讀 draft JSON，正規化 factor weight。
2. 透過 `universe.py` 取得 `universe_tag` 對應的**凍結股池清單**（§7，防 survivorship）。
3. 對股池每檔：用 `data.py` 既有的 `fetch_finmind`/`get_price_history`/`get_fundamental` 抓資料（沿用既有 retry/backoff/timeout），用第 2 章因子庫把每檔每日算成因子分，組成 `price_with_factors_df`；用第 3 章 `regime_classify` 算 `regime_series`。
4. 呼叫第 4 章 `backtest_v2(strategy_def, price_with_factors_df, regime_series)`，**逐檔回測後彙整成投組層級**的 `overall/by_regime/is/oos`。
5. 把結果 `json.dump` 到 `--out`，**同時** `print` 到 stdout（agent 兩條路都能拿到，互為備援）。
6. exit code：成功 0、資料不足/股池空 2、內部例外 1（stderr 印 traceback 末段）。

`backtest_cli.py` 骨架（薄殼，計算全在地基層）：

```python
# stock_strategies/research/backtest_cli.py
import argparse, json, sys, traceback
from pathlib import Path

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", required=True)
    ap.add_argument("--as-of", required=True)        # YYYY-MM-DD，回測截止日，禁未來
    ap.add_argument("--years", type=int, default=5)
    ap.add_argument("--universe", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-stocks", type=int, default=0)  # 0=全用
    args = ap.parse_args()

    try:
        from stock_strategies.research.backtest_engine import backtest_v2  # 第 4 章
        from stock_strategies.research.universe import load_universe       # §7
        from stock_strategies.research.factor_pipeline import build_panel  # 第 2 章組裝
        from stock_strategies.regime import regime_classify               # 第 3 章

        strat = json.loads(Path(args.strategy).read_text(encoding="utf-8"))
        stocks = load_universe(args.universe, as_of=args.as_of)
        if args.max_stocks:
            stocks = stocks[: args.max_stocks]
        if not stocks:
            print(json.dumps({"error": "empty_universe"}), file=sys.stderr)
            return 2

        # 逐檔算因子 panel（截到 as_of，無未來資訊）
        panel = build_panel(stocks, strat, as_of=args.as_of, years=args.years)
        regime = regime_classify(panel.taiex_df)   # 與股票同一時間軸

        result = backtest_v2(strat, panel.price_with_factors, regime)
        result.setdefault("meta", {}).update({
            "strategy_id": strat["id"], "as_of_date": args.as_of,
            "years": args.years, "universe_tag": args.universe,
            "weight_normalized": True,
        })
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False))   # 供 agent 直接讀 stdout
        return 0
    except Exception as e:
        print("".join(traceback.format_exc())[-600:], file=sys.stderr)
        print(json.dumps({"error": str(e)[:200]}), file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
```

#### 5.2 可信度稽核（審查官 checklist）

研發跑完後，可用一行 script 驗證「報告數字 = CLI 真檔數字」，作為審查官自動稽核：

```
# 對 chief_summary.accepted 的每個 id，比對報告引用的 oos_sharpe 是否等於 backtests/<id>.json 的 oos.sharpe
uv run python -m stock_strategies.research.audit_run --run <run_id>
```

`audit_run` 失敗（任何 agent 引用的數字對不上真檔）→ 整輪研發判定不可信、報告作廢。這把「LLM 臆造數字」的風險變成可機器檢出的硬錯誤。

---

### 6. 淘汰準則（集中定義，腳本/critic/chief/報告共用）

所有否決閥值集中在腳本頂部 `REJECT` 字面量（§2），三方都引用同一份：

| 規則名 | 條件（觸發即 reject） | 防什麼 |
|---|---|---|
| `oos_sharpe_min` | `oos.sharpe < 0.5` | 樣本外無效，純過擬合 |
| `is_oos_sharpe_gap_max` | `is.sharpe - oos.sharpe > 0.6` | IS/OOS 落差過大＝過擬合 |
| `max_drawdown_max` | `overall.max_drawdown > 0.25` | 回撤過深，實務無法持有 |
| `min_samples` | `overall.samples < 30` | 樣本太少，統計不可信 |
| `bear_avg_return_min` | `by_regime.bear.avg_return < -0.02` | 空頭全虧，無防禦 |
| `overall_winrate_min` | `overall.winrate < 0.45` | 勝率過低 |

判定順序：critic 先判 → chief 二次覆核（可 `chief_override` 補淘汰）→ `audit_run` 機器覆核數字真偽。**任一規則觸發即淘汰，不做加權妥協**（風控用 AND-of-NOT，寧缺勿濫）。

> 閥值是研發參數，調整時改腳本頂部 `REJECT` 一處即可，報告會自動把當輪 `REJECT` 全文印出，確保每份報告自帶判準、可追溯。

---

### 7. 防 look-ahead / survivorship bias 的具體做法

**Look-ahead（未來資訊）**：
1. **單一基準日 `as_of_date` 貫穿全程**：CLI 抓資料時，所有 dataset 一律過濾 `date <= as_of_date`；回測引擎 `backtest_v2` 內部對訊號日 i「隔天 i+1 開盤進場」（沿用既有 `backtest.py` 的可執行性慣例）。
2. **公布延遲對齊（`publish_lag_days`）**：月營收次月 10 號才公布 → 因子在 `revenue_month + lag` 之後才可見；財報用「公告日」非「結算日」。`data_spec` 的 `point_in_time_rule` 明文規定，`build_panel` 照做：每個因子值的「生效日 = 原始日 + publish_lag_days」，生效日之前該因子對該檔回 None（因子端吃 None 回中性 0.5）。
3. **指標 warm-up**：MA60/季線需要 60 根，回測起點自動往後推 ≥60 交易日（沿用 `backtest.py` 既有 `range(60, ...)` 慣例），避免用到 NaN 指標。
4. **regime 同步**：`regime_classify` 對歷史每一日只用截至該日的 TAIEX，回測該日的 regime override 用「該日的 regime」，不用整段事後標的 regime。

**Survivorship（倖存者偏差）**：
1. **凍結股池快照 `universe_tag`**：`universe.py` 的 `load_universe(tag, as_of)` 回傳的是**回測窗起點當時**的上市清單快照（例：`twse_listed_2021_q2` = 2021 Q2 時點的全上市股 id），而非「今天還活著的股票」。快照存成 `research/data/universe/<tag>.json`（commit 進 repo），一次抓好凍結，避免每次研發用到不同（且已倖存）的清單。
2. **下市/長停處理**：快照內若某檔在回測窗中途下市，`build_panel` 取得的 price_df 自然在下市日截止，回測對該檔在下市日後不再產生新訊號（持倉按既有 hold_days 結算），不把它整檔丟掉——丟掉就是倖存者偏差。
3. **新股不足 N 天**：`global.min_listed_days=120`，上市未滿 120 交易日的標的在該基準日**不納入訊號候選**，但仍保留在股池（之後夠天數了會自然納入），避免「只挑活下來且夠久的」。

---

### 8. 邊界與錯誤處理

| 情境 | 處理 |
|---|---|
| FinMind 限流 / timeout | 沿用 `data.py` 既有 `fetch_finmind` 的 retry+backoff(1s,2s)+timeout30；`build_panel` 對單檔失敗記入 `panel.errors[]` 並跳過該檔（不中斷整輪）；失敗檔數 > 股池 30% 時 CLI 以 exit code 2 中止，agent 回 status=error。 |
| 某檔資料缺漏（缺月營收/估值等） | 該因子回 None → 因子層中性 0.5（不偏多不偏空），不讓缺資料偽裝成看多。 |
| 停牌 / 長停 | price_df 在停牌期無 K 線，回測自動跳過該區間，不前向填補價格（前向填補會虛構成交）。 |
| 新股上市不足 N 天 | `min_listed_days` 門檻，該基準日不納入候選（§7）。 |
| 股池為空 / universe_tag 不存在 | CLI exit 2，回測 agent status=error，critic 對該 draft 直接 reject（無證據）。 |
| CLI 例外（程式 bug） | exit 1 + stderr traceback 末 600 字；回測 agent 把 error 原樣回報，**不捏造數字**；chief 把該 draft 列 rejected 並標 `backtest_error`。 |
| draft 不合 v2 schema | 分析師輸出階段被 `output_schema` 擋下；萬一漏掉，CLI 載入時報錯 → exit 1。 |
| weight 加總 ≠ 1 | CLI 自動正規化並標 `meta.weight_normalized=true`，不報錯。 |
| LLM 臆造回測數字 | `audit_run` 機器比對報告 vs `backtests/*.json`，對不上即整輪作廢（§5.2）。 |

---

### 9. 可測試性（關鍵單元測試點）

放在 `tests/research/`，用 `uv run pytest`：

1. **`backtest_cli` 契約測試**：用一份 fixture draft + 一份小型固定價格 fixture（mock `fetch_finmind`），跑 CLI，斷言 (a) exit 0、(b) `--out` 檔存在且通過 `backtest_result.schema.json`、(c) stdout JSON == 檔案 JSON。
2. **no-look-ahead 測試**：給定一檔在 t 日後才大漲的人造資料，斷言 t 日之前的訊號/因子值不受 t+1 漲幅影響（把 t 之後資料整段刪掉再跑，t 日及之前的 result 必須完全一致）。
3. **publish_lag 對齊測試**：月營收 fixture 設 publish_lag=10，斷言營收因子在公布日前回 None（中性 0.5），公布日後才生效。
4. **survivorship 測試**：股池含一檔中途下市的 fixture，斷言它仍被納入回測且在下市後不再產新訊號（不是整檔被丟）。
5. **REJECT 規則一致性測試**：把 `REJECT` 閥值同步驗證 critic prompt 與 chief 二次覆核用的是同一組（用一份「故意全踩線」的 backtest fixture，斷言 critic verdict=reject 且 triggered_rules 命中全部規則）。
6. **weight 正規化測試**：draft 給 weight 加總=2，斷言 CLI 正規化後等價於加總=1 的結果且 `meta.weight_normalized=true`。
7. **`audit_run` 測試**：人為把報告數字改錯一位，斷言 `audit_run` 回非 0（能抓出臆造）。
8. **regime 占比測試**：固定 TAIEX fixture，斷言 `regime_classify` 三段占比與 regime_expert 規則一致，且歷史任一日可計算（無 NaN-only 區段例外）。

---

### 10. 與現有 code 的對接點摘要

| 既有資產 | 本章如何用 | 是否修改 |
|---|---|---|
| `stock_strategies/data.py`（`fetch_finmind`/`get_price_history`/`get_fundamental`） | `build_panel` 與 CLI 直接沿用，不重寫抓取/retry | 不改 |
| `stock_strategies/indicators.py`（`add_indicators`） | 技術類因子（ma_alignment 等）底層沿用 | 不改（第 2 章因子庫包一層） |
| `stock_strategies/backtest.py`（`backtest`） | **不直接用**；`backtest_v2` 是它的分市況/OOS 強化版（第 4 章），但沿用「i 日訊號、i+1 開盤進場、hold_days 結算」的執行慣例 | 不改（並存） |
| `stock_strategies/loader.py`（`merge_params`/`validate_strategy`） | chief 寫出的 v2 json 帶 `params` 扁平相容區塊，舊 loader 仍可讀；第 5 章再擴 loader 認 v2 全欄 | 本章不改，依賴第 5 章 |
| `strategies/*.json` | chief 把通過策略寫進這裡（`source:"research"`） | 新增檔 |
| `config.py`（`FINMIND_URL`/`CONFIG`） | CLI/build_panel 沿用 | 不改 |


---

## §11 固化層：每日多專家 Pipeline（把研發邏輯搬進 main.py）

# 固化層：每日多專家 Pipeline（把研發邏輯搬進 main.py）

> 本章把「研發層」驗證過的因子組合與策略，固化成每日 runtime 的確定性流程。
> 核心改動：`evaluate.py` 升級成 `evaluate_v2`，逐檔流程＝**build FactorContext → 算所有因子 → 判定該檔當日 regime → 選用策略（多策略投票）→ regime_overrides 調整 → 規則決定 BUY/WATCH/SKIP + 分數 → LLM 解說員產生「專家會議紀要」**。
> 設計原則：**LLM 只解說、不裁決**。所有買賣決策由純 Python 規則引擎產生，LLM 失敗時用模板理由降級，pipeline 不中斷。

---

## 1. 設計總覽與不變式

### 1.1 與舊 `evaluate()` 的關係（向後相容）

- **保留** `evaluate(stock_id, name, strategy=None)` 原函式不動，作為 fallback 與舊測試的依據。
- **新增** `evaluate_v2(stock_id, name, ctx=None, strategies=None, regime=None, llm=True)`，回傳 dict **是舊 `evaluate` 回傳 dict 的超集**（新增欄位，不刪舊欄位），確保 `sheet.append_signals`、`notify.format_messages`、`performance.update_performance` 完全不需改即可吃。
- `main.py` 用環境變數 `PIPELINE_VERSION`（`v1`|`v2`，預設 `v2`）切換，可一鍵回滾。

### 1.2 三條不可違反的不變式

| # | 不變式 | 落地做法 |
| --- | --- | --- |
| I1 | **無 look-ahead**：runtime 評估只能用到「截至昨日收盤」的資料（今日盤前/盤中跑），進場價是「今日/明日開盤」。 | `FactorContext.t` 鎖在資料最後一筆收盤日；所有因子只讀 `ctx` 切片，禁止讀 `df.iloc[-1]` 之後的列。 |
| I2 | **LLM 不改決策**：`action`/`signal_score`/價格全由規則引擎算完才呼叫 LLM，LLM 只能寫文字到 `expert_memo`。 | `evaluate_v2` 先把 `result` dict 完整算好（含 `action`），再把它**唯讀**傳給 `build_expert_memo()`；memo 寫不進 `action`。 |
| I3 | **降級可用**：任何 LLM / 因子 / 資料失敗都不能讓該檔變 ERROR 連鎖到整批。 | 每檔 `try/except` 包覆；LLM 失敗回模板 memo；單因子失敗回中性 `0.5` 且記在 `result["risk_notes"]`。 |

---

## 2. 檔案清單與職責

| 檔案 | 動作 | 職責 |
| --- | --- | --- |
| `stock_strategies/factors/__init__.py` | 新增 | 因子註冊表 `FACTOR_REGISTRY` + `compute_all_factors()`。（本章只定義契約與註冊機制；個別因子實作屬「因子庫」章） |
| `stock_strategies/context.py` | 新增 | `FactorContext` dataclass + `build_context(stock_id, name)`（封裝抓資料、切到 t、無未來資訊）。 |
| `stock_strategies/regime.py` | 新增 | `regime_classify(taiex_df)` + `get_regime_today()`（單日 regime 標籤；runtime 用）。與第「regime 章」共用同一函式，本章只描述 runtime 呼叫點。 |
| `stock_strategies/strategy_select.py` | 新增 | 策略選用器：方案 A（全庫取最高分）/ 方案 B（產業路由）/ 多策略投票。 |
| `stock_strategies/evaluate.py` | **改** | 新增 `evaluate_v2()` 與 `_apply_regime_overrides()`、`_decide_action()`；舊 `evaluate()` 保留。 |
| `stock_strategies/expert_memo.py` | 新增 | LLM 解說員：`build_expert_memo(result, regime, night)`，含 Gemini prompt、JSON 解析、模板降級。 |
| `stock_strategies/notify.py` | **改** | `_format_stock_detail` 末尾插入 `expert_memo` 摘要；新增 memo 區塊渲染。 |
| `stock_strategies/sheet.py` | **改** | `append_signals` 多寫 1 欄 `regime` 與 1 欄 `chief_conclusion`（向後相容：append 在尾端）。 |
| `main.py` | **改** | 接 `build_context` → `get_regime_today` → `evaluate_v2`，並把 regime 傳給濾鏡與 notify。 |
| `stock_strategies/config.py` | **改** | 新增 LLM 成本控制與 regime 門檻常數。 |
| `tests/test_evaluate_v2.py` 等 | 新增 | 見 §10。 |

---

## 3. FactorContext（資料切片，無未來資訊）

### 3.1 資料結構

`stock_strategies/context.py`

```python
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import pandas as pd

@dataclass
class FactorContext:
    stock_id: str
    name: str
    t: pd.Timestamp                  # 評估基準日 = price_df 最後一筆收盤日（不含未來）
    price_df: pd.DataFrame           # 截至 t，欄位 open/high/low/close/volume + 指標(add_indicators 後)
    fundamentals: dict               # {"eps":{year:val}, "roe":{year:val}}（近3完整年度）
    inst: Optional[pd.DataFrame] = None        # 三大法人買賣超（截至 t；可能 None）
    revenue: Optional[pd.DataFrame] = None      # 月營收（截至 t；可能 None）
    valuation: Optional[pd.DataFrame] = None    # PER/PB/殖利率（截至 t；可能 None）
    margin: Optional[pd.DataFrame] = None        # 融資券（截至 t；可能 None）
    shareholding: Optional[pd.DataFrame] = None  # 外資持股比（截至 t；可能 None）
    industry: Optional[str] = None               # 產業別（來自 watchlist category 或 FinMind）
    market_regime: Optional[str] = None          # 該日大盤 regime 標籤 bull/range/bear
    meta: dict = field(default_factory=dict)     # listing_days, data_warnings 等
```

### 3.2 `build_context()` — look-ahead 防護的唯一入口

```python
def build_context(stock_id: str, name: str,
                  regime: Optional[str] = None,
                  years: int = 5) -> FactorContext:
    """抓所有資料、對齊到 t、回傳 FactorContext。任何子資料失敗只記 warning 不拋。"""
```

實作規則（**這樣做**，不是「應該考慮」）：

1. **價格主軸**：`px = add_indicators(get_price_history(stock_id, years))`（沿用 `data.get_price_history` + `indicators.add_indicators`，欄位已是 open/high/low/close/volume）。`t = px["date"].iloc[-1]`。
2. **t 對齊**：所有輔助資料抓回後，一律 `df = df[df["date"] <= t]`。這是消除 look-ahead 的關鍵——即使 FinMind 回傳了比股價更新的法人/營收資料，也砍掉。
3. **月營收延遲**：台股月營收每月 10 日前公布上月數字。為避免「用到尚未公布的營收」，營收再加一道：`revenue = revenue[revenue["revenue_month_end"] + 10天 <= t]`（用「公布日」而非「資料月份」對齊）。沒有公布日欄位時，保守用「資料月份月底 + 11 天 <= t」。
4. **基本面**：沿用 `data.get_fundamental(stock_id)`（近 3 完整年度）。
5. **缺資料不致命**：任一輔助 dataset 抓取例外 → 該欄位設 `None` + `meta["data_warnings"].append("inst 缺")`，不拋。
6. **上市天數**：`meta["listing_days"] = len(px)`。供新股門檻判斷（§7）。
7. **限流節流**：`build_context` 內每個 FinMind 呼叫之間 `time.sleep(CONFIG["finmind_pace_sec"])`（預設 0.3s）；`fetch_finmind` 本身已有 retry+backoff，沿用不重造。

> **對接點**：`build_context` 內所有抓取都走 `data.fetch_finmind` / `data.get_price_history` / `data.get_fundamental`，不直接 `requests.get`（market.py/night_session.py 是大盤層、屬例外，可保留）。

---

## 4. 因子契約與 `compute_all_factors`

### 4.1 因子純函式契約（六章共識，本章固定 runtime 介面）

```python
# 每個因子是純函式
def compute(ctx: FactorContext, params: dict) -> Optional[float]:
    """回傳 0.0..1.0（1=最看多）；資料不足回 None（呼叫端折成中性 0.5）。"""
```

因子以 **註冊表** 暴露（`stock_strategies/factors/__init__.py`）：

```python
FACTOR_REGISTRY: dict[str, "FactorSpec"] = {}

@dataclass
class FactorSpec:
    name: str
    school: str                 # value/growth/momentum/chips/revenue/reversal/breakout
    required_data: list[str]    # 宣告依賴 ["price_df"], ["inst"], ["revenue"]...
    fn: Callable[[FactorContext, dict], Optional[float]]

def register(name, school, required_data):
    def deco(fn):
        FACTOR_REGISTRY[name] = FactorSpec(name, school, required_data, fn)
        return fn
    return deco
```

### 4.2 `compute_all_factors` — runtime 一次算齊

```python
def compute_all_factors(ctx: FactorContext, params: dict) -> dict[str, float]:
    """對 REGISTRY 內所有因子求值，缺依賴/None → 0.5（中性）。
    回傳 {factor_name: score(0..1)}。"""
    out = {}
    for name, spec in FACTOR_REGISTRY.items():
        # required_data 缺 → 直接中性，省一次計算
        if any(getattr(ctx, d, None) is None for d in spec.required_data
               if d not in ("price_df", "fundamentals")):
            out[name] = 0.5
            ctx.meta.setdefault("neutral_factors", []).append(name)
            continue
        try:
            v = spec.fn(ctx, params)
            out[name] = 0.5 if v is None else max(0.0, min(1.0, float(v)))
        except Exception as e:
            out[name] = 0.5
            ctx.meta.setdefault("factor_errors", {})[name] = str(e)[:60]
    return out
```

> **與舊技術分相容**：研發層的因子可包一層把舊 `tech_score_at(row, params)["score"]/100` 當成一個 `momentum_tech` 因子；`detect_patterns` 的 `bonus` 折成 `volume_pattern` 因子 `0.5 + bonus/40` 夾擠。這樣 `evaluate_v2` 不必重寫指標，只是把它「因子化」。

---

## 5. Runtime Regime 判定（單日標籤）

`stock_strategies/regime.py`（與 regime 章共用 `regime_classify`，本章定義 runtime 包裝）

```python
def get_regime_today(taiex_df: pd.DataFrame | None = None) -> str:
    """回傳今日大盤 regime: 'bull' | 'range' | 'bear'。抓不到 → 'range'（中性 fallback）。"""
```

runtime 演算法（必須可在歷史任一日重算，與回測一致）：

1. 取加權指數日線（沿用 `market._fetch_taiex()`，已處理 TAIEX/TWII 兩 data_id + rename）。
2. 計 `ma20`、`ma60`、`ma20_slope = ma20.iloc[-1]/ma20.iloc[-6] - 1`（近 5 日斜率）。
3. 分段規則（門檻進 `config.py`，可調）：
   - `bull`：`close > ma60` 且 `ma20_slope >= +0.01`
   - `bear`：`close < ma60` 且 `ma20_slope <= -0.01`
   - 其餘 → `range`
4. **單日逐檔共用**：`main.py` 每天只算一次 `regime = get_regime_today()`，傳給每檔 `evaluate_v2`，避免 N 次重抓加權指數。

> 注意：`market.get_market_state()`（站上月線否，二元）與 `regime`（三態）是**兩個獨立濾鏡**。`get_market_state` 繼續管「BUY→WATCH 降級」（風控硬閘），`regime` 管「策略內部 overrides 與選股傾向」（軟調整）。兩者不衝突，疊加生效。

---

## 6. 策略選用：一檔股票用哪個策略？

### 6.1 建議：**方案 B 路由為主 + 方案 A 投票為輔的混合制**（預設 `STRATEGY_SELECT_MODE="hybrid"`）

理由：純方案 A（全庫取最高分）會有「過度擬合挑選偏誤」——每檔都選當下分數最高的策略，等於對每檔做了一次 in-sample 最佳化，runtime 容易選到剛好對近況過擬合的策略。純方案 B（硬路由）又太僵化。折衷如下：

```python
# stock_strategies/strategy_select.py
def select_strategies(ctx, all_strategies, regime, mode="hybrid") -> list[dict]:
    """回傳「要對這檔投票的策略子集」（已過 universe 過濾）。"""
```

三模式：

| mode | 行為 | 用途 |
| --- | --- | --- |
| `route` (方案 B) | 依 `ctx.industry` + `ctx.meta` 特性，從策略 `universe.industries` / `school` 路由出符合的策略子集 | 策略庫大、流派分明時 |
| `all` (方案 A) | 全庫策略都評估，**最終取最高 `signal_score` 那一個** | 策略庫小（<5）時最簡單 |
| `hybrid`（預設） | 先用 `route` 選出候選子集（空則 fallback 全庫），子集內**全部評估後投票**（§6.2），不是單純取最高分 | 平衡擬合偏誤與適配度 |

`universe` 過濾（避免把成長股策略套在金融股）：

```python
def _passes_universe(ctx, strat) -> bool:
    uni = strat.get("universe", {})
    inds = uni.get("industries")
    if inds and ctx.industry and ctx.industry not in inds:
        return False
    cap_min = uni.get("market_cap_min")
    if cap_min and ctx.meta.get("market_cap", 0) < cap_min:
        return False
    return True
```

### 6.2 多策略投票（hybrid 的核心）

對候選策略子集 `S`，每個策略 `s` 跑一次 `_score_with_strategy(ctx, factors, s, regime)` 得 `(action_s, score_s)`。投票規則（確定性）：

```
votes_buy   = count(action_s == "BUY")
votes_watch = count(action_s == "WATCH")
n           = len(S)

# 決策（保守：要多數同意才 BUY）
if votes_buy / n >= CONFIG["vote_buy_ratio"]   (預設 0.5):  action = "BUY"
elif (votes_buy + votes_watch) / n >= 0.5:                 action = "WATCH"
else:                                                       action = "SKIP"

# 分數 = 投 BUY/WATCH 的策略的 score 加權平均（用各策略對該檔的歷史夏普當權重，
#        無歷史則等權）；落單的 SKIP 不拉低分數但記在 components.dissent
signal_score = weighted_mean([score_s for s in agree], weights)
```

- `result["components"]["votes"] = {"buy":k, "watch":m, "skip":j, "n":n}`
- `result["components"]["winning_strategy"]`＝子集中分數最高且 action 與最終一致的策略 id（給 memo 與 sheet 用）
- `result["strategy_id"]` 設為 `winning_strategy`（向後相容，舊欄位仍有單一 id）

> **成本/穩定性**：投票只跑純 Python，零 LLM、零額外 FinMind（因子已在 §4 算好一次，所有策略共用同一份 `factors` dict，只是用不同 `weights/門檻` 重算加權分），所以一檔即使被 8 個策略投票也只是 8 次純算術。

---

## 7. `evaluate_v2` 主流程

`stock_strategies/evaluate.py`（新增，不動舊 `evaluate`）

```python
def evaluate_v2(stock_id: str, name: str,
                ctx: FactorContext | None = None,
                strategies: list[dict] | None = None,
                regime: str = "range",
                llm: bool = True,
                night: dict | None = None) -> dict:
    """V2 逐檔評估：因子化 + regime + 多策略投票 + 規則決策 + LLM 解說。
    回傳 dict 為舊 evaluate() 回傳的超集。"""
```

逐步（**這樣做**）：

1. **建 context**：`ctx = ctx or build_context(stock_id, name, regime)`；`ctx.market_regime = regime`。
2. **不足資料閘**（沿用舊邏輯 + 擴充）：
   - `len(ctx.price_df) < 100` → `action="SKIP"`, `risk_notes += "價格資料不足"`，回傳（不呼叫 LLM）。
   - `ctx.meta["listing_days"] < CONFIG["min_listing_days"]`（預設 60）→ `action="SKIP"`, `risk_notes += "新股上市未滿{N}日"`（避免 survivorship/新股暴衝噪音）。
3. **算因子**：`factors = compute_all_factors(ctx, base_params)`（base_params＝預設策略 merge）。
4. **選策略**：`S = select_strategies(ctx, strategies or list_strategies(), regime, mode=CONFIG["strategy_select_mode"])`。
5. **逐策略評分 + 投票**：得 `action0, signal_score, components`（§6.2）。
   - 單策略 `_score_with_strategy` 內：基本面閘沿用舊 `fund_pass` 邏輯；技術/因子分由 `factors` 加權（策略 `factors:[{name,weight}]`，無 `factors` 欄位時退回舊四開關 → 包成因子）；回測勝率欄位由研發期固化進策略檔的 `backtest.winrate_by_regime[regime]`（runtime **不重跑回測**，直接讀研發產出，省時且避免 runtime look-ahead）。
6. **regime_overrides**：`action, params_eff = _apply_regime_overrides(action0, winning_strategy, regime)`（§7.1）。
7. **算進出場價**（沿用舊公式，但停利/停損取 `params_eff`）：
   - `entry = float(ctx.price_df["close"].iloc[-1])`
   - `stop_price / target_price / rr / position_pct / entry_rule` 同舊 `evaluate`。
8. **risk_notes**：沿用舊 5 條 +（regime=bear 時）「空頭 regime，已縮停損/降部位」+ 因子缺漏警示。
9. **trend 區塊**：完全沿用舊 `evaluate` 的 `chg_5d/chg_20d/vol_ratio/pct_from_high/above_ma20/above_ma60`（notify 依賴）。
10. **LLM 解說**（最後一步，唯讀）：
    - 只在 `action in ("BUY","WATCH")` 且 `llm=True` 時呼叫 `build_expert_memo(result, regime, night)`。
    - `result["expert_memo"] = memo`（含 5 專家句 + 首席結論）；失敗用模板（§8.3）。
    - SKIP/ERROR **不呼叫 LLM**（成本控制，見 §9）。
11. **全程 `try/except`**：任何例外 → `action="ERROR"`, `risk_notes += "錯誤: ..."`（同舊行為），仍回傳 dict。

### 7.1 `_apply_regime_overrides`

```python
def _apply_regime_overrides(action, strategy, regime) -> tuple[str, dict]:
    ov = (strategy.get("regime_overrides") or {}).get(regime, {})
    params = merge_params(strategy)
    # 覆寫允許欄位
    for k in ("min_score", "stop_loss", "target_return", "position_scale"):
        if k in ov: params[k] = ov[k]
    # 停止進場開關
    if ov.get("no_entry"):           # 例如 bear regime 某些策略停買
        action = "WATCH" if action == "BUY" else action
    # 動態門檻：bear 拉高 min_score
    if action == "BUY" and "min_score" in ov:
        if result_score < ov["min_score"]:
            action = "WATCH"
    return action, params
```

預設 regime_overrides（策略未自定時的系統級保底，放 `config.py`）：

| regime | 系統保底行為 |
| --- | --- |
| bull | 不額外限制 |
| range | `position_scale *= 0.8` |
| bear | `no_entry`=true（BUY→WATCH）、`stop_loss` 不放寬、`position_scale *= 0.5` |

### 7.2 `evaluate_v2` 回傳 dict（舊欄位 + 新欄位）

```python
{
  # ===== 舊欄位（notify/sheet/performance 直接吃，不可改名）=====
  "stock_id","name","date","strategy_id","action","signal_score",
  "components":{... 舊欄位 ...},      # 見下方新增
  "trend":{...}, "entry_price","stop_loss_price","target_price",
  "risk_reward_ratio","position_size_pct","entry_rule","risk_notes":[...],
  # ===== V2 新增 =====
  "regime": "bull|range|bear",
  "factors": {factor_name: 0..1},
  "components": {
     ... 舊 ...,
     "votes": {"buy":k,"watch":m,"skip":j,"n":n},
     "winning_strategy": "momentum_v2",
     "dissent": ["value_v1:SKIP", ...],
     "factor_top": [("breakout",0.9),("revenue_yoy",0.85)],  # 給 memo 用
  },
  "expert_memo": {                  # LLM 產出或模板
     "data_expert": "…一句",
     "tech_expert": "…一句",
     "fund_expert": "…一句",
     "chips_expert": "…一句",
     "market_expert": "…一句",
     "chief_conclusion": "…首席結論",
     "source": "llm" | "template",  # 供觀測降級率
  },
}
```

---

## 8. LLM 解說員（專家會議紀要）

### 8.1 定位與安全邊界

- **只解說已決定的結果**：輸入已含 `action`、`signal_score`、進出場價；prompt 明令「不得更動買賣決策，只能解釋」。
- **沿用既有 Gemini 介面**：複用 `api/services/ai_generator.py` 的 `genai` 初始化模式（`GEMINI_MODEL` 預設 `gemini-2.5-flash`、`GEMINI_API_KEY`/`GOOGLE_API_KEY`、`response_mime_type=application/json`）。新模組 `expert_memo.py` 自帶 client，不耦合策略生成器。

### 8.2 Gemini Prompt 設計

`stock_strategies/expert_memo.py`

```python
SYSTEM_PROMPT_MEMO = """你是台股投資委員會的「會議書記」。輸入是系統「已經做完決策」的一檔股票評估結果。
你的工作是把數字翻成五位專家各一句話的會議紀要，外加一句首席結論。

## 硬規則
- 只能輸出一份 JSON，無 markdown fence、無多餘文字
- 嚴禁更改或質疑系統的買賣決策(action)；你只是「解釋為什麼系統這樣判」
- 每位專家最多 40 個中文字，講人話、給依據（引用提供的數字），不要喊口號
- 不得捏造數據；只能用 input 內出現的數字
- 首席結論要呼應 action（BUY=可進場理由+風險一句；WATCH=還差什麼；）

## 輸出 schema
{
 "data_expert":   "資料/趨勢專家：依 5日/20日漲跌、量比、距高點講一句",
 "tech_expert":   "技術專家：依觸發訊號(均線/布林/KD/MACD)講一句",
 "fund_expert":   "基本面專家：依 EPS/ROE 是否過門檻講一句",
 "chips_expert":  "籌碼專家：依三大法人/融資券/外資持股因子講一句(沒資料就說資料不足)",
 "market_expert": "市場專家：依大盤 regime 與夜盤濾鏡講一句",
 "chief_conclusion": "首席：兩句內，呼應最終 action"
}
"""
```

User payload（**只塞已算好的精簡數字，不塞原始 df**，省 token）：

```python
payload = {
  "stock": f"{result['stock_id']} {result['name']}",
  "action": result["action"],
  "signal_score": result["signal_score"],
  "regime": result["regime"],
  "night_bias": (night or {}).get("bias"),         # bull/bear/flat
  "trend": result["trend"],                         # chg_5d/20d, vol_ratio, pct_from_high, above_ma20/60
  "tech_signals": result["components"].get("tech_signals", []),
  "fundamental_pass": result["components"].get("fundamental_pass"),
  "eps_min": result["components"].get("eps_min"),
  "roe_min": result["components"].get("roe_min"),
  "factor_top": result["components"].get("factor_top", []),
  "winrate": result["components"].get("backtest_winrate"),
  "entry": result["entry_price"], "stop": result["stop_loss_price"], "target": result["target_price"],
  "risk_notes": result["risk_notes"][:4],
}
```

呼叫設定：`temperature=0.3`、`response_mime_type="application/json"`、`max_output_tokens=400`（夠 6 句）、`timeout` 由 SDK 控、外層再包 `concurrent.futures` 超時 8s。

### 8.3 降級（fallback）— 必須有

```python
def build_expert_memo(result, regime, night) -> dict:
    try:
        memo = _call_gemini_memo(result, regime, night)   # 8s timeout
        memo["source"] = "llm"
        return _validate_memo(memo)        # 缺鍵補模板句、超長截斷
    except Exception as e:
        return _template_memo(result, regime, night, reason=str(e)[:60])
```

`_template_memo` 用純規則拼字串（零外部依賴），確保「LLM 全掛」時報告照樣有專家會議紀要：

```python
def _template_memo(result, regime, night, reason=""):
    t, c = result["trend"], result["components"]
    return {
      "data_expert": f"近5日{t['chg_5d']:+.1f}%、量比{t['vol_ratio']}，距高點{t['pct_from_high']:.0f}%。",
      "tech_expert": ("觸發 " + "、".join(c.get("tech_signals", []))) if c.get("tech_signals") else "技術面無明確多方訊號。",
      "fund_expert": ("EPS/ROE 過門檻" if c.get("fundamental_pass") else "基本面未達門檻") + "。",
      "chips_expert": "籌碼資料不足，本次未納入" if "chips" in result.get("factors", {}) and result["factors"].get("chips")==0.5 else "籌碼面中性。",
      "market_expert": f"大盤 regime={regime}" + (f"、夜盤{night['label']}" if night else "") + "。",
      "chief_conclusion": _chief_by_action(result),
      "source": "template", "fallback_reason": reason,
    }
```

`_validate_memo`：6 個鍵必須齊全（缺則補模板句）、每句截斷至 60 字、剝除任何 `action` 字眼篡改（防 prompt injection 改決策——即使 LLM 寫了「應改為 SKIP」也只當文字，不影響 `result["action"]`，因為 action 早已定案）。

---

## 9. 成本控制（每日 N 檔的 LLM 呼叫量）

| 機制 | 規則 |
| --- | --- |
| **只對 BUY/WATCH 生成 memo** | SKIP/ERROR 不呼叫（佔比通常 >60%）。`evaluate_v2(..., llm=True)` 內部 gate。 |
| **WATCH 上限** | 只對 `signal_score` 排序後前 `CONFIG["memo_max_watch"]`（預設 8）檔 WATCH 生 memo，其餘 WATCH 用模板。BUY 一律生 LLM。 |
| **全域開關** | `CONFIG["memo_enabled"]`（env `MEMO_ENABLED`，預設 true）。設 false → 全部走模板，零 LLM 成本，pipeline 不變。 |
| **批次節流** | memo 呼叫間 `time.sleep(0.3)`，避免 Gemini 限流。 |
| **每日預算上限** | `CONFIG["memo_max_calls"]`（預設 30）。達上限後剩餘檔自動走模板，並在報告標頭記「LLM 配額用罄，部分為模板」。 |
| **token 控制** | payload 只塞精簡數字（§8.2），單檔 input < 400 token、output 上限 400 token。30 檔/日 ≈ 2.4 萬 token，Gemini Flash 成本可忽略。 |

`main.py` 在進入逐檔迴圈前先決定哪些檔要 LLM（需先有 action/score → 因此 memo 在「決策後」批次補，而非逐檔即時）：建議流程改為**兩段**——先全部 `evaluate_v2(..., llm=False)` 算決策，再對 BUY + top WATCH 批次補 memo。這樣排序與配額分配最乾淨。

---

## 10. `main.py` 整合（改動點）

```python
# main.py（v2 分支）
from stock_strategies.context import build_context
from stock_strategies.regime import get_regime_today
from stock_strategies.evaluate import evaluate_v2
from stock_strategies.expert_memo import build_expert_memo
from stock_strategies.loader import list_strategies

def main_v2():
    ... 環境檢查、read_watchlist 同舊 ...
    market = get_market_state()           # 二元月線濾鏡（保留）
    night  = get_night_session()
    regime = get_regime_today()           # ← 新增：每日只算一次三態 regime
    strategies = list_strategies()

    # 第一段：純規則決策（不呼叫 LLM）
    results = []
    for i, row in enumerate(watchlist, 1):
        sid, name = str(row["stock_id"]), row.get("name","")
        r = evaluate_v2(sid, name, strategies=strategies, regime=regime,
                        llm=False, night=night)
        # 產業別注入（給策略路由用）
        r.setdefault("components", {})["industry"] = row.get("category")
        results.append(r)
        time.sleep(0.6)

    # 濾鏡（順序：先市場硬閘，再夜盤）— 完全沿用既有函式
    apply_market_filter(results, market)
    apply_night_filter(results, night)

    # 排序（沿用舊 order map）
    order = {"BUY":0,"WATCH":1,"SKIP":2,"ERROR":3}
    results.sort(key=lambda x:(order.get(x.get("action"),4), -x.get("signal_score",0)))

    # 第二段：對 BUY + top WATCH 批次補 memo（成本控制在此集中）
    _attach_memos(results, regime, night)   # §9 配額/上限邏輯都在這

    append_signals(results)                  # sheet 改動見 §11
    # performance / telegram / perf summary — 完全沿用舊流程
```

> 關鍵：**濾鏡降級（BUY→WATCH）發生在 memo 生成之前**，所以 memo 的 `market_expert` 句能正確反映「因大盤/夜盤被降級」。`apply_market_filter`/`apply_night_filter` 已會 append risk_notes，memo payload 直接吃得到。

---

## 11. Sheet / Notify / Telegram 整合

### 11.1 `sheet.append_signals`（向後相容擴欄）

在現有 14 欄尾端 **append** 兩欄（不動既有欄位順序，舊 sheet 直接相容）：

```python
# 既有 header 不變，只在建立新 Signals 分頁時多加：
[..., "tech_signals", "risk_notes", "regime", "chief_conclusion"]
# 每列尾端多塞：
s.get("regime",""), s.get("expert_memo",{}).get("chief_conclusion","")
```

對舊 sheet（已有 14 欄 header）：偵測 header 長度 < 16 時，用 `ws.update` 補兩個 header cell；資料列一律 `append_rows`（gspread 會自動對齊到最寬，不足補空）。

### 11.2 `notify._format_stock_detail`（插入專家紀要）

在現有 `_format_stock_detail` 回傳 lines 末尾、`risk_notes` 之後插入 memo 區塊：

```python
m = s.get("expert_memo")
if m:
    lines.append(f"🗣 *專家會議紀要* {'🤖' if m.get('source')=='llm' else '📋'}")
    lines.append(f"  📊 資料：{m['data_expert']}")
    lines.append(f"  📈 技術：{m['tech_expert']}")
    lines.append(f"  💰 基本面：{m['fund_expert']}")
    lines.append(f"  🏦 籌碼：{m['chips_expert']}")
    lines.append(f"  🌐 市場：{m['market_expert']}")
    lines.append(f"  👑 *首席：{m['chief_conclusion']}*")
```

- BUY/top-WATCH 顯示完整六行；其餘 WATCH 只顯示首席一行（控訊息長度，避免 Telegram 4096 字上限爆）。
- `format_messages` 多接一個 `regime` 參數，在第一則「市場總覽」加一行 `🧭 大盤 regime：{中文標籤}`（bull=多頭/range=盤整/bear=空頭），與既有「大盤濾鏡/夜盤濾鏡」並列。`regime` 不傳時保持舊行為。

### 11.3 不需改動者

`performance.update_performance` / `summary` 只讀 `action=="BUY"`、`date`、`stock_id`、`entry_price`，這些 V2 全保留 → **零改動**。`premarket.py`/`format_premarket` 同理（讀 sheet 扁平欄位）。

---

## 12. 邊界與錯誤處理（逐項落地）

| 情境 | 處理 |
| --- | --- |
| FinMind 限流 / 逾時 | `data.fetch_finmind` 已有 retry+backoff；`build_context` 每呼叫間 sleep `finmind_pace_sec`；輔助 dataset 失敗 → `None` + warning，不拋。 |
| 停牌 / 當日無成交 | `price_df` 以 FinMind 實際交易日為準，停牌日本就不在 df → `t` 自動退到最後有效交易日；不另補列。 |
| 新股上市不足 N 天 | `meta["listing_days"] < min_listing_days(60)` → SKIP + note（§7 step2），且回測勝率欄缺 → 投票時該策略視該檔 SKIP。 |
| 基本面缺年度 | 沿用舊 `len(eps_vals)>=2` 判斷；不足 → `fund_pass=False`，基本面強制策略直接擋 BUY。 |
| 因子單點失敗 | `compute_all_factors` 內 `try` → 中性 0.5 + 記 `factor_errors`，memo 的籌碼/資料專家會說「資料不足」。 |
| regime 抓不到 | `get_regime_today` → `"range"`（中性），不讓整批失敗。 |
| LLM 失敗/逾時/配額 | `_template_memo` 降級，`source="template"`，報告標頭可顯示降級率。 |
| Gemini 回非法 JSON | `ai_generator._extract_json` 同款抽取 + `_validate_memo` 補鍵；再失敗走模板。 |
| 整檔崩潰 | `evaluate_v2` 外層 `try/except` → `action="ERROR"`（與舊一致），排序時沉到最後，不進 performance。 |
| look-ahead 自檢 | `build_context` 對齊 t 後 `assert (aux["date"] <= ctx.t).all()`（debug 模式開，prod 用 warning）。 |

---

## 13. 可測試性（關鍵單元測試點）

`tests/`（用 `uv run pytest`）。**所有測試用固定 fixture DataFrame，不打網路**（FinMind 呼叫以 monkeypatch 攔截）。

### 13.1 不變式測試（最高優先）

- `test_llm_does_not_change_action`：mock `_call_gemini_memo` 回傳含「應改為 SKIP」「action: BUY」的惡意字串 → 斷言 `result["action"]` 不變、memo 只當文字。（守 I2 / prompt injection）
- `test_no_lookahead_in_context`：給一段 price_df，輔助 dataset 含「比 t 更新的列」→ 斷言 `build_context` 後所有 aux `date <= t`。（守 I1）
- `test_llm_failure_falls_back_to_template`：mock LLM 拋例外 → `expert_memo["source"]=="template"` 且六鍵齊全、`action` 不受影響。（守 I3）

### 13.2 決策邏輯

- `test_evaluate_v2_superset_of_v1`：對同一 mock 資料、單一 default 策略、`llm=False`，斷言 V2 回傳含 V1 全部欄位且 `action` 一致（回歸保護）。
- `test_vote_majority_buy`：3 策略中 2 投 BUY → `action=="BUY"`、`votes` 正確。
- `test_vote_no_majority_downgrades`：3 策略 1 BUY/1 WATCH/1 SKIP → `action=="WATCH"`。
- `test_regime_override_bear_blocks_buy`：regime=bear + `no_entry` → BUY 變 WATCH，停損未放寬。
- `test_universe_filter_routes`：成長股策略 `universe.industries=["半導體"]`、ctx.industry="金融" → 該策略不進候選。

### 13.3 因子與 regime

- `test_compute_all_factors_neutral_on_missing`：ctx 缺 `inst` → 依賴 inst 的因子回 0.5 且記 `neutral_factors`。
- `test_factor_clamped_0_1`：因子回 1.7 / -0.2 → 夾擠成 1.0 / 0.0。
- `test_regime_classify_three_states`：構造 bull/range/bear 三段 taiex fixture，斷言標籤正確且「歷史任一日可重算」。

### 13.4 成本控制

- `test_memo_only_for_buy_watch`：5 檔（2 BUY/1 WATCH/2 SKIP）→ LLM 呼叫次數 == 3（mock 計數）。
- `test_memo_watch_cap`：12 檔 WATCH、`memo_max_watch=8` → 只前 8 檔 `source=="llm"`，其餘 `template`。
- `test_memo_budget_exhausted`：`memo_max_calls=2` → 第 3 檔起走模板。

### 13.5 整合

- `test_sheet_backward_compat`：V2 result 餵 `append_signals`，舊 14 欄 header 不報錯、新 2 欄正確 append。
- `test_notify_renders_memo`：含 memo 的 result 過 `format_messages` → 訊息含「專家會議紀要」與首席結論、總長 < 4096。

---

## 14. config.py 新增常數

```python
CONFIG.update({
  # regime
  "regime_ma_fast": 20, "regime_ma_slow": 60, "regime_slope_th": 0.01,
  # 策略選用
  "strategy_select_mode": "hybrid",   # route|all|hybrid
  "vote_buy_ratio": 0.5,
  # 資料/新股
  "min_listing_days": 60, "finmind_pace_sec": 0.3,
  # LLM 成本
  "memo_enabled": True, "memo_max_watch": 8, "memo_max_calls": 30,
  "memo_timeout_sec": 8, "memo_model": "gemini-2.5-flash",
})
PIPELINE_VERSION = os.environ.get("PIPELINE_VERSION", "v2")
```


---

## §12 整合審查與實作里程碑

> 由整合審查官交叉檢查六章介面一致性後產出。**§4 已採納其中技術裁決並凍結為契約**；
> 本節保留審查原文供追溯，並給出 M0–M7 實作順序。

### §12.1 整體結論

方向正確、分層清楚、向後相容意識強（v1 byte-identical、相容 wrapper、缺料降級），六章對『純 Python 確定性地基 + LLM 當設計者/解說員、回測數字一律來自確定性引擎』的核心架構決策高度一致，且已核對全部與現有程式碼(data/evaluate/backtest/loader/market/config/ai_generator/sheet 14欄/performance/main)相符，沒有憑空另起爐灶。但此 spec『尚未 ready 直接進實作計畫』——卡在五個 blocker 級的跨章介面三重定義/簽名衝突：FactorContext 被三章各自定義且 price vs price_df 欄名分歧、build_context 三套互斥簽名(回測限流 vs 自抓)、因子缺料回 None vs 0.5 的契約分歧(直接改變總分)、regime_overrides 白名單與 key 名三章各表(stop_entry/no_entry/paused)、backtest_v2 單檔 vs 投組聚合粒度未定。另有兩個 major 級的核心算法分歧(factor composite vs 三段加權 vs 多策略投票的評分模型、survivorship universe owner)需人拍板。這些不是文字微調而是會讓平行開工的章節對著不同版本實作的整合炸彈。建議：先插入一個『M0 契約凍結』里程碑，把上述八個跨章介面收斂成單一真相來源(context.py + schema.py 常數 + schemas/*.json)，並由使用者對評分模型、universe 近似法、快取選型(建議 parquet)、regime 唯一真相四件事拍板後，才把六章拆成可平行的實作計畫。地基(M0→M1→M2→M3→M4)務必先於 workflow(M5)與固化(M6)，這點 spec 已正確主張。完成 M0 後本 spec 即可交付。"}

### §12.2 跨章一致性問題（已於 §4 收斂，此處供追溯）

| 嚴重度 | 涉及 | 問題摘要 | 裁決 |
| --- | --- | --- | --- |
| BLOCKER | data / factors / runtime 三章都各自定義 FactorContext | FactorContext 被三章各自 @dataclass 定義且簽名不一致：(1) 欄位命名分歧——data 章與 runtime 章用 price_df，factors 章用 price；(2) 欄位集合不同——data 章多了 index_df/shares_outstanding/market_cap/meta 與方法 latest_price()/asof_row()，factors 章把 market_regime 放進 dataclass 但沒有 index_df，runtime 章有 name/t/meta 但沒有 index_df/valuation 細節對齊；(3) as_of 型別分歧——data 章 pd.Timestamp、factors 章 pd.Timestamp\|str、runtime 章叫 t;(4) 對 add_indicators 套用時機不一致——factors 章說『price 進來前已過 add_indicators』，runtime 章說 build_context 內對齊但沒明說誰算指標。三個定義同名同責但不可互換，是最嚴重的整合炸彈。 | 把 FactorContext 收斂成單一真相來源，建議放 stock_strategies/context.py（地基一/二/runtime 共 import 同一個），由地基一擁有。統一決定：欄位名一律 price_df（不要 price），保留 index_df（相對強弱因子需要），as_of 一律 pd.Timestamp（字串在 build_context 入口轉），market_regime/industry/fundamentals/inst/revenue/valuation/margin/shareholding 為標準欄位，name/meta 可選。明文規定 price_df 進 ctx 時『尚未』add_indicators，由因子層或 build_context 末段統一呼叫一次並 cache，避免重算。其餘三章的 dataclass 定義刪除，改 from .context import FactorContext。 |
| BLOCKER | data / factors / runtime 三章的 build_context 簽名 | build_context 有三套互斥簽名與職責：data 章 build_context(stock_id, as_of_date:str, *, lookback_years, info_df, strict) 自己抓資料；factors 章 build_context(stock_id, as_of, raw_bundle:dict) 吃『已抓好的 raw_bundle』只做切片（為了回測一次抓、逐日切片避免限流）；runtime 章 build_context(stock_id, name, regime, years) 自己抓。這直接撞上回測場景：回測逐日呼叫若每天都重抓（data/runtime 版）就會觸發 FinMind 限流，factors 版的 raw_bundle 注入才是回測可行解。三版無法共存。 | 拆成兩個明確函式：build_context_from_bundle(stock_id, as_of, raw_bundle)->FactorContext（純切片、無 IO，回測引擎/CLI 逐日呼叫用這個）與 build_context(stock_id, as_of, *, lookback_years, strict)->FactorContext（抓資料一次後內部呼叫 from_bundle，runtime 單日用這個）。runtime 章的 name 參數移到 FactorContext.name 或由呼叫端帶。明定『回測路徑一律走 from_bundle，runtime 單檔走 build_context』。 |
| BLOCKER | factors 章 compute 契約 vs runtime 章 register/compute_all_factors vs data 章描述 | 因子缺資料的回傳值契約三章說法不一：factors 章 compute 恆回 [0,1]、缺料回 0.5（純函式內部兜底）；runtime 章 register 的 compute 回 Optional[float]、缺資料回 None，再由 compute_all_factors 把 None 折成 0.5；data 章描述『缺資料回 None 或中性 0.5』。到底因子本體是回 0.5 還是回 None，決定了 composite 加權時 None 要不要被排除（影響分母 Σweight）還是當 0.5 計入——這會實質改變策略總分。 | 統一為『因子本體回 Optional[float]，缺 required_data 回 None；0.5 是有資料但中性』。compute_factor/compute_all_factors 層負責 None 處理，但加權策略要明定：composite = Σ(score*weight)/Σweight 時，None 因子『剔除出分子與分母』（不是當 0.5 計入），這樣缺料因子不汙染總分。把這條寫進 schema 章 composite 公式與 base.py docstring，三章引用同一句。 |
| BLOCKER | schema 章 apply_regime_overrides vs runtime 章 _apply_regime_overrides | regime_overrides 的套用有兩個 owner 且欄位語彙不一致：schema 章 REGIME_OVERRIDE_WHITELIST 含 min_score/stop_loss/weight 調整/是否停止進場，workflow 章 schema 用 {min_score, stop_loss, stop_entry, weight_scale}，runtime 章 _apply_regime_overrides 用 {min_score, stop_loss, target_return, position_scale, no_entry}。『停止進場』在三章分別叫 stop_entry / no_entry / paused，『權重縮放』叫 weight_scale / factor_weight_multipliers / weight 調整。同一概念三個 key 名，固化層讀研發層產的 JSON 會 silently 不生效。 | 在 schema 章 v2_schema_constants 定一份 REGIME_OVERRIDE_WHITELIST 當唯一真相，敲定 key 名（建議：no_entry:bool、position_scale:float、min_score:float、stop_loss:float、target_return:float、factor_weight_multipliers:dict）。schema 章的 apply_regime_overrides 是『編譯期』套用（merge_params 內），runtime 章 _apply_regime_overrides 應改為呼叫 schema 章的同一函式或只做『系統級保底』（bear no_entry/縮部位），不要各自實作一套白名單。明定：策略級覆寫歸 schema.apply_regime_overrides，系統級硬保底歸 runtime。 |
| BLOCKER | backtest 章 backtest_v2 回傳 vs workflow 章 backtest_cli/backtest_result.schema.json | 回測結果的『粒度』兩章沒對齊：backtest 章 backtest_v2(strategy_def, price_df單檔, regime_series) 回的是『單檔』結果，含 sharpe/sortino/profit_factor/oos{in_sample,out_sample,degradation{verdict}}；workflow 章 backtest_cli 與其 REJECT 準則假設拿到的是『投組層級』彙整（對股池逐檔回測後聚合），且 key 名是 is{sharpe,samples} 與 oos{...}（不是 backtest 章的 oos{in_sample,out_sample}）。investment portfolio 的 sharpe/max_drawdown 不能由單檔結果直接平均得到，聚合邏輯該放 CLI 還是引擎、等權還是訊號日資金分配，目前無人擁有。 | 敲定：backtest_v2 維持單檔職責；新增 aggregate_portfolio(list_of_single_results 或 trades)->portfolio_result 由回測章提供（因為 sharpe/maxDD 要串權益曲線、不能事後平均），backtest_cli 只是薄殼呼叫它。統一 key 名：把 workflow 章的 is/oos 對齊成 backtest 章的 oos{in_sample,out_sample,degradation}（或反向，二選一寫進共用 backtest_result.schema.json）。並明定投組資金分配假設（建議等權、單檔同時只持一張）寫進 meta。 |
| MAJOR | factors 章 / schema 章 / runtime 章（evaluate 接管邊界） | factor composite 與舊三段加權(fund0.3/tech0.3/bt0.4)的關係三章各表，且互相留為對方的開放問題：factors 章問『composite*100 餵 min_total_score_for_buy 後，三段加權怎麼共存，並存兩路徑還是整段換掉』；schema 章假設『保留三段外層、factors 只決定 tech 內部、三段權重用固定 0.3/0.3/0.4 或由 period 決定』；runtime 章 evaluate_v2 描述為『算因子→多策略投票→規則決定』看似已棄三段加權改投票制。三種模型（純 composite 取代 tech / 三段外層保留 / 多策略投票）會得到完全不同的 signal_score 與 BUY 門檻。 | 由 schema 章+runtime 章共同拍板單一評分模型。建議：v2 一律走『factors composite 決定 tech_score 內部，外層仍三段加權但權重由 period 決定（短線偏 tech、長線偏 fund）』，多策略投票是『選哪檔策略』而非取代評分。把最終 signal_score 公式寫成一條，三章（factors/schema/runtime evaluate）引用同一條，並在 schema 章 PERIOD_DEFAULTS 內放各 period 的三段權重。 |
| MAJOR | data 章 get_xxx loader 簽名 vs factors 章 data.py 擴充取數簽名 | 同一批新資料源 loader 兩章簽名不同：data 章用 get_institutional/get_month_revenue/get_valuation/get_margin/get_shareholding(stock_id, start:str, as_of:str\|None)（point-in-time 切片、走快取）；factors 章用 get_institutional/get_monthly_revenue/get_valuation/get_margin/get_shareholding(stock_id, years)（用 years、無 as_of）。函式名也分歧：get_month_revenue vs get_monthly_revenue。years-based 無 as_of 的版本會 look-ahead（回測逐日切片需要 as_of 上界），不可用於回測。 | 統一為 data 章的 (stock_id, start, as_of=None) 簽名（as_of 是 point-in-time 正確性的硬需求）；years 是便利參數可在內部換算 start。函式名統一為 get_monthly_revenue（complete word）。factors 章改 import data 章的同名函式，不要再宣告一套。明定這些 loader 全歸地基一，地基二只消費。 |
| MAJOR | workflow 章 v2_strategy.schema.json vs schema 章 v2_strategy_schema | v2 策略 JSON 結構兩章有差異欄位：schema 章有 fundamental:{eps_threshold,roe_threshold}、entry.min_factor_score/min_signal_score/paused、factors[].enabled、legacy_signals、version:2 欄；workflow 章 schema 沒有 fundamental 區塊（基本面門檻去哪？）、factors 限定 [3..6] 筆、regime_overrides 用 stop_entry/weight_scale、有 source enum 含 'research'、有 params 向後相容扁平區塊。兩份都自稱『六章共同遵守的契約』但欄位集不同，研發層產出的 draft 可能過不了固化層 validate_strategy。 | 以 schema 章 stock_strategies/schema.py 為單一真相，workflow 章的 research/schemas/v2_strategy.schema.json 必須是它的 JSON Schema 鏡像（或由 schema.py 自動生成）。對齊缺漏：把 fundamental 區塊補進 workflow schema；factors 筆數限制 [3..6] 若要保留就也寫進 schema 章 CLAMPS；source enum 加 'research'；regime_overrides key 名依前述 blocker 統一。新增一個跨章測試：用 schema.py.validate_strategy 去驗 workflow 產的每個 draft，CI 強制。 |
| MAJOR | data 章 get_index_history vs backtest 章 regime_classify/get_regime_series_for vs runtime 章 get_regime_today vs market.py | 大盤指數來源與 regime 計算的對接點分散且重疊：data 章新增 get_index_history 並說『market.py 改呼叫此函式』；backtest 章 regime_classify 吃 taiex_df 但沒說 taiex_df 從哪來（應是 get_index_history）；runtime 章 get_regime_today 說與 regime_classify『共用同一分段規則(ma20/ma60/斜率)』但又是另一支函式；同時 market.get_market_state 仍存在做二元站上月線。三套 regime 邏輯（regime_classify 三態 / get_regime_today 三態 / get_market_state 二元）必須保證『分段規則完全一致』否則回測 regime 與 runtime regime 不同步，by_regime 績效對不上實盤。 | 明定唯一 regime 真相在 backtest 章 regime.py 的 regime_classify(taiex_df)。get_regime_today 必須是『regime_classify(get_index_history()).iloc[-1]』的薄包裝，不可另寫一套門檻。market.get_market_state 保留但降格為純『二元硬降級濾鏡』（runtime 章已澄清 market_state 管硬降級、regime 管軟調整，可並存——審查官確認接受疊加）。所有 taiex_df 一律來自 data.get_index_history。regime 參數(ma_fast/ma_slow/slope_win)集中在 config 或 regime.py 常數，runtime 與回測共用。 |
| MAJOR | workflow 章 load_universe vs backtest/data/schema 章的 survivorship 處理 | survivorship bias 的責任歸屬在四章都被列為『跨章協調』開放問題但無人最終擁有：data 章先以『該日 price_df 有資料』代理；schema 章採『回測放寬 universe、僅 runtime 選股套用』；backtest 章標 universe_note『盡力而為』；workflow 章承諾 load_universe 回『凍結時點上市快照』並 commit research/data/universe/<tag>.json。四種策略並存會導致回測股池定義不一致（有的放寬、有的用快照）。 | 拍板由 workflow 章 load_universe + research/data/universe/*.json 快照當唯一 universe 真相，backtest_cli 凍結股池傳給回測引擎。明定快照生成法（FinMind TaiwanStockInfo 含上市/下市日→反推回測窗起點存活清單的近似法），並要求使用者書面接受『近似仍可能殘留輕微 survivorship』。backtest_v2 的 meta.universe_note 標註用的是哪個 tag。data 章的『price_df 有資料』代理僅作 fallback。 |
| MAJOR | workflow 章 backtest_cli build_panel / CATALOG vs factors 章 build_context / FACTOR_REGISTRY | workflow 章 backtest_engineer 依賴 build_panel(stocks, strat, as_of, years)->panel(price_with_factors, taiex_df, errors) 與穩定的因子名清單，但 factors 章提供的是 build_context(單檔切片) + compute_factor(逐因子)。從『逐檔逐日 compute_factor』到『panel(price_with_factors_df 含 factor__* 欄)』之間的批次組裝層（build_panel）沒有 owner——backtest_v2 章吃的是『已附 factor__* 欄的 df』，但誰把因子算進 df 欄、欄名前綴是 factor__ 還是別的，未定義。 | 新增 build_panel 為地基二與地基三的交界函式（建議放 stock_strategies/research/ 或 factors/），職責：對股池逐檔、逐交易日呼叫 compute_all_factors，攤平成 price_df + factor__<name> 欄，回 panel{price_with_factors, taiex_df, errors}。明定因子欄名前綴一律 'factor__'（backtest_v2 已假設此前綴）。因子名清單由 list_factors() 提供，workflow 的 school_analyst 只能引用 list_factors() 回傳的 name，加一個測試擋未知因子名。 |
| MINOR | data 章快取選型 vs 全專案 | 快取技術選型(parquet 需 uv add pyarrow vs sqlite stdlib)在 data 章列為開放問題，但 backtest 章『回測一次抓全期』與 workflow 章 research/data/cache 都依賴此快取的讀寫介面。選型未定會讓三章的快取 IO 介面懸空。 | 拍板 parquet + pyarrow（DataFrame 往返最自然、回測重複抓必命中），uv add pyarrow 寫進 pyproject。快取讀寫只透過 data 章 fetch_finmind_cached 一個入口，其他章不直接碰快取檔，這樣即使日後換 sqlite 也只改一處。 |
| MINOR | runtime 章 sheet 擴欄 vs 現有 sheet.py 14 欄 Signals header | runtime 章說 sheet.append_signals『尾端擴 regime/chief_conclusion 兩欄』，已核對現有 sheet.py 確實是 14 欄固定 header（date..risk_notes）。但現有 append_signals 是『WorksheetNotFound 才建 header』，既有分頁不會自動補 header，runtime 章自己也把『何時補 header』列為開放問題。直接尾端加兩欄會造成舊列 14 格、新列 16 格錯位。 | append_signals 改為每次跑先讀第一列 header，若缺 regime/chief_conclusion 就 update header 並對舊資料列補空白（或一次性遷移腳本）。建議接受 runtime 自動補 header（低風險、冪等），寫進 runtime 章定案。新欄一律加在『尾端且容缺』，performance.py 只讀舊欄不受影響（已核對 performance 用 r.get(欄名) 取值，加欄安全）。 |
| MINOR | workflow 章 ai_generator 切換 vs factors/schema 章 | ai_generator.py 現有 system prompt 是舊 use_* 開關白名單（已核對 api/services/ai_generator.py 確實列 use_ma_alignment 等 bool）。factors 章與 workflow 章都要它改成『從 FACTOR_REGISTRY 挑 name+weight』+ v2 schema，但切換時機/過渡期是否雙 schema 並存散在三章未定。 | 訂 ai_generator 切換里程碑：等地基二 FACTOR_REGISTRY 與地基四 schema.py 都上線後再改 prompt，一次切到 v2（產 factors 清單），不做雙 schema 過渡（避免長期雙軌）。過渡期前端若還是扁平表單，靠 schema 章 migrate_v1_to_v2 與 merge_params 退化相容，AI 路徑先維持舊 v1 直到切換點。 |
| MINOR | backtest 章勝率口徑變更 vs evaluate/schema/runtime 門檻 | backtest_v2 含成本+同日停損悲觀假設會系統性壓低勝率，連帶 evaluate 的 signal_score 與 min_total_score_for_buy 門檻整體偏移；backtest 章自列為開放問題。runtime 章又說 runtime 不重跑回測、改讀策略檔固化的 backtest.winrate_by_regime[regime]——若固化值用新口徑、舊策略門檻沒重校，BUY 會大幅減少。 | 地基四在切 v2 時順帶用新口徑重跑現有 default/conservative 策略，據以重校 min_total_score_for_buy（或 entry.min_score）。runtime 讀 winrate_by_regime[regime] 時，缺該 regime 樣本(<10)的處理要敲定：建議 fallback 用 overall winrate 並在 expert_memo 標『該市況樣本不足』，不要視同無訊號。此 key 名 winrate_by_regime 要與 backtest 章 by_regime{bull/range/bear}.winrate 對齊（建議 runtime 直接讀 by_regime[regime].winrate，不要另立 winrate_by_regime 鍵）。 |

### §12.3 實作里程碑順序

1. M0 契約凍結（地基零，最先）：把 FactorContext、build_context/from_bundle、Factor 回傳契約(None vs 0.5)、regime_overrides 白名單與 key 名、v2 strategy schema、backtest_result schema、因子欄前綴(factor__)、regime 分段規則 這八個跨章介面收斂成單一真相文件 + stubs（context.py / schema.py 常數 / 一份 schemas/*.json）。所有後續章 import 同一份，禁止各自 redefine。
2. M1 地基一資料層：fetch_finmind_cached(parquet+pyarrow 快取+限流退避)、FinMindRateLimitError、五個 point-in-time loader(get_institutional/get_monthly_revenue/get_valuation/get_margin/get_shareholding 統一 as_of 簽名)、get_index_history、get_stock_info/get_capital_and_industry、build_context/from_bundle。先跑一支 -m live 對 2330 定稿 FinMind rename 表（解掉欄名開放問題）。
3. M2 地基二因子庫：在 M0 凍結的 FactorContext/Factor 契約上實作七派因子 + legacy 包裝 + FACTOR_REGISTRY + list_factors + build_panel(批次攤平 factor__ 欄)。含 test_lookahead 守無未來資訊。
4. M3 地基三回測引擎：regime_classify/get_regime_series_for(唯一 regime 真相)、apply_costs、backtest_v2(單檔) + aggregate_portfolio(投組聚合)、stats(sharpe/sortino/significance)、backtest 相容 wrapper。用真實 5 年資料校 regime 門檻使三市況樣本均衡。
5. M4 地基四 schema 升級：detect_version/validate_strategy 分流 v1/v2(v1 byte-identical)、merge_params(+regime)、apply_regime_overrides(唯一 owner)、migrate_v1_to_v2、PERIOD_DEFAULTS/CLAMPS/WHITELIST。敲定並寫死最終 signal_score 評分公式(composite→tech、三段權重由 period 決定)。同時用新口徑重校現有策略門檻。
6. M5 研發層 workflow：backtest_cli(薄殼，數字只來自 backtest_v2/aggregate)、load_universe(凍結股池快照 commit 進 repo)、audit_run(稽核 LLM 臆造)、六類 agent prompt + 強制 schema、strategy_factory.workflow.js。此時地基一二三四已穩定，agent 只引用 list_factors() 與真實回測數字。產出 strategies/v2/*.json。
7. M6 固化層 runtime：build_context(單日)、compute_all_factors、get_regime_today(包裝 regime_classify)、select_strategies(hybrid/all)、evaluate_v2(讀 M4 評分公式 + M3 固化的 by_regime winrate)、_apply_regime_overrides(只做系統級保底，策略級委派 M4)、expert_memo(LLM 解說 + template 降級)、sheet 自動補 header、main.py 兩段式(先 llm=False 算決策再批次補 memo)。最後接上 notify/performance(零改動)。
8. M7 前端/API v2(收尾)：API round-trip v2 讀寫、ai_generator 切到 FACTOR_REGISTRY name+weight、前端先做 v2 唯讀展示再做巢狀編輯器。legacy_signals 與舊四開關訂退場里程碑，待全 v2 填滿 factors 後整批移除。

### §12.4 主要風險

- 介面三重定義風險最高：FactorContext / build_context / Factor 回傳契約 在多章各自 @dataclass/各自簽名，若不先做 M0 契約凍結就平行開工，地基二三四會各自對著不同版本實作，整合時大改。這是本 spec 最大的單點風險。
- 回測限流與 point-in-time 的衝突：回測逐日若每天重抓會撞 FinMind 免費額度；唯一解是 build_context_from_bundle(一次抓全期、逐日純切片) + parquet 快取。若 loader 簽名沒統一帶 as_of（factors 章的 years-only 版本）會 silently look-ahead，回測數字虛高卻不報錯——這種錯最難抓。
- Survivorship bias 是『四章互相指望、無人最終拍板』的典型孤兒問題；FinMind 無歷史成分快照，只能用上市/下市日反推近似。若不在 M0 指定 owner(workflow load_universe)並讓使用者接受近似，回測 by_regime 績效會被選股池偏誤汙染，再準的因子也救不回。
- 勝率口徑切換的連鎖位移：v2 含成本+同日停損會壓低勝率→signal_score 下移→不重校門檻則 BUY 量驟減；且 runtime 直接讀固化的 by_regime winrate，新舊口徑混用會讓實盤決策與報告對不上。必須在 M4 一併重校門檻並對齊 winrate key 名。
- regime 三套實作(regime_classify / get_regime_today / get_market_state)若分段規則不一致，回測 by_regime 與 runtime regime 不同步，自適應策略在實盤觸發的 override 與回測驗證的不是同一市況，等於回測沒驗到實際行為。
- 評分模型未定(純 composite / 三段外層 / 多策略投票 三選一)：三章各自假設不同模型，這不是介面命名問題而是核心算法分歧，必須由人拍板，否則 evaluate_v2 寫出來會與回測 CLI 用的評分不一致，研發挑出的『好策略』固化後行為走樣。
- LLM 臆造數字風險：研發層 agent 可能在報告寫『回測 sharpe 1.2』但與真檔不符。workflow 章的 audit_run 是必要防線，必須在 M5 與 backtest_cli 同步落地並進 CI，否則人工挑策略時會被假數字誤導。
- 雙軌長期並存風險：legacy_signals/舊四開關、v1/v2 schema、舊 backtest/新 backtest_v2 若沒訂退場里程碑，會長期雙軌增加維護面與測試矩陣。需在 M4/M7 明訂退場條件。


---

## §13 附錄：各章開放問題彙整

> 多數已於 §4 契約凍結或 §5 待拍板決策中處理；此處彙整供實作時逐項核對。

**§6 地基一：資料層擴充（讓各路分析師有料）**

- 股本來源裁決：TaiwanStockInfo 是否含股本？若無，市值改由 TaiwanStockFinancialStatements 的普通股股本 type 推算（需確認確切 type 字串），缺則市值因子回中性 None。請拍板是否接受『缺股本→市值因子中性』。
- ★ FinMind 各 dataset 實際欄位名需以真實回傳校正：法人 name 列舉值（Foreign_Investor/Investment_Trust/Dealer_self/Dealer_Hedging?）、TaiwanStockPER 是 PER/PBR/dividend_yield 還是小寫、融資券餘額欄名、TaiwanStockShareholding 的外資持股欄名與更新頻率。建議排一支 -m live 測試對 2330 跑一次定稿 rename 表。
- 月營收 avail_date 採『次月10日』保守估是否足夠？少數公司提前公布或延後，會造成輕微偏差；若要更精準需另抓公告日資料源（FinMind 無直接欄位）。請確認以法規上限次月10日為準可接受。
- 快取技術選型：本章選 parquet（需 uv add pyarrow）。若想避免新依賴可改 sqlite（stdlib）。請拍板 parquet vs sqlite——影響地基二/回測章共用的快取讀寫介面。
- 下市股快取保留策略涉及 survivorship bias：是否要建立『歷史曾上市清單』以正確還原各時點 universe？本章先以『該日 price_df 有資料』作代理，若回測章需要更嚴謹的歷史成分股，需跨章協調額外資料源。
- lookback_years 預設 5 年 vs 近況判斷 1~2 年的切分：本章把『回測窗 5 年』與『近況因子自取近窗』分離，須與『回測引擎章』對齊 OOS 切分點，避免兩邊各自定義窗長。

**§7 地基二：因子庫（把專家判斷量化成可回測因子）**

- 除權息還原股價：FinMind 是否提供還原股價（adj close）？momentum.* / breakout.* 用未還原價在除權息日會有一次性跳空噪音。需地基一拍板要不要在 build_context 統一供還原價，或在因子層用報酬率規避。
- EPS 單季資料來源：get_fundamental 目前只回年度 EPS/ROE。單季 eps_q 是要從 TaiwanStockFinancialStatements 逐季抽取，還是另抓 FinMind 的單季財報 dataset？需地基一確認可用欄位與財報公布日（deadline 推算是否夠保守）。
- FinMind dataset 正式名稱與額度：新增的 5 個資料源（法人/月營收/PER/融資券/外資持股）實際 dataset id 與回傳欄位需在實作前用 finmind 技能驗一次；且每檔多抓 5 個 dataset 會否撞免費額度限流，需評估是否要本地快取（影響回測一次抓全期的可行性）。
- composite 與舊 evaluate 的接管邊界：composite*100 餵 min_total_score_for_buy 後，舊三段加權(fund0.3/tech0.3/bt0.4)如何與新因子層共存？是並存兩路徑，還是地基四把 tech_score 整段換成因子 composite？需與地基四 evaluate 章對齊。
- survivorship bias 的 universe：因子層不選股池，但回測 universe 必須是『歷史該日實際上市清單』。此責任歸屬 universe/回測章，需確認由誰提供 point-in-time 成分股，否則因子算得再準也會被選股池偏誤污染。
- AI 生成器切換時機：ai_generator.py 的 system prompt 要從舊 use_* 開關改成『從 FACTOR_REGISTRY 挑 name+weight』。何時切換、是否需要過渡期同時支援兩種 schema，需跨章（前端/API）協調。
- 因子 params 命名空間：factor_params 是全策略共用一份，還是每因子各自帶 params？目前設計共用一份（如 pb_window、box_n 互不衝突），若未來不同流派同名 param 衝突需引入命名空間，先標記。

**§8 地基三：回測引擎升級（讓「準」可被驗證）**

- 勝率口徑變更：v2 含交易成本且採『同日停損優先』的悲觀假設，舊策略勝率會系統性下降，連帶 evaluate 的 signal_score 與 BUY/WATCH 門檻可能整體偏移。是否需要 (a) 重新校準 min_total_score_for_buy 等門檻，或 (b) 加 legacy=True 開關讓過渡期沿用舊樂觀算法？需使用者拍板。
- regime 門檻參數（ma_fast=20/ma_slow=60/slope_win=20/slope_eps=0）是否要用近 5 年台股實際資料反覆校準到三市況樣本量大致均衡？波動率 vol 是否要正式參與分類（目前僅保留欄位）？建議地基四先用預設值跑、之後用研發 workflow 調。
- 無風險利率：Sharpe/Sortino 目前設 rf=0。台股是否要改用某固定年化（如 1.5%）？影響不大但需一致口徑。
- 交易成本最低手續費 20 元低消：目前不折進報酬率（僅 meta 標註）。若要對小資金部位更真實，需要引擎知道『假設部位金額』才能折算——是否引入 assumed_position_size 參數？跨章（部位管理）協調。
- survivorship：下市股的歷史價格 FinMind 是否完整可取？若 TaiwanStockPrice 對已下市 data_id 不回資料，需要地基二補一份下市清單/價格來源；否則 survivorship 只能『盡力而為』並在 meta.universe_note 誠實標註限制。需跨章(資料層/universe)協調。
- 重疊交易策略：目前預設『單檔同時只持一張、持有期內忽略後續訊號』以利串權益曲線。若某些策略本意是金字塔加碼或多筆並行，這個假設會低估。是否需要 portfolio 級回測（多檔資金分配）當地基五？本章先做單檔，標記為後續。
- performance.py 的 hit_target/hit_stop 判定是否要改成與 _settle_one 同口徑（同日停損優先），讓 runtime 成績單與回測一致？目前成績單用樂觀口徑，兩者並存會造成輕微不一致。

**§9 地基四：策略 Schema 升級 + Regime 自適應（讓策略不再是扁平 20 格）**

- regime 來源過渡期：地基三的 regime_classify 尚未上線前，runtime 用 market.py 的二元 get_market_state。需確認映射：bullish=True 一律當 bull 還是依月線斜率細分 bull/range？建議空窗期 True→不套 override（等同 base，最安全），False→bear（套暫停/縮停損）。需與地基三章拍板。
- survivorship bias 與 universe 回測：FinMind TaiwanStockInfo 無歷史成分快照，無法精準還原『過去某日的市值/產業/是否仍上市』。本章採『回測階段放寬 universe 過濾、僅 runtime 選股套用』並在報告標註。是否需要引入下市股清單或第三方歷史成分？需與回測 v2 章協調。
- 因子權重 vs 三段權重共存：v1 有 weight_fundamental/technical/backtest 三段加權，v2 改用 factor 加權但 evaluate.py 的最終 signal_score 仍是三段加權（fund*wf+tech*wt+bt*wb）。v2 是要保留三段外層加權（factors 只決定 tech_score 內部），還是整個改成單層 factor 加權？本章假設『保留三段外層、factors 決定 tech 內部、三段權重在 v2 用固定 0.3/0.3/0.4 或由 period 決定』，需與評估章確認。
- 前端是否本期支援 v2 編輯：v2 是巢狀結構，現行前端表單是扁平。本章只保證 loader/API 吃得下 v2 並可 round-trip 存讀，v2 視覺化編輯器屬前端章。需確認是否需要 schema_v2_defaults() 給前端先做唯讀展示。
- legacy_signals 退場時機：過渡期 factors 為空時 fallback 四開關。一旦地基一因子引擎上線且所有 v2 都填 factors，legacy_signals 與四開關是否整批移除？需訂退場里程碑避免雙軌長期並存。

**§10 研發層：多專家 Workflow 腳本（規則化專家 + LLM 設計者，回測數字一律來自確定性 CLI）**

- 回測引擎 backtest_v2 的彙整粒度需與第 4 章拍板：本章假設 CLI 對股池逐檔回測後彙整成『投組層級』的 overall/by_regime/is/oos（含 cagr/max_drawdown/sharpe）。若第 4 章 backtest_v2 只回單檔結果，則彙整邏輯該放 CLI 還是引擎需確認，且 sharpe/max_drawdown 的投組計算方式（等權？訊號日資金分配？）要對齊。
- OOS 切分方式需第 4 章定義：是時間序列尾段切 OOS（如最後 20%）還是 walk-forward？本章 REJECT 的 is_oos_sharpe_gap 與 oos_sharpe 依賴此定義。建議用時間尾段 oos_split 預設 0.2。
- 因子庫 CATALOG 與 build_panel 介面屬第 2 章：本章 school_analyst 只引用 factors[].name，需第 2 章提供穩定的因子名清單（value/momentum/chips/revenue_momentum 各派至少 4-5 個可用因子）與 build_panel(stocks, strat, as_of, years)->panel(含 price_with_factors, taiex_df, errors) 的確切回傳結構。
- 凍結股池快照 twse_listed_2021_q2.json 的取得方式需拍板：FinMind 是否有歷史時點的上市清單 dataset（TaiwanStockInfo 含上市/下市日）？若無，需用『今日清單 + 各檔上市日/下市日反推回測窗起點存活清單』的近似法，這仍可能殘留輕微 survivorship，要使用者接受此近似。
- loader.py 的 v2 相容映射表屬第 5 章：chief 寫出 params 扁平區塊時，v2 的 entry.min_score(0..1) 要如何映射回舊 min_total_score_for_buy(0..100)、factors 加權如何退化成舊四開關，需第 5 章給確切對照，否則固化層讀 v2 策略會行為不一致。
- run_id 命名與並行衝突：目前 run_id 為腳本頂部手改字面量（符合 meta 純字面量規範）。若使用者希望一天多輪，需約定命名規則（如 -a/-b 後綴），並確認是否要在 audit_run 內擋重複 run_id 覆寫。
- 研發期 universe 規模 vs FinMind 限流：整個上市股池逐檔抓 5 年資料 + 多 dataset，量很大可能觸發 FinMind 額度。需拍板研發期是否預設 --max-stocks 限縮（如 200 檔代表性樣本）或加本地快取層（research/data/cache/），以及快取是否影響 no-look-ahead 保證。

**§11 固化層：每日多專家 Pipeline（把研發邏輯搬進 main.py）**

- 回測勝率來源：runtime 不重跑回測，改讀研發層固化進策略檔的 backtest.winrate_by_regime[regime]。需與『回測引擎章』與『策略 schema 章』敲定該欄位精確 key 名與缺值（某 regime 無樣本）時的處理（用 overall 還是視同無訊號？）。
- 策略選用預設模式：本章建議 hybrid（路由候選+投票），但策略庫初期可能只有 2-3 檔。需使用者拍板：庫小時是否自動退化成 all（取最高分），門檻設幾檔？
- 產業別來源：ctx.industry 目前打算用 watchlist 的 category 欄（notify 已在用）。若要更精準的 FinMind 產業分類，需新增一支抓 industry 的 data 函式並決定快取策略——是否值得？
- regime 與 market.get_market_state（站上月線）兩個濾鏡語意重疊。本章設計為『market_state 管硬降級、regime 管軟調整與選股』並存。需審查官確認不要二擇一、確實要疊加。
- Gemini 成本與額度：預設每日上限 30 次 LLM 呼叫。實際 watchlist 規模與 BUY/WATCH 比例需使用者提供，以校準 memo_max_calls / memo_max_watch。是否需要改用 Claude/其他模型由使用者決定（目前沿用既有 Gemini 基建）。
- sheet 擴欄相容：對『既有 14 欄 Signals 分頁』補兩個 header 的時機（每次跑都檢查 vs 一次性遷移腳本）。需確認是否接受 runtime 自動補 header，或要求手動遷移。
- LLM memo 與夜盤/大盤降級的時序已定為『先濾鏡降級再生成 memo』。需確認 main.py 兩段式（先全部 llm=False 算決策、排序後再批次補 memo）是否可接受多一輪迴圈的實作複雜度。

