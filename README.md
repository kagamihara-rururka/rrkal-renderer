# RRKAL RenderKit（Photo-like 預渲染器）

RRKAL RenderKit 是一個獨立的預渲染工具，目標是讀取 RRKAL artifact 並輸出可直接交付前端/分析使用的報表：

- Markdown 摘要（`report.md`）
- HTML 互動報表（`report.html`）
- SVG 股權曲線（`equity_curve.svg`）
- PDF 報表（`report.pdf`）
- CSV/JSONL 導出（`trades.csv`、`equity_curve.csv`、`events.csv`、`events.jsonl`）

這個版本特別做了兩件事：

1. 優先對齊「photo-like」檢視習慣（卡片化 KPI、快速篩選、互動表格）
2. 針對大資料做前置降採樣（`rdp` / `lttb` / `uniform`）避免前端卡頓

## 安裝

```bash
python -m pip install -e .
```

## 快速上手

```bash
rrkal-renderer --help
```

### validate

```bash
python -m rrkal_renderer.cli validate path/to/run.json
```

### render

```bash
# 預設輸出 HTML / MD / JSON 元資訊 / SVG
python -m rrkal_renderer.cli render path/to/run.json

# 只輸出 HTML + 降採樣設定
python -m rrkal_renderer.cli render path/to/run.json --format html --equity-compress rdp --equity-max-points 8000

# 加速分析：輸出 csv
python -m rrkal_renderer.cli render path/to/run.json --emit-svg --export-csv --output-dir outputs/run01

# 啟用 / 停用 photo-like 版型
python -m rrkal_renderer.cli render path/to/run.json --photo-style
python -m rrkal_renderer.cli render path/to/run.json --no-photo-style
```

### 批次

```bash
python -m rrkal_renderer.cli render-batch path/to/result_dir --pattern "*.json" --output-root outputs/batch
python -m rrkal_renderer.cli render-batch path/to/result_dir --pattern "*.jsonl" --equity-compress lttb --equity-max-points 6000
```

## 輸出參數

- `--lenient`：放寬 `schema_version` 驗證
- `--format {all,md,html,json,svg,pdf}`
- `--equity-compress {auto,rdp,lttb,uniform,none}`
- `--equity-max-points`（預設 5000）
- `--equity-rdp-epsilon`
- `--trade-max-rows`
- `--event-max-rows`
- `--emit-svg`
- `--export-csv`
- `--export-jsonl`
- `--photo-style` / `--no-photo-style`

## 注意事項

- 只會驗證 artifact 的結構是否可載入，不做 RRKAL 執行/交易決策
- `schema_version` 預設採 `2.0.0`，未通過可在 `--lenient` 開啟後繼續輸出
