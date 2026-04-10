# 📈 台股每日選股機器人

> **基本面 × 技術面 × 歷史回測** — 全自動掃描、評分、推播  
> 每天收盤後自動跑，Telegram 收通知，Google Sheet 存紀錄  
> 零伺服器成本，GitHub Actions 免費跑

[![GitHub Actions](https://img.shields.io/badge/自動排程-GitHub_Actions-2088FF?logo=github-actions&logoColor=white)](#-github-actions-自動排程)
[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)](#)
[![Telegram](https://img.shields.io/badge/通知-Telegram_Bot-26A5E4?logo=telegram&logoColor=white)](#-telegram-通知範例)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## 這是什麼？

一個 **單檔 Python 腳本**，幫你每天自動做三件事：

```
Google Sheet 股票池 → 跑策略評分 → Telegram 推播 + Sheet 紀錄
```

你只需要維護一張 Google Sheet 的觀察清單，系統每天台股收盤後自動：

1. **抓資料** — 透過 FinMind API 取得基本面財報 + 日 K 線
2. **跑策略** — 基本面篩選 → 技術面評分 → 3 年歷史回測
3. **發通知** — Telegram 推播買進/觀察訊號，附完整進出場價位
4. **存紀錄** — 結果寫回 Google Sheet，累積歷史追蹤

**不用租伺服器、不用學框架、不用碰資料庫。** Fork 這個 repo，設好環境變數，就會自動跑。

---

## 📱 Telegram 通知長這樣

**第一則 — 市場總覽**

```
📊 V3.0 每日選股報告 2026/04/09
掃描 15 檔 | BUY 2 | WATCH 5 | SKIP 8

🌡️ 市場氛圍
🟢 偏多 — 多數標的上漲且站穩月線，可積極佈局
池內均漲 +1.8% | 10/15 檔上漲 | 11/15 檔站上月線

📡 類股強弱排名
🔥 CPO (3檔) 5日均漲+4.2% | BUY 1 WATCH 1
📈 機器人 (2檔) 5日均漲+1.5% | BUY 1 WATCH 0
📉 重電 (2檔) 5日均漲-0.8% | BUY 0 WATCH 1
```

**第二則 — 個股詳情**

```
🟢 BUY — 建議進場 (2)

2308 台達電  綜合 72 分
🔥 5日+3.8% | 20日+8.2% | 距高點-5% | 站上月季線 | 量能放大
進場 1660 → 停損 1527.2 / 目標 1826
風報比 1:1.25 | 建議部位 20%
基本面✅ | 技術分 75 | 勝率 68% (12次)
觸發: 均線多頭, KD黃金交叉, MACD多頭
💡 為何買: 所有條件皆達標
```

**第三則 — 操作建議**

```
🧠 今日操作建議

🔑 最值得關注
• 2308 台達電 (BUY, 72分)
  技術面出現均線多頭/KD黃金交叉/MACD多頭，帶量上攻，多頭排列
  若進場: 進 1660 → 損 1527.2 / 標 1826

📌 操作方向
• 市場偏多，可挑選技術面強勢股分批進場
• 優先選回測勝率>60%、站穩月線的標的
```

> 通知分三則推送：市場總覽 → 個股詳情 → 操作建議，手機閱讀友善。

---

## ⚡ 5 分鐘部署

### 前置準備

你需要準備四組免費的 API / 帳號：

| 服務 | 用途 | 取得方式 |
|------|------|----------|
| [FinMind](https://finmindtrade.com/) | 台股財報 + K 線資料 | 免費註冊，拿 API token |
| [Telegram Bot](https://t.me/BotFather) | 推播通知 | 找 @BotFather 建 bot |
| [Google Sheet](https://sheets.google.com) | 股票池 + 訊號紀錄 | 建一張空的 Sheet |
| [GCP Service Account](https://console.cloud.google.com/) | 程式讀寫 Sheet 的權限 | 建 SA，下載 JSON 金鑰 |

### Step 1：Fork & Clone

```bash
# Fork 這個 repo 到你的 GitHub，然後
git clone https://github.com/<你的帳號>/stock-strategies-only.git
cd stock-strategies-only
```

### Step 2：建立 Google Sheet

建一張新的 Google Sheet，第一個分頁命名為 **`Watchlist`**，欄位如下：

| stock_id | name | category | enabled |
|----------|------|----------|---------|
| 2330 | 台積電 | AI | TRUE |
| 2308 | 台達電 | CPO | TRUE |
| 2049 | 上銀 | 機器人 | TRUE |

- `stock_id` — 台股代號
- `category` — 自訂類股分類（用於類股強弱排名）
- `enabled` — 設 `FALSE` 可暫停追蹤，不用刪除

> `Signals` 分頁不用手動建，程式第一次跑會自動建立。

記下 Sheet ID（網址中 `https://docs.google.com/spreadsheets/d/【這段】/edit`）。

### Step 3：設定 Google Service Account

1. 到 [Google Cloud Console](https://console.cloud.google.com/) 建專案
2. 啟用 **Google Sheets API** 和 **Google Drive API**
3. 建立 **Service Account**，下載 JSON 金鑰
4. 把 JSON 裡的 `client_email`（長得像 `xxx@xxx.iam.gserviceaccount.com`）加到你的 Google Sheet 共用權限（**編輯者**）

### Step 4：設定 Telegram Bot

1. Telegram 搜尋 **@BotFather**，輸入 `/newbot` 建立機器人，拿到 `BOT_TOKEN`
2. 搜尋 **@userinfobot**，拿到你的 `CHAT_ID`

### Step 5：設定 FinMind

到 [finmindtrade.com](https://finmindtrade.com/) 免費註冊，登入後在個人頁面取得 API Token。

### Step 6：本機測試

```bash
# 安裝 uv（如果還沒有）
curl -LsSf https://astral.sh/uv/install.sh | sh

# 安裝依賴
uv sync

# 複製環境變數範本，填入你的值
cp .env.example .env
# 編輯 .env，填入上面拿到的各組 token

# 跑一次看看
uv run python v3_daily_signal.py
```

成功的話，你的 Telegram 會收到選股通知，Google Sheet 的 Signals 分頁會出現新資料。

### Step 7：GitHub Actions 自動排程

到你 fork 的 repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**，加入五個 secret：

| Secret 名稱 | 值 |
|---|---|
| `FINMIND_TOKEN` | 你的 FinMind API token |
| `TELEGRAM_BOT_TOKEN` | 你的 Telegram Bot token |
| `TELEGRAM_CHAT_ID` | 你的 Telegram Chat ID |
| `GOOGLE_SHEET_ID` | 你的 Google Sheet ID |
| `GOOGLE_CREDS_JSON` | Service Account JSON **整串貼進去** |

把 `daily.yml` 搬到正確位置：

```bash
mkdir -p .github/workflows
cp daily.yml .github/workflows/daily.yml
git add . && git commit -m "setup: enable daily workflow" && git push
```

到 **Actions** 分頁點 **Run workflow** 手動跑一次測試。沒問題後，每個交易日**台灣時間 14:30** 會自動執行。

> GitHub Actions 免費額度：Private repo 每月 2000 分鐘，這個 workflow 每次約 2 分鐘，每月最多跑 22 天（交易日）= 44 分鐘，完全免費。

---

## 🧠 選股策略解析

### 評分公式

```
綜合分 = 基本面 × 30% + 技術面 × 30% + 回測勝率 × 40%
```

| 綜合分 | 動作 | 條件 |
|--------|------|------|
| ≥ 65 | 🟢 **BUY** | 基本面通過 + 技術分 ≥ 50 + 綜合分 ≥ 65 |
| ≥ 50 | 🟡 **WATCH** | 接近但未全過 |
| < 50 | ⚪ **SKIP** | 不符合 |

### 基本面篩選

```python
# 近 3 年每年都要達標
EPS > 5.0   # 每股盈餘
ROE > 15%   # 股東權益報酬率
```

通過 = 100 分，未通過 = 40 分。這是成長股篩選的基本門檻，過濾掉體質差的公司。

### 技術面評分（0-100）

四大指標各 25 分：

| 指標 | 滿分條件（25 分） | 部分得分 |
|------|-------------------|----------|
| **均線排列** | 收盤 > MA20 > MA60（多頭排列） | 收盤 > MA20（12 分） |
| **布林通道** | 貼近下軌反彈（距下軌 < 3%） | 收盤在中軌下方（10 分） |
| **KD 指標** | K > D 且 K < 80（黃金交叉未過熱） | K > D（10 分） |
| **MACD** | 柱狀 > 0 且 DIF > DEA | 柱狀 > 0（10 分） |

### 歷史回測

對過去 3 年所有技術分 ≥ 60 的交易日，模擬：
- 以**收盤價進場**
- 持有 **20 個交易日**
- **+10% 停利** / **-8% 停損** / 到期以收盤結算

```
回測分 = 歷史勝率 × 100
```

> ⚠️ 回測樣本 < 8 次時系統會標註「統計弱」，這種勝率參考就好。

### 風險管理

每筆交易自動計算：

```
停損價 = 進場價 × (1 - 8%)
目標價 = 進場價 × (1 + 10%)
風報比 = 10% / 8% = 1.25
建議部位 = min(2% / 停損%, 20%) = 25%（上限 20%）
```

---

## 🔧 自訂你的策略

### 調整參數

修改 `v3_daily_signal.py` 頂部的 `CONFIG`：

```python
CONFIG = {
    "eps_threshold": 5.0,       # EPS 門檻，降低可納入更多股票
    "roe_threshold": 15.0,      # ROE 門檻
    "backtest_years": 3,        # 回測年數
    "hold_days": 20,            # 持有天數（交易日）
    "target_return": 0.10,      # 停利 10%
    "stop_loss": 0.08,          # 停損 8%
    "min_tech_score_for_signal": 60,  # 回測取樣的技術分門檻
    "min_total_score_for_buy": 65,    # BUY 的綜合分門檻
}
```

### 改寫策略的幾個方向

**加入新技術指標（以 RSI 為例）**

```python
# 在 add_indicators() 裡新增
delta = df["close"].diff()
gain = delta.where(delta > 0, 0).rolling(14).mean()
loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
df["rsi"] = 100 - (100 / (1 + gain / loss))

# 在 tech_score_at() 裡新增評分邏輯
if pd.notna(row.get("rsi")) and 30 < row["rsi"] < 70:
    score += 20
    signals.append("RSI 中性區")
```

**調整評分權重**

```python
# evaluate() 裡的綜合分公式
signal_score = round(
    0.3 * fund_score +   # 基本面 30%
    0.3 * tech_score +   # 技術面 30%
    0.4 * bt_score,      # 回測 40%（目前權重最高）
    1
)
# 想更重視技術面？改成 0.2 / 0.5 / 0.3
```

**週期股策略**

預設策略適合成長股（台達電、上銀這類）。週期股（面板、記憶體、航運）建議：
- 降低或跳過 EPS/ROE 門檻
- 改看營收年增率
- 加入產業景氣指標

---

## 📐 系統架構

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│ Google Sheet │────▶│  Python 腳本  │────▶│  Telegram   │
│  (Watchlist) │     │              │     │  (通知推播)  │
└─────────────┘     │  1. 讀觀察清單 │     └─────────────┘
                    │  2. FinMind API│
┌─────────────┐     │  3. 策略評分   │     ┌─────────────┐
│   FinMind   │────▶│  4. 歷史回測   │────▶│ Google Sheet │
│ (財報 + K線) │     │  5. 發通知     │     │  (Signals)  │
└─────────────┘     └──────────────┘     └─────────────┘
                           │
                    ┌──────┴──────┐
                    │ GitHub Actions│
                    │ 每日 14:30   │
                    │ 自動觸發      │
                    └─────────────┘
```

---

## 🗂️ 檔案結構

```
stock-strategies-only/
├── v3_daily_signal.py     # 主程式（選股引擎 + 通知 + Sheet 讀寫）
├── pyproject.toml         # Python 依賴管理（uv）
├── uv.lock                # 鎖定版本
├── daily.yml              # GitHub Actions workflow（複製到 .github/workflows/）
├── .env.example           # 環境變數範本
└── README.md
```

---

## ❓ FAQ

<details>
<summary><b>FinMind 免費帳號有請求限制嗎？</b></summary>

有，免費帳號每天約 600 次請求。每檔股票需要 2 次（財報 + K 線），所以觀察清單 **300 檔以內**不會超限。一般散戶追蹤 10-50 檔完全沒問題。

</details>

<details>
<summary><b>可以用其他資料源取代 FinMind 嗎？</b></summary>

可以。只需要改 `fetch_finmind()`、`get_price_history()`、`get_fundamental()` 三個函式，回傳格式一樣就行。常見替代：[TWSE OpenData](https://openapi.twse.com.tw/)、Yahoo Finance（需額外套件）。

</details>

<details>
<summary><b>GitHub Actions 要錢嗎？</b></summary>

Private repo 每月免費 2000 分鐘，這個 workflow 每次約 2 分鐘，每月最多跑 22 天 = 44 分鐘，完全免費。Public repo 更是無限制。

</details>

<details>
<summary><b>想用 LINE Notify 而不是 Telegram？</b></summary>

改寫 `send_telegram()` 函式，換成 LINE Notify API 即可，核心邏輯完全不用動。

</details>

<details>
<summary><b>回測勝率可信嗎？</b></summary>

看樣本數。系統會自動標註：
- **< 8 次**：基本無統計意義
- **8-15 次**：僅供參考
- **> 20 次**：相對可信，但仍不保證未來表現

回測用的是固定停損停利結算，對趨勢型成長股比較適用。

</details>

<details>
<summary><b>怎麼用 AI 助手管理觀察清單？</b></summary>

如果你有接 Google Sheets MCP（如 Claude Desktop），可以直接用對話操作：

> 「把 2330 台積電加進 watchlist，category 放 AI」  
> 「先把 3081 聯亞停掉，還沒反轉」

下次排程自動納入或排除。

</details>

---

## 🛣️ Roadmap

- [ ] 類股強弱前置過濾 — 順風類股才出訊號
- [ ] 資金控管模組 — 追蹤總部位曝險
- [ ] 追蹤停利 — 已進場部位的每日監控
- [ ] 週期股策略 — 營收年增率 + 景氣指標
- [ ] 週報自動生成 — 每週日回顧本週訊號表現

---

## 📄 License

[MIT License](LICENSE) — 自由使用、修改、散佈。

---

## ⚠️ 免責聲明

本專案僅供**學習與研究**用途。程式產生的訊號**不構成投資建議**。股票投資有風險，任何交易決策請自行判斷並承擔責任。過去的回測表現不代表未來的獲利保證。
