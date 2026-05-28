# RRKAL RenderKit（Photo-like 預渲染器）

RRKAL RenderKit 是一個獨立的預渲染工具，目標是讀取 RRKAL artifact 並輸出可直接交付前端/分析使用的報表：

- Markdown 摘要（`report.md`）
- HTML 互動報表（`report.html`）
- SVG 股權曲線（`equity_curve.svg`）
- PDF 報表（`report.pdf`）
- CSV/JSONL 導出（`trades.csv`、`equity_curve.csv`、`events.csv`、`events.jsonl`）
- 渲染摘要（`render_summary.html/json`）
- 報表打包（`render_bundle.zip`，與 report.html 同目錄）
  - `report.html` 會在支援打包的情境顯示 `Download Bundle` 按鈕，可直接下載全部輸出成果（含 `bundle_manifest.json`）
- `render_summary.json` 會記錄 `outputs.bundle`：
  - `requested`：是否啟用打包模式
  - `available`：是否成功輸出 zip
  - `path`：壓縮檔路徑（預設 `render_bundle.zip`）

這個版本特別做了兩件事：

1. 優先對齊「photo-like」檢視習慣（卡片化 KPI、快速篩選、互動表格）
2. 針對大資料做前置降採樣（`rdp` / `lttb` / `uniform`）避免前端卡頓

## 安裝

```bash
python -m pip install -e .
```

```bash
# 僅需 PDF 功能時
python -m pip install -e ".[pdf]"
```

## 快速上手

```bash
rrkal-renderer --help
```

### validate

```bash
python -m rrkal_renderer.cli validate path/to/run.json
python -m rrkal_renderer.cli validate-summary path/to/render_summary.json
```

### render

```bash
# 預設輸出 HTML / MD / JSON 元資訊 / SVG
python -m rrkal_renderer.cli render path/to/run.json

# 只輸出 PDF 報表（需要安裝 PDF 轉換後端）
python -m rrkal_renderer.cli render path/to/run.json --format pdf

# 指定 PDF 頁面標題與備註 metadata
python -m rrkal_renderer.cli render path/to/run.json --format pdf --pdf-title "My Report" --pdf-meta "RRKAL v2"

# 輸出全部時若缺少 PDF 轉換套件，會保留 `pdf_export_error.txt` 而不中斷其他輸出
python -m rrkal_renderer.cli render path/to/run.json --format all

# 只輸出 HTML + 降採樣設定
python -m rrkal_renderer.cli render path/to/run.json --format html --equity-compress rdp --equity-max-points 8000

# 只輸出 HTML 報表，但不打包（例如大資料量節省時間）
python -m rrkal_renderer.cli render path/to/run.json --format html --no-bundle

# 只輸出摘要（含 render_summary + render_bundle）
python -m rrkal_renderer.cli render path/to/run.json --format md --export-csv --emit-svg

# 只輸出 JSON + 打包
python -m rrkal_renderer.cli render path/to/run.json --format json --export-jsonl

# 只生成 bundle manifest（不產生 zip）
python -m rrkal_renderer.cli render path/to/run.json --format md --bundle-manifest-only

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
- `--emit-svg`（若輸出同時包含任何可匯出檔案，會在輸出目錄補齊 `render_bundle.zip`）
- `--export-csv`（同上）
- `--export-jsonl`（同上）
- `--format all|md|html|json|pdf`（同時會輸出 `render_bundle.zip`，有摘要時便可直接下載）
- `--pdf-title`：PDF metadata title
- `--pdf-meta`：PDF metadata note
- `--bundle` / `--no-bundle`（控制是否強制/取消輸出 bundle，預設依條件自動判斷）
- `--bundle-manifest-only`（只輸出 `bundle_manifest.json`，不壓縮）
- `bundle_manifest.json` 內會保留 `bundle_name`（固定為 `render_bundle.zip`）與 `bundle_mode`（`manifest`/`zip`/`none`）
- `--photo-style` / `--no-photo-style`

## 注意事項

- 只會驗證 artifact 的結構是否可載入，不做 RRKAL 執行/交易決策
- `schema_version` 預設採 `2.0.0`，未通過可在 `--lenient` 開啟後繼續輸出

## RRKAL bundle contract

`render_summary.json` now exposes `outputs.bundle` as follows:
- `mode`: `"zip"` / `"manifest"` / `"none"`
- `path`: physical output path for RRKAL download (`render_bundle.zip` or `bundle_manifest.json`)
- `requested`: boolean, whether bundling was requested by flags or auto-policy
- `available`: boolean, whether the target file was successfully written
- `reason`: concise reason for final state

Current reason values:
- `disabled by --no-bundle`
- `not requested by format/export options`
- `bundle generation failed`
- `no files eligible for manifest generation`
- `bundle manifest generated`
- `no files eligible for zip bundling`
- `bundle zip generated`

`bundle_manifest.json` fields:
- `bundle_name`: fixed target package name (`render_bundle.zip`)
- `bundle_mode`: `"manifest"` (for manifest-only mode)
- `created_at`, `file_count`, `items`

`items` includes each item name, byte size, and mtime.

Mode mapping:
- `zip`: command path produces/requests full `render_bundle.zip` (`--bundle`, or auto when format/json/html/md/pdf/svg/csv/jsonl exports are enabled)
- `manifest`: `--bundle-manifest-only` was used
- `none`: bundle is skipped (`--no-bundle`, or no auto-trigger for bundle)

Example `outputs.bundle` payload:

```json
{
  "mode": "zip",
  "path": "render_bundle.zip",
  "requested": true,
  "available": true,
  "reason": "bundle zip generated"
}
```

When bundle is skipped:

```json
{
  "mode": "none",
  "path": "render_bundle.zip",
  "requested": false,
  "available": false,
  "reason": "disabled by --no-bundle"
}
```

Machine-readable contract summary (RRKAL integration):

```json
{
  "artifact_name": "<artifact name>",
  "run_id": "<run id>",
  "outputs": {
    "bundle": {
      "mode": "zip | manifest | none",
      "path": "render_bundle.zip | bundle_manifest.json",
      "requested": true | false,
      "available": true | false,
      "reason": "<state reason>"
    }
  }
}
```

Expected states:

- `mode=zip` + `path=render_bundle.zip` in normal or `--bundle` mode
- `mode=manifest` + `path=bundle_manifest.json` when `--bundle-manifest-only`
- `mode=none` in disabled/non-auto cases
- `available=true` only if the output file exists after render

Downstream rule:

- If `outputs.bundle.mode` is `zip`, RRKAL should treat `outputs.bundle.path` as the downloadable artifact.
- If `outputs.bundle.mode` is `manifest`, RRKAL should treat `outputs.bundle.path` as pre-export inventory.
- If `outputs.bundle.mode` is `none`, RRKAL should ignore bundle download path and rely on `requested`/`reason`.
