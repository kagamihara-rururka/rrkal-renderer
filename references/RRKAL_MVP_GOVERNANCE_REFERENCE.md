# RRKAL MVP Governance (Taiwan MVP)

RRKAL 是本專案開發治理最小邊界，適用於每一次 spec 或資料流變更。

## R — Requirements
- 每次變更都要映射到可追溯目標
  - 目標可在 `specs/*` 或 issue 描述中明確定義
- 交易流程不得變更為無對應 evidence 的隱式邏輯

## R — Risks
- 風險項目需進入 `risk` spec 或 runtime 防護：
  - 單筆部位上限
  - 回撤上限
  - 日交易次數
  - 重複時間戳 / 資料品質
- Provider 失敗需 fallback，避免回測中斷

## K — Keep / Known / Ask / Learn
- Keep: 現階段仍為 paper backtest，不對接實盤下單
- Known: 台股為第一階段重點，其他市場預留 `provider` 抽象層
- Ask: 風險規則變更時先同步更新 `docs` 與 `specs`
- Learn: 每次回測保留 evidence（events/equity/quality/trades）作為下一輪決策依據

## A — Assurance / Audit
- `validate`：檢查 spec 可載入
- `plan`：輸出執行節點順序
- `run`：輸出 evidence 與 summary，含品質報告與 drawdown
- Artifact：可另行輸出 `--trades-csv`、`--equity-csv`、`--events-jsonl` 形成可追溯檔案；run 會輸出 `schema_version` / `artifacts` / `run_metadata`，便於審計與跨平台接續。

## L — Lifecycle
- 規格變更遵守 SSOT：`specs/*.json`
- 交付節點以 `docs/MVP_CHECKPOINT.md` 紀錄更新狀態
- 任何可追溯性不足或資料品質掉隊，須先補 evidence 再進入下一步
