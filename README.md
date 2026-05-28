# RRKAL RenderKit (MVP)

獨立於 RRKAL 核心的預渲染工具（pre-renderer）。

## 目標
- 將 RRKAL artifact (`.json`) 轉成可閱讀報告：
  - `report.md`
  - `report.html`（含簡版 equity 曲線與交易表）
- 保留可追溯輸出：CSV + JSONL（可選）
- 支援單檔與資料夾批次預渲染

## 核心流程（Governance 對齊）
- R（Requirements）：每次輸出前先做 schema 驗證
- R（Risks）：缺欄位、schema 版本不符、資料空值會有清楚錯誤訊息
- K（Keep / Known / Ask / Learn）：沿用 RRKAL `2.0.0` artifact 格式並逐步擴展
- A（Assurance）：`validate` 與 `render` 都會輸出可核對紀錄
- L（Lifecycle）：輸出檔案放在指定目錄，便於版本追蹤

## 安裝

```bash
# 可直接執行，不需要額外套件
```

## 使用

```bash
# 驗證 artifact
python -m rrkal_renderer.cli validate path/to/run.json

# 單檔預渲染（輸出 md + html）
python -m rrkal_renderer.cli render path/to/run.json

# 單檔只輸出 html，並導出 csv
python -m rrkal_renderer.cli render path/to/run.json --format html --export-csv --output-dir outputs/first

# 批次渲染整個目錄
python -m rrkal_renderer.cli render-batch path/to/results_dir --pattern *.json --output-root outputs/batch

# 輸出 json 備份
python -m rrkal_renderer.cli render path/to/run.json --format json --output-dir outputs/json_pack
```

### 參數
- `--lenient`：放寬 schema 版本檢查
- `--format {all,md,html,json}`：預設 `all`
- `--export-csv`：輸出 `trades.csv`、`equity_curve.csv`、`events.csv`
- `--export-jsonl`：輸出 `events.jsonl`
- `render-batch`：可用 `--pattern` 支援多格式（例如 `*.json`）
