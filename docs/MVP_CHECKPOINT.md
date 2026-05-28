# MVP Checkpoint

## 狀態

- [x] 建立 RRKAL RenderKit 專案骨架（K槽）
- [x] 引入 RRKAL 參照文件（architecture/output/schema/governance）
- [x] 建立 `validate` 與 `render` CLI
- [x] 實作 Markdown/HTML 輸出模板
- [x] 將 HTML 優化為可互動 photo-style 檢視（篩選、事件 Chip、PnL 排序）
- [x] 加入批次預渲染 `render-batch`
- [x] HTML 輸出加入簡版 equity 圖與交易清單
- [x] 支援 trades/events/equity 的 CSV 與 events JSONL 匯出
- [x] 支援壓縮包輸入（jsonl / zip）
- [x] 支援 equity SVG 輸出（`equity_curve.svg`）
- [x] 支援 PDF 輸出
- [x] 強化報表互動：事件/交易列選取時，會同步將 equity 圖游標對位到最接近時間點
- [x] 在 `render_summary.json` 的 `render_settings` 紀錄 `html_row_cap`，方便 RRKAL 追蹤 HTML inspector 行數策略
- [x] 點擊 equity 點位時同步最近事件與最近交易列，增加事件/交易時間軸對焦
- [x] 報表工具列加入「Photo Layout」即時切換（記錄到 localStorage，利於工作區偏好保持）
- [x] `Photo Layout` 按鈕同步更新副標題並加入 `P` 快捷鍵切換
- [x] 新增 `Compact View` 切換（`M` 快捷鍵 + `localStorage` 持久化，切換表格/卡片/版面密度，服務大數據瀏覽）
- [x] 加入 CLI `--compact/--no-compact` 控制輸出時是否以 compact 初始密度開啟，並在 `render_summary.json` 紀錄
- [x] 強化 `validate-summary` 契約檢查：新增 `render_settings.compact_layout` 欄位必填與型別驗證
- [x] 新增 inspector 卡片式摘要模式（`Photo Layout` 時）與搜尋命中高亮 (`.match`)，事件/交易清單保留原始欄位並提供卡片摘要列
- [x] 事件/交易渲染以 HTML 欄位插值進行摘要拼接，並在切換 `photo/compact` 時套用 `card-mode`，作為 RRKAL 大數據 inspector 的預渲染對齊方向
- [x] 修正 Photo/Compact 切換事件綁定時機，改為 `bind` 初始化時一次綁定，避免切版切換時重複綁定事件導致錯誤累積
