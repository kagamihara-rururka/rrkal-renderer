# MVP Architecture (Taiwan-first, declarative by default)

## 1. 宣告式核心
本系統以 `specs/*.json` 為單一真實來源（SSOT）

1. `platform`：全域執行參數、目標股票清單、預設 provider 參照
2. `market`：市場設定（時區、交易時段、交易日）
3. `provider`：資料源能力宣告（mock / yfinance / twse）
4. `pipeline`：欄位衍生流程（ema、rsi、lag/shift、cross）
5. `strategy`：入場/出場條件規則
6. `risk`：部位與風險上限
7. `execution`：下單/模擬參數（手續費、滑價、部位限制）

同目錄可承載多個 `platform`，透過 CLI 的 `--platform-id` 切換（例如 `tw_platform_v1`、`us_platform_v1`）。

## 2. Planner
`planner.py` 將 spec 轉成可執行計畫：

1. `provider.fetch`
2. `pipeline.*`（依 spec 宣告）
3. `strategy.evaluate`
4. `risk.apply`
5. `execution.paper`

程式沒有硬編交易邏輯，行為由 spec + runtime 實作決定。

## 2.5 registry 層（市場 / Provider）

- `platform.market.provider_registry_id` 指向 `kind: registry` spec。
- registry 會定義每個市場可用 provider、預設 provider、fallback chain 與別名（例如 `mock`）。
- 執行時會先讀 registry 決策，再由 `provider` spec 套出具體 adapter。

- 未來跨市場擴充時，只要新增/更新：
  - `market` spec（台灣、市場參數）
  - `registry` spec（該市場可用 provider 與預設策略）
  - `provider` spec（資料供應者 adapter 宣告）

即可不改 runtime 核心程式。

## 3. Runtime（執行責任）
`runtime.py` 主要職責：

1. 依 spec 讀取資料
2. 執行資料品質 gate
3. 套 pipeline 計算指標（ema/rsi/lag/cross）
4. 套 strategy 條件做進出場（支援 expression DSL）
5. 套 risk 規則（倉位、回撤、最大日交易）
6. 輸出 `intent` / `plan` / `evidence`（`artifacts`）與 trace id

新增收斂項目：
- `row` 質量檢查：時間戳、價格欄位、`high/low` 邏輯、負值、重複
- 風險事件輸出：`skip_entry`, `force_exit`, `quality_gate_drop`, `max_trades_per_day`
- 以可回溯 evidence 保留 `events`、`trades`、`equity_curve`、`quality`
- 輸出 artifact：JSON、Trades/Equity CSV、Events JSONL
- 標準化 `events` / `trades` / `equity_curve` 欄位並新增 `schema_version`, `run_id`, `run_metadata`

## 4. Provider 分層
`data_providers.py` 的資料行為由 provider spec 決定：

1. `provider_type: mock`
2. `provider_type: yfinance`
3. `provider_type: twse`

`runtime` 可在主 provider 失敗時依 `platform.fallback_provider` fallback。

## 5. MVP 目標與交付
先收斂台股路徑，確保：
1. 可切換資料源
2. 可重複執行
3. 有品質與風控報告
4. 可繼續抽象到跨市場架構
