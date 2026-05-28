# RRKAL RenderKit

## 專案目標
- 目標：建立獨立的 RRKAL 渲染工具（RRKAL RenderKit），專責將 RRKAL 執行結果（artifact）轉為可讀報告與圖表輸出。
- 適用：回測結果 `evidence/run artifact`（JSON）轉盤點、風險事件、進出場、績效表。

## 核心能力
- 載入 RRKAL artifact（必須包含 `schema_version`、`evidence`）
- 產生 Markdown / HTML 報告
- 產生 CSV 匯出（trades、equity_curve、events）
- 畫面快速摘要（最終績效、總交易筆數、最大回撤、風險事件）

## 參考治理
- 本專案採 RRKAL 的治理模式：`RRKAL_MVP_GOVERNANCE.md`（R R K A L）
