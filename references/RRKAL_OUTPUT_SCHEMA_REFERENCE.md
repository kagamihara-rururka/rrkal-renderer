# Run Artifact Schema (MVP v2)

這份 schema 定義 `runtime.execute_backtest()` 回傳與 CLI `--output` 檔案的結構（宣告式 MVP）。

## 1) Root

- `schema_version`: artifact schema version（目前 `2.0.0`）
- `intent`: 執行意圖，包含 `market_id` / `provider_id` / `strategy_id` 等
- `plan`: planner 產出的節點（含 provider fetch / pipeline / strategy / risk / execution）
- `artifacts`: 與 `evidence` 相同參照，主要實際 payload
- `evidence`: `artifacts` 的別名，保留相容

## 2) Artifacts / Evidence

欄位固定（MVP）：

- `schema_version`: `2.0.0`
- `generated_at`: UTC ISO timestamp
- `run_id`: 唯一執行 ID
- `run_metadata`: 與這次執行相關的 metadata
  - `platform_id`
  - `market_id`
  - `provider_id`
  - `fallback_provider_ids`
  - `strategy_id`
  - `risk_id`
  - `execution_id`
- `events`: 事件陣列
  - 每筆至少含：
    - `event_type`
    - `event`（相容舊欄位）
    - `symbol`
    - `timestamp`
    - `details`（dict）
- `trades`: 交易陣列
  - 每筆欄位：
    - `symbol`
    - `direction`
    - `quantity`
    - `entry`
    - `exit`
    - `pnl`
    - `start_ts`
    - `end_ts`
    - `entry_cost`
    - `exit_cost`
- `equity_curve`: 每日資產快照陣列
  - `symbol`
  - `timestamp`
  - `run_id`
  - `cash`
  - `position`
  - `price`
  - `equity`
  - `drawdown`
  - `equity_peak`
- `summary.run`: 全域總結
- `summary.symbols`: 各標的總結
- `quality`: 每個標的品質 gate 報告

## 3) 兼容性

- 先前版本使用的 `evidence` 仍可用，但實際欄位統一到上述 schema。
- CLI 匯出 `--trades-csv`、`--equity-csv`、`--events-jsonl` 對應 `artifacts` 的 `trades`、`equity_curve`、`events`。
