# MVP Checkpoint

## 当前進度（Taiwan-first Declarative MVP）

- [x] Spec + CLI 入口完成
  - `python -m trading_platform.cli validate --spec-dir specs`
  - `python -m trading_platform.cli plan --spec-dir specs`
  - `python -m trading_platform.cli run --spec-dir specs`
- [x] Planner 以 `platform/pipeline/strategy/risk/execution` 產生執行節點
- [x] 三種 provider adapter 可切換
  - mock
  - yfinance
  - twse
- [x] Runtime 回測輸出 `intent`、`plan`、`evidence`
- [x] data quality gate + quality report（MVP 收斂項）
- [x] 風險門檻擴充：最大回撤、最大日交易次數
- [x] Strategy DSL 落地，可直接在 spec 寫可讀條件式邏輯
- [x] Pipeline 可宣告運算：`lag/shift`、`cross`
- [x] 回測結果可輸出 JSON、Trades CSV、Equity CSV、Events JSONL
- [x] Market/provider registry 已收斂（market 對應 provider allowlist / fallback）
- [x] Output schema 標準化（schema_version / events / trades / equity / summary）
- [x] 加入多平台選擇（`--platform-id`）與 `us_platform_v1` 美國範本
- [x] 修正 runtime 主迴圈縮排與事件路徑，`execute_backtest` 還原為逐筆 `working` 內部進出場邏輯，並保留 evidence 追蹤

## MVP 邊界（第一階段）

- 平台目標為台股，收斂為 daily/1d 時間足資料
- 使用 mock 與真實資料源可切換
- 不保證：多帳號、多市場、事件驅動即時下單

## 下一步

1. 事件/指標監控儀表板（dashboard）
2. 交易入口（paper/realtime 分層）
- [x] runtime.execute_backtest 控制流已修正縮排，恢復 `for row in working` 內部進出場邏輯
- [x] 事件模型統一為標準欄位（`event_type`,`event`,`symbol`,`timestamp`,`details`）
- [x] 多平台 + registry 流程與 US 範本保留
