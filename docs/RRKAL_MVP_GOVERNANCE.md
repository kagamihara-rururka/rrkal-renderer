# RRKAL RenderKit MVP Governance

採用 RRKAL 原則展開本工具治理規範：

## R — Requirements
- 每次變更都要對應 `RRKAL_Renderer` 需求與驗收標準。
- 任何渲染流程必須有輸入 artifact schema 檢查。
- 不可新增無 evidence/可追溯紀錄的輸出行為。

## R — Risks
- 風險：schema 版本變動、欄位缺漏、資料體積過大、輸出遺漏事件。
- 對策：版本欄位檢查、容錯欄位對應、分批載入與分頁輸出。

## K — Keep / Known / Ask / Learn
- Keep: 獨立於 RRKAL 核心執行邏輯，只負責 artifact 轉譯。
- Known: 先支援 `2.0.0` schema 與 JSON artifact。
- Ask: 資料欄位變更與視覺化需求時，同步更新本目錄 `docs/specs.md` 與本文件。
- Learn: 每次版本更新都輸出一份渲染驗證摘要，歸檔到 `artifacts/checkpoints`。

## A — Assurance / Audit
- Validate：CLI `validate` 先檢查必要欄位。
- Render：`render` 產生 `report.md`、`report.html`。
- Audit：保留 `artifacts/run_id`、`schema_version`、輸出時間。

## L — Lifecycle
- 規格與命令參數採 SSOT：本 repo 的 `docs/specs.md`。
- 每次交付後更新 `docs/MVP_CHECKPOINT.md`。
