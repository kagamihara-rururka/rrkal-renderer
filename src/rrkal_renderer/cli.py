from __future__ import annotations

import argparse
import csv
import html
import json
import math
import re
import zipfile
import heapq
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple


SUPPORTED_SCHEMA_VERSION = {"2.0.0"}
DEFAULT_EQUITY_MAX_POINTS = 5000
DEFAULT_TRADE_MAX_ROWS = 4000
DEFAULT_EVENT_MAX_ROWS = 2000


def _slugify(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "-", value.strip())
    safe = re.sub(r"-{2,}", "-", safe).strip("-")
    return safe[:120] if safe else "artifact"


def _load_json(path: str) -> Dict[str, Any]:
    raw = Path(path).read_text(encoding="utf-8")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise SystemExit(f"artifact json root must be object: {path}")
    return payload


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _emit_csv(path: str, rows: List[Dict[str, Any]]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        output.write_text("", encoding="utf-8")
        return
    fieldnames = sorted({k for row in rows for k in row.keys()})
    with output.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})


def _emit_jsonl(path: str, rows: Iterable[Dict[str, Any]]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as fp:
        for row in rows:
            fp.write(json.dumps(row, ensure_ascii=False))
            fp.write("\n")


def _write_pdf(path: Path, html_content: str, *, required: bool) -> str | None:
    path.parent.mkdir(parents=True, exist_ok=True)
    details = None

    weasy_error: Exception | None = None
    try:
        from weasyprint import HTML

        HTML(string=html_content).write_pdf(str(path))
        return None
    except Exception as exc:
        weasy_error = exc

    try:
        import pdfkit

        pdfkit.from_string(html_content, str(path))
        return None
    except Exception as exc:
        details = (
            "PDF export unavailable. Install one of: `pip install weasyprint` "
            "(preferred), or `pip install pdfkit` and provide wkhtmltopdf. "
            f"weasyprint error: {weasy_error}; pdfkit error: {exc}"
        )
        if required:
            raise RuntimeError(details) from exc
    return details


def _inject_pdf_metadata(html_content: str, *, title: str | None, note: str | None) -> str:
    safe_title = html.escape((title or "").strip())
    safe_note = html.escape((note or "").strip())
    if not safe_title and not safe_note:
        return html_content

    parts = [line for line in html_content.split("</head>", 1)]
    if len(parts) != 2:
        return html_content

    meta_parts = ["    <meta name='x-rrkal-render-pdf' content='true'>"]
    if safe_title:
        meta_parts.append(f"    <meta name='x-rrkal-pdf-title' content='{safe_title}'>")
    if safe_note:
        meta_parts.append(f"    <meta name='x-rrkal-pdf-note' content='{safe_note}'>")
    return f"{parts[0]}{''.join(meta_parts)}\n</head>{parts[1]}"


def _write_render_summary(
    out_dir: Path,
    artifact_name: str,
    run_id: str,
    payload: Dict[str, Any],
    args: argparse.Namespace,
    *,
    rendered: List[str],
    pdf_status: Dict[str, Any],
    bundle_info: Optional[Dict[str, Any]] = None,
) -> None:
    summary = {
        "artifact_name": artifact_name,
        "run_id": run_id,
        "schema_version": payload.get("schema_version"),
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "requested_format": args.format,
        "outputs": {
            "rendered_files": rendered,
            "pdf": pdf_status,
            "bundle": bundle_info
            or {
                "mode": "none",
                "path": "render_bundle.zip",
                "requested": False,
                "available": False,
                "reason": "not requested by command options",
            },
        },
        "render_settings": {
            "title": args.title,
            "trade_max_rows": args.trade_max_rows,
            "event_max_rows": args.event_max_rows,
            "html_row_cap": args.html_row_cap,
            "compact_layout": args.compact_layout,
            "equity_max_points": args.equity_max_points,
            "equity_compress": args.equity_compress,
            "equity_rdp_epsilon": args.equity_rdp_epsilon,
            "photo_style": args.photo_style,
        },
    }
    _write_text(out_dir / "render_summary.json", json.dumps(summary, ensure_ascii=False, indent=2))

    output_links = "".join(
        [
            f"<li><a href=\"{html.escape(item)}\">{html.escape(item)}</a></li>" if item != "render_summary.html" else
            f"<li><strong>{html.escape(item)}</strong> (current page)</li>"
            for item in rendered
        ]
    )
    pdf_result = "success" if pdf_status.get("success") else "failed"
    html_lines = [
        "<!doctype html>",
        "<html><head><meta charset='utf-8'><title>Render Summary</title><style>",
        "body{font-family:system-ui,Segoe UI,sans-serif;padding:1rem;background:#090909;color:#e5e7eb}",
        "a{color:#38bdf8}ul{line-height:1.8}",
        "</style></head><body>",
        "<h1>RRKAL RenderKit Summary</h1>",
        f"<p>artifact: <strong>{html.escape(artifact_name)}</strong></p>",
        f"<p>run_id: <code>{html.escape(run_id)}</code></p>",
        f"<p>requested format: <code>{html.escape(args.format)}</code></p>",
        f"<p>schema_version: <code>{html.escape(str(payload.get('schema_version')))}</code></p>",
        f"<p>pdf export: <strong>{pdf_result}</strong></p>",
        f"<p>bundle: requested=<strong>{bundle_info['requested'] if bundle_info else False}</strong> | "
        f"available=<strong>{bundle_info['available'] if bundle_info else False}</strong> | "
        f"mode=<strong>{html.escape((bundle_info or {}).get('mode', 'zip'))}</strong> | "
        f"path=<code>{html.escape((bundle_info or {}).get('path', 'render_bundle.zip'))}</code> | "
        f"reason=<strong>{html.escape((bundle_info or {}).get('reason', 'n/a'))}</strong></p>",
        "<h2>Rendered files</h2>",
        "<ul>",
        output_links,
        "</ul>",
        "<h3>Notes</h3>",
        f"<pre>{html.escape(json.dumps(pdf_status, ensure_ascii=False, indent=2))}</pre>",
        "</body></html>",
    ]
    _write_text(out_dir / "render_summary.html", "\n".join(html_lines))


def _write_bundle(out_dir: Path, file_names: List[str], *, bundle_name: str = "render_bundle.zip") -> bool:
    bundle_path = out_dir / bundle_name
    candidates = [p for p in file_names if p != bundle_name]
    added = 0
    manifest_items: List[Dict[str, Any]] = []
    with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name in candidates:
            item = out_dir / name
            if item.exists():
                zf.write(item, arcname=name)
                manifest_items.append(
                    {
                        "name": name,
                        "size_bytes": item.stat().st_size,
                        "mtime": datetime.utcfromtimestamp(item.stat().st_mtime).isoformat() + "Z",
                    }
                )
                added += 1
        if added == 0:
            return False
        zf.writestr(
            "bundle_manifest.json",
            json.dumps(
                {
                    "bundle_name": bundle_name,
                    "created_at": datetime.utcnow().isoformat() + "Z",
                    "file_count": len(manifest_items),
                    "items": manifest_items,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
        )
    return added > 0


def _build_bundle_manifest(
    out_dir: Path,
    file_names: List[str],
    *,
    bundle_name: str = "render_bundle.zip",
    manifest_name: str = "bundle_manifest.json",
) -> bool:
    candidates = [p for p in file_names if p != manifest_name]
    manifest_items: List[Dict[str, Any]] = []
    added = 0
    for name in candidates:
        item = out_dir / name
        if item.exists():
            manifest_items.append(
                {
                    "name": name,
                    "size_bytes": item.stat().st_size,
                    "mtime": datetime.utcfromtimestamp(item.stat().st_mtime).isoformat() + "Z",
                }
            )
            added += 1
    if added == 0:
        return False
    _write_text(
        out_dir / manifest_name,
        json.dumps(
            {
                "bundle_name": bundle_name,
                "bundle_mode": "manifest",
                "created_at": datetime.utcnow().isoformat() + "Z",
                "file_count": len(manifest_items),
                "items": manifest_items,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
    )
    return True


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _as_text(value: Any, default: str = "") -> str:
    return default if value is None else str(value)


def _default_output_dir(run_id: str) -> Path:
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    run_part = run_id[:8] if len(run_id) >= 8 else run_id
    return Path(f"rrkal_render_{ts}_{run_part}")


def _iter_artifact_sources(path: str) -> Iterator[Tuple[str, Dict[str, Any]]]:
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"input not found: {p}")
    if p.is_dir():
        raise SystemExit(f"input must be a file path (json/jsonl/zip): {p}")

    suffix = p.suffix.lower()
    if suffix == ".json":
        yield (p.stem, _load_json(path))
        return
    if suffix == ".jsonl":
        with p.open("r", encoding="utf-8") as handle:
            for index, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise SystemExit(f"invalid jsonl record at line {index} in {p}: {exc}") from exc
                if not isinstance(payload, dict):
                    raise SystemExit(f"invalid artifact in {p} line {index}: root must be an object")
                yield (f"{p.stem}_line_{index}", payload)
        return
    if suffix == ".zip":
        with zipfile.ZipFile(p, "r") as zf:
            infos = [i for i in zf.infolist() if not i.is_dir() and i.filename.lower().endswith(".json")]
            if not infos:
                raise SystemExit(f"zip has no .json file: {p}")
            for info in sorted(infos, key=lambda x: x.filename):
                try:
                    payload = json.loads(zf.read(info).decode("utf-8"))
                except json.JSONDecodeError as exc:
                    raise SystemExit(f"invalid json in zip entry {info.filename}: {exc}") from exc
                except Exception as exc:
                    raise SystemExit(f"failed reading zip entry {info.filename}: {exc}") from exc
                if not isinstance(payload, dict):
                    raise SystemExit(f"invalid artifact in zip entry {info.filename}: root must be object")
                stem = Path(info.filename).name.rsplit(".", 1)[0]
                yield (f"{p.stem}::{stem}", payload)
        return
    raise SystemExit("unsupported input format, need .json, .jsonl or .zip")


def _resolve_evidence(payload: Dict[str, Any]) -> Dict[str, Any]:
    evidence = payload.get("evidence")
    if isinstance(evidence, dict):
        return evidence
    artifacts = payload.get("artifacts")
    if isinstance(artifacts, dict) and isinstance(artifacts.get("evidence"), dict):
        return artifacts["evidence"]
    return {}


def _resolve_run_id(payload: Dict[str, Any]) -> str:
    evidence = _resolve_evidence(payload)
    run_meta = evidence.get("run_metadata", {}) if isinstance(evidence.get("run_metadata"), dict) else {}
    return (
        _as_text(run_meta.get("run_id"))
        or _as_text(evidence.get("run_id"))
        or _as_text(payload.get("run_id"))
        or "run"
    )


def _validate(payload: Dict[str, Any], strict: bool = True) -> None:
    missing = [k for k in ("schema_version", "intent", "plan") if k not in payload]
    if missing:
        raise SystemExit(f"artifact missing required field: {', '.join(missing)}")

    schema = str(payload.get("schema_version", "")).strip()
    if strict and schema and schema not in SUPPORTED_SCHEMA_VERSION:
        raise SystemExit(f"unsupported schema_version={schema}, expected one of {sorted(SUPPORTED_SCHEMA_VERSION)}")

    if not isinstance(_resolve_evidence(payload), dict):
        raise SystemExit("artifact missing evidence object")


def _collect_stats(payload: Dict[str, Any]) -> Dict[str, Any]:
    evidence = _resolve_evidence(payload)
    summary_run = evidence.get("summary", {}).get("run", {})
    run_meta = evidence.get("run_metadata", {})
    trades = evidence.get("trades", []) if isinstance(evidence.get("trades"), list) else []
    events = evidence.get("events", []) if isinstance(evidence.get("events"), list) else []

    pnls = [_as_float(t.get("pnl", 0.0)) for t in trades if isinstance(t, dict)]
    wins = [p for p in pnls if p > 0]
    event_counter: Counter[str] = Counter(
        _as_text(item.get("event_type", item.get("event", "")), "")
        for item in events
        if isinstance(item, dict) and _as_text(item.get("event_type", item.get("event", "")), "")
    )

    symbols = [str(t.get("symbol")) for t in trades if isinstance(t, dict) and t.get("symbol") is not None]
    return {
        "summary_run": summary_run,
        "run_meta": run_meta,
        "trade_count": len(trades),
        "symbol_count": len(set(symbols)) if symbols else 0,
        "win_count": len(wins),
        "loss_count": len(pnls) - len(wins),
        "gross_pnl": round(sum(pnls), 4),
        "avg_pnl": round(sum(pnls) / len(pnls), 4) if pnls else 0.0,
        "win_rate": round((len(wins) / len(pnls) * 100), 2) if pnls else 0.0,
        "event_counter": event_counter,
        "max_drawdown_seen": summary_run.get("max_drawdown_seen", "N/A"),
    }


def _normalize_timestamp(value: Any, fallback: float) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    text = _as_text(value, "").strip()
    if not text:
        return fallback
    try:
        return float(text)
    except Exception:
        pass
    try:
        value_norm = text.replace("Z", "+00:00")
        return datetime.fromisoformat(value_norm).timestamp()
    except Exception:
        return fallback


def _extract_equity_points(evidence: Dict[str, Any]) -> List[Tuple[float, float, str]]:
    rows = evidence.get("equity_curve", []) if isinstance(evidence.get("equity_curve"), list) else []
    points: List[Tuple[float, float, str]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        eq = _as_float(row.get("equity"), _as_float(row.get("value"), 0.0))
        if math.isnan(eq) or math.isinf(eq):
            continue
        ts_raw = row.get("timestamp", row.get("ts", row.get("time", index)))
        ts_text = _as_text(ts_raw, str(index))
        ts = _normalize_timestamp(ts_raw, float(index))
        points.append((ts, eq, ts_text))
    return points


def _extract_trades(evidence: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = evidence.get("trades", []) if isinstance(evidence.get("trades"), list) else []
    out: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        out.append(
            {
                "symbol": _as_text(row.get("symbol", "")),
                "direction": _as_text(row.get("direction", "")),
                "quantity": _as_float(row.get("quantity", 0), 0.0),
                "entry": _as_float(row.get("entry", 0), 0.0),
                "exit": _as_float(row.get("exit", 0), 0.0),
                "pnl": _as_float(row.get("pnl", 0), 0.0),
                "start_ts": _as_text(row.get("start_ts", row.get("entry_ts", ""))),
                "end_ts": _as_text(row.get("end_ts", row.get("exit_ts", ""))),
                "entry_cost": _as_float(row.get("entry_cost", 0), 0.0),
                "exit_cost": _as_float(row.get("exit_cost", 0), 0.0),
            }
        )
    return out


def _extract_events(evidence: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = evidence.get("events", []) if isinstance(evidence.get("events"), list) else []
    out: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        out.append(
            {
                "event": _as_text(row.get("event_type", row.get("event", row.get("type", "")))),
                "symbol": _as_text(row.get("symbol", "")),
                "timestamp": _as_text(row.get("timestamp", row.get("ts", ""))),
                "details": row.get("details", {}),
            }
        )
    return out


def _rdp_indices(x: List[float], y: List[float], epsilon: float) -> List[int]:
    if epsilon < 0:
        raise ValueError("epsilon must be non-negative")
    n = len(x)
    if n <= 2:
        return list(range(n))

    keep = [False] * n
    keep[0] = True
    keep[-1] = True
    stack: List[Tuple[int, int]] = [(0, n - 1)]

    while stack:
        start, end = stack.pop()
        if end <= start + 1:
            continue
        x1 = x[start]
        y1 = y[start]
        x2 = x[end]
        y2 = y[end]
        xs = x[start + 1 : end]
        ys = y[start + 1 : end]
        dx = x2 - x1
        dy = y2 - y1
        denom = math.hypot(dx, dy)
        if denom == 0.0:
            dist = [math.hypot(xs[i] - x1, ys[i] - y1) for i in range(len(xs))]
        else:
            dist = [abs(dy * xs[i] - dx * ys[i] + x2 * y1 - y2 * x1) / denom for i in range(len(xs))]
        if not dist:
            continue
        max_i = max(range(len(dist)), key=lambda idx: dist[idx])
        if dist[max_i] > epsilon:
            index = start + 1 + max_i
            keep[index] = True
            stack.append((start, index))
            stack.append((index, end))

    return [idx for idx, keep_flag in enumerate(keep) if keep_flag]


def _lttb_indices(x: List[float], y: List[float], threshold: int) -> List[int]:
    n = len(x)
    if threshold >= n:
        return list(range(n))
    if threshold < 2:
        return [0, n - 1]

    sampled = [0] * threshold
    sampled[0] = 0
    sampled[-1] = n - 1
    bucket_size = (n - 2) / float(threshold - 2)
    anchor = 0

    for bucket_index in range(0, threshold - 2):
        r0 = int(math.floor(bucket_index * bucket_size)) + 1
        r1 = int(math.floor((bucket_index + 1) * bucket_size)) + 1
        r1 = min(r1, n - 1)
        r2 = int(math.floor((bucket_index + 1) * bucket_size)) + 1
        r3 = int(math.floor((bucket_index + 2) * bucket_size)) + 1
        r3 = min(r3, n)
        if r2 >= r3:
            avg_x = x[-1]
            avg_y = y[-1]
        else:
            avg_x = sum(x[r2:r3]) / (r3 - r2)
            avg_y = sum(y[r2:r3]) / (r3 - r2)

        cand = list(range(r0, max(r0 + 1, r1)))
        if not cand:
            sampled[bucket_index + 1] = r1
            anchor = r1
            continue
        ax = x[anchor]
        ay = y[anchor]
        areas = [abs((ax - avg_x) * (y[c] - ay) - (ax - x[c]) * (avg_y - ay)) for c in cand]
        chosen = cand[max(range(len(areas)), key=lambda i: areas[i], default=0)]
        sampled[bucket_index + 1] = chosen
        anchor = chosen

    return sampled


def _downsample_points(
    points: List[Tuple[float, float, str]],
    *,
    max_points: int,
    method: str = "auto",
    rdp_epsilon: float = 0.002,
) -> List[Tuple[float, float, str]]:
    n = len(points)
    if n <= 1 or max_points <= 0 or n <= max_points:
        return points

    if method == "none":
        return points[:max_points]

    x = [p[0] for p in points]
    y = [p[1] for p in points]
    if method == "uniform":
        stride = max(1, n // max_points)
        idx = list(range(0, n, stride))
        if idx[-1] != n - 1:
            idx.append(n - 1)
    elif method == "lttb":
        idx = _lttb_indices(x, y, max_points)
    elif method == "rdp":
        idx = _rdp_indices(x, y, rdp_epsilon)
    else:
        if n <= max_points * 3:
            stride = max(1, n // max_points)
            idx = list(range(0, n, stride))
            if idx[-1] != n - 1:
                idx.append(n - 1)
        else:
            idx = _rdp_indices(x, y, rdp_epsilon)
            if len(idx) > max_points:
                idx = _lttb_indices(x, y, max_points)

    if len(idx) > max_points:
        step = max(1, len(idx) // max_points)
        idx = idx[::step]
        if idx[-1] != n - 1:
            idx[-1] = n - 1

    unique = []
    seen = set()
    for i in idx:
        if i not in seen:
            seen.add(i)
            unique.append(i)
    return [points[i] for i in unique if 0 <= i < n]


def _svg_polyline(points: List[Tuple[float, float, str]], width: int = 1080, height: int = 360, padding: int = 36) -> str:
    if len(points) < 2:
        return ""
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    x_min = min(xs)
    x_max = max(xs)
    y_min = min(ys)
    y_max = max(ys)
    if x_max == x_min:
        x_max = x_min + 1.0
    if y_max == y_min:
        y_max = y_min + 1.0
    x_span = x_max - x_min
    y_span = y_max - y_min

    def sx(v: float) -> float:
        return padding + (v - x_min) / x_span * (width - 2 * padding)

    def sy(v: float) -> float:
        return height - padding - (v - y_min) / y_span * (height - 2 * padding)

    parts = [f"M {sx(points[0][0]):.2f} {sy(points[0][1]):.2f}"]
    for px, py, _ in points[1:]:
        parts.append(f"L {sx(px):.2f} {sy(py):.2f}")
    return " ".join(parts)


def _summary_markdown(payload: Dict[str, Any], point_count: int, rendered_point_count: int) -> str:
    stats = _collect_stats(payload)
    run_meta = stats["run_meta"]
    summary_run = stats["summary_run"]

    lines = [
        "# RRKAL Render Snapshot",
        "",
        "## 1) Run metadata",
        f"schema_version: `{payload.get('schema_version', 'N/A')}`",
        f"run_id: `{_resolve_run_id(payload)}`",
        f"platform_id: `{run_meta.get('platform_id', 'N/A')}`",
        f"market_id: `{run_meta.get('market_id', 'N/A')}`",
        f"provider_id: `{run_meta.get('provider_id', 'N/A')}`",
        f"strategy_id: `{run_meta.get('strategy_id', 'N/A')}`",
        "",
        "## 2) Key metrics",
        "| metric | value |",
        "| --- | ---: |",
        f"| initial_cash | {_as_text(summary_run.get('initial_cash', 'N/A'))} |",
        f"| final_cash | {_as_text(summary_run.get('final_cash', 'N/A'))} |",
        f"| total_pnl | {_as_text(summary_run.get('total_pnl', 'N/A'))} |",
        f"| total_trades | {stats['trade_count']} |",
        f"| win_rate | {stats['win_rate']}% |",
        f"| gross_pnl | {stats['gross_pnl']} |",
        f"| avg_pnl | {stats['avg_pnl']} |",
        f"| max_drawdown_seen | {_as_text(stats['max_drawdown_seen'])} |",
        f"| equity_curve_points | {point_count} |",
        f"| rendered_points | {rendered_point_count} |",
        "",
        "## 3) Event frequency",
    ]

    if stats["event_counter"]:
        lines.extend(["| event | count |", "| --- | ---: |"])
        for name, count in stats["event_counter"].most_common():
            lines.append(f"| {name} | {count} |")
    else:
        lines.append("(No event records)")

    return "\\n".join(lines)


def _to_html(
    payload: Dict[str, Any],
    title: str,
    bundle_download_name: str | None,
    max_equity_points: int,
    equity_compress: str,
    rdp_epsilon: float,
    trade_max_rows: int,
    event_max_rows: int,
    html_row_cap: int = 5000,
    photo_style: bool,
    compact_layout: bool = False,
) -> str:
    evidence = _resolve_evidence(payload)
    trades = _extract_trades(evidence)
    events = _extract_events(evidence)
    equity_points = _extract_equity_points(evidence)
    sampled = _downsample_points(
        equity_points,
        max_points=max_equity_points,
        method=equity_compress,
        rdp_epsilon=rdp_epsilon,
    )
    path_d = _svg_polyline(sampled)
    html_trade_cap: int
    if trade_max_rows > 0:
        html_trade_cap = trade_max_rows
    elif html_row_cap > 0:
        html_trade_cap = html_row_cap
    elif html_row_cap == 0:
        html_trade_cap = len(trades)
    else:
        html_trade_cap = 5000

    if event_max_rows > 0:
        html_event_cap = event_max_rows
    elif html_row_cap > 0:
        html_event_cap = html_row_cap
    elif html_row_cap == 0:
        html_event_cap = len(events)
    else:
        html_event_cap = 5000

    inspect_cap_label = "trade:unlimited" if html_trade_cap >= len(trades) else f"trade:{html_trade_cap}"
    inspect_cap_label = f"{inspect_cap_label}, event:unlimited" if html_event_cap >= len(events) else f"{inspect_cap_label}, event:{html_event_cap}"

    trade_events = trades
    top_trades = (
        heapq.nlargest(
            html_trade_cap,
            trade_events,
            key=lambda row: abs(_as_float(row.get("pnl", 0.0), 0.0)),
        )
        if html_trade_cap > 0
        else trade_events
    )

    recent_events = (
        heapq.nlargest(
            html_event_cap,
            events,
            key=lambda row: _as_text(row.get("timestamp", "")),
        )
        if html_event_cap > 0
        else sorted(events, key=lambda row: _as_text(row.get("timestamp", "")), reverse=True)
    )
    if html_event_cap > 0:
        recent_events = sorted(
            recent_events,
            key=lambda row: _as_text(row.get("timestamp", "")),
            reverse=True,
        )

    md = _summary_markdown(payload, len(equity_points), len(sampled))
    symbols = sorted({trade.get("symbol", "") for trade in top_trades if trade.get("symbol")})
    event_names = sorted({event.get("event", "") for event in recent_events if event.get("event")})
    event_chip_names = event_names[:20]
    trade_chip_counter = Counter(trade.get("symbol", "") for trade in top_trades if trade.get("symbol", "").strip())
    trade_chip_names = [name for name, _ in trade_chip_counter.most_common(12)]
    trade_dirs = sorted({trade.get("direction", "").strip() for trade in top_trades if trade.get("direction", "").strip()})

    eq_stats = {
        "count": len(equity_points),
        "sampled": len(sampled),
        "start": sampled[0][1] if sampled else 0.0,
        "end": sampled[-1][1] if sampled else 0.0,
        "high": max((p[1] for p in sampled), default=0.0),
        "low": min((p[1] for p in sampled), default=0.0),
    }
    eq_delta = eq_stats["end"] - eq_stats["start"]
    equity_chart_points = [
        {"ts": point[0], "value": point[1], "label": point[2]} for point in sampled
    ] if sampled else []

    safe_title = html.escape(title, quote=True)
    safe_run_id = html.escape(_resolve_run_id(payload), quote=True)
    body_class = "photo" if photo_style else "classic"
    if compact_layout:
        body_class += " compact"
    chip_block = "".join(
        f'<span class="chip" data-event="{html.escape(name, quote=True)}">{html.escape(name)}</span>'
        for name in event_chip_names
    )

    html_lines: List[str] = [
        "<!doctype html>",
        "<html lang=\"en\">",
        "<head>",
        '  <meta charset="utf-8" />',
        '  <meta name="viewport" content="width=device-width, initial-scale=1" />',
        f"  <title>{safe_title}</title>",
        "  <style>",
        "    :root{"
        "--bg:radial-gradient(120% 120% at 0% 0%, #0b1020, #131f37 45%, #060b17);"
        "--panel:rgba(15,23,42,.72);"
        "--line:rgba(148,163,184,.22);"
        "--text:#e2e8f0;"
        "--text-dim:#94a3b8;"
        "--ok:#22c55e;"
        "--bad:#ef4444;"
        "}",
        "    *{box-sizing:border-box}",
        "    body{margin:0;padding:1.2rem;color:var(--text);font-family:Inter, 'SF Pro Display', 'Avenir Next', 'Noto Sans TC', 'Microsoft JhengHei', sans-serif;background:var(--bg)}",
        "    body.photo{animation:fadeIn .35s ease-out}",
        "    @keyframes fadeIn{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}",
        "    .toolbar{display:flex;gap:.5rem;align-items:center;flex-wrap:wrap;margin:.25rem 0 .7rem}",
        "    .toolbar input,.toolbar select,.toolbar button{background:#020617;color:var(--text);border:1px solid #334155;border-radius:8px;padding:.35rem .5rem}",
        "    .toolbar button, .panel button{background:#020617;color:var(--text);border:1px solid #334155;border-radius:8px;padding:.35rem .5rem;cursor:pointer;white-space:nowrap}",
        "    .toolbar button:hover, .panel button:hover{border-color:#93c5fd;color:#bfdbfe}",
        "    .toolbar .mono{font-family:ui-monospace, SFMono-Regular, Menlo, Consolas, monospace}",
        "    .global-toolbar{align-items:flex-end}",
        "    .global-toolbar .title-block{flex:1;min-width:220px}",
        "    .badge{font-size:.75rem;padding:.2rem .5rem;border-radius:999px;border:1px solid #334155;color:#cbd5e1;background:rgba(15,23,42,.6)}",
        "    .panel-head{display:flex;align-items:center;justify-content:space-between;gap:.5rem}",
        "    .panel-head .panel-title{margin:0;font-size:1rem;font-weight:600}",
        "    .panel-head .collapse-btn{margin-left:auto}",
        "    .panel-note{font-size:.78rem;color:var(--text-dim)}",
        "    .container{max-width:1320px;margin:0 auto}",
        "    .title{font-size:1.35rem;font-weight:700;letter-spacing:.02em;margin:.2rem 0 .5rem}",
        "    .subtitle{color:var(--text-dim);margin-bottom:1rem}",
        "    .panel{background:var(--panel);border:1px solid #334155;border-radius:12px;padding:.85rem;margin-bottom:1rem}",
        "    .panel h3{margin:0 0 .6rem;font-size:1rem}",
        "    .panel-body{margin-top:.55rem}",
        "    .panel-body.hidden{display:none}",
        "    .collapse-btn{background:#020617;border:1px solid #334155;border-radius:7px;padding:.2rem .45rem;color:var(--text-dim);cursor:pointer;font-weight:700;line-height:1}",
        "    .collapse-btn[data-collapsible]{min-width:28px}",
        "    .snapshot{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:.6rem}",
        "    .kpi{background:#0f172ab3;border:1px solid #334155;border-radius:10px;padding:.55rem .65rem;display:flex;flex-direction:column}",
        "    .kpi .name{color:var(--text-dim);font-size:.82rem}",
        "    .kpi .val{margin-top:.2rem;font-weight:700;word-break:break-all}",
        "    .layout{display:grid;grid-template-columns:1.1fr 1fr;gap:0.9rem}",
        "    .chart{width:100%;height:380px;border:1px solid #334155;border-radius:10px;padding:10px;background:#02061788;position:relative;overflow:hidden}",
        "    .chart svg{width:100%;height:100%;display:block;cursor:crosshair}",
        "    .chart .chart-meta{margin-top:.45rem}",
        "    .chart-marker{fill:#38bdf8;stroke:#fff;stroke-width:2;opacity:0;transition:opacity .2s}",
        "    .table-wrap{overflow:auto;max-height:360px}",
        "    table{width:100%;border-collapse:collapse;font-size:.85rem}",
        "    th,td{padding:.4rem .35rem;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}",
        "    th{color:var(--text-dim);font-weight:600;position:sticky;top:0;background:#0f172ab3}",
        "    .up{color:var(--ok)}",
        "    .down{color:var(--bad)}",
        "    pre{background:#02061788;padding:.75rem;border-radius:10px;border:1px solid #334155;overflow:auto;white-space:pre-wrap;max-height:240px}",
        "    .chips{display:flex;flex-wrap:wrap;gap:.4rem;margin:.2rem 0}",
        "    .chip{padding:.18rem .55rem;border-radius:999px;border:1px solid #334155;color:var(--text-dim);font-size:.78rem;cursor:pointer}",
        "    .chip:hover,.chip[data-active='1']{border-color:#93c5fd;color:#bfdbfe}",
        "    .chip[data-active='1']{box-shadow:0 0 0 1px #93c5fd66 inset}",
        "    .classic .chip{display:none}",
        "    .pager{display:flex;align-items:center;gap:.45rem;flex-wrap:wrap;margin-top:.45rem}",
        "    .pager button{padding:.25rem .6rem}",
        "    .pager .right{margin-left:auto}",
        "    .symbol-link{color:#93c5fd;text-decoration:underline;cursor:pointer}",
        "    .detail{margin-top:.55rem;max-height:190px;overflow:auto;white-space:pre-wrap;background:#02061788;padding:.55rem;border-radius:10px;border:1px solid #334155;}",
        "    .row-selected{outline:1px solid #93c5fd;background:rgba(59,130,246,.12)}",
        "    .panel.active{outline:1px solid #60a5fa;box-shadow:0 0 0 1px #60a5fa22 inset}",
        "    tbody tr{cursor:pointer}",
        "    .small{font-size:.8rem;color:var(--text-dim)}",
        "    .note{font-size:.8rem;color:var(--text-dim);margin-top:.4rem}",
        "    .hotkeys{margin:.45rem 0 0;padding:.45rem 0;border-top:1px dashed #334155;color:var(--text-dim)}",
        "    .counter{font-size:.78rem;color:#a5b4fc;font-family:ui-monospace, SFMono-Regular, Menlo, Consolas, monospace}",
        "    .inspector-meta{display:flex;flex-wrap:wrap;gap:.5rem;align-items:center;margin-bottom:.4rem}",
        "    .inspector-meta .hint{margin-left:auto}",
        "    .photo .layout{grid-template-columns:1.1fr 1fr}",
        "    .photo .panel{backdrop-filter:blur(2px)}",
        "    .compact .panel{padding:.6rem;border-radius:11px}",
        "    .compact .panel h3{font-size:.92rem}",
        "    .compact .panel-note{font-size:.72rem}",
        "    .compact .kpi{padding:.42rem .5rem}",
        "    .compact .layout{gap:.64rem}",
        "    .compact .snapshot{grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:.5rem}",
        "    .compact .chart{height:310px;padding:8px}",
        "    .compact .toolbar{gap:.36rem}",
        "    .compact .toolbar input,.compact .toolbar select,.compact .toolbar button{padding:.28rem .42rem}",
        "    .compact .table-wrap{max-height:260px}",
        "    .compact th,.compact td{padding:.3rem .28rem;font-size:.78rem}",
        "    .compact .detail{max-height:130px}",
        "    .compact .subtitle{margin-bottom:.55rem}",
        "    .section-empty{padding:.9rem;border:1px dashed #334155;border-radius:10px;color:var(--text-dim);text-align:center}",
        "    @media (max-width: 1024px){.layout{grid-template-columns:1fr;}}",
        "    @media (max-width: 640px){body{padding:0.8rem;}}",
        "  </style>",
        "</head>",
        f"<body class=\"{body_class}\">",
        "  <div class='container'>",
        f"    <div class='title'>{safe_title}</div>",
        f"    <div id='layoutSubtitle' class='subtitle'>RRKAL 2.0.0 photo-style pre-renderer mode={body_class} | html rows: {inspect_cap_label}</div>",
        "    <section class='panel global-toolbar'>",
        "      <div class='toolbar'>",
        "        <div class='title-block'>",
        "          <div class='panel-title'>Workspace Controls</div>",
        "          <div class='panel-note'>Photo-like workflow: focus + filter + inspect</div>",
        "        </div>",
        "        <input id='globalSearch' placeholder='Global search (symbol/event/pnl...)'>",
        "        <select id='globalScope'>",
        "          <option value='all'>All</option>",
        "          <option value='events'>Events</option>",
        "          <option value='trades'>Trades</option>",
        "        </select>",
        "        <button id='globalReset'>Clear Search</button>",
        "        <button id='compactToggle'>Compact View: Off</button>",
        "        <button id='styleToggle'>Photo Layout: On</button>",
    ]
    if bundle_download_name:
        html_lines.extend(["        <button id='exportBundle'>Download Bundle</button>"])
    html_lines.extend([
        "        <span id='globalMatch' class='badge'>No global query</span>",
        "      </div>",
        "      <p class='note'>Shortcuts: / to focus search, Enter/C to copy selection, F/T to jump symbol input, P to toggle Photo Layout, M to toggle Compact View.</p>",
        "    </section>",
        "    <section class='panel'>",
        "      <div class='panel-head'>",
        "        <h3>1) Run Snapshot</h3>",
        f"        <span class='panel-note'>{safe_title}</span>",
        "      </div>",
        "      <div class='snapshot'>",
        f'        <div class="kpi"><div class="name">run_id</div><div class="val mono">{safe_run_id}</div></div>',
        f'        <div class="kpi"><div class="name">schema_version</div><div class="val">{payload.get("schema_version", "N/A")}</div></div>',
        f'        <div class="kpi"><div class="name">equity points</div><div class="val">{eq_stats["count"]}</div></div>',
        f'        <div class="kpi"><div class="name">render points</div><div class="val">{eq_stats["sampled"]}</div></div>',
        f'        <div class="kpi"><div class="name">symbols</div><div class="val">{len(symbols)}</div></div>',
        f'        <div class="kpi"><div class="name">events</div><div class="val">{len(events)}</div></div>',
        f'        <div class="kpi"><div class="name">equity change</div><div class="val">{eq_delta:.4f}</div></div>',
        f'        <div class="kpi"><div class="name">equity range</div><div class="val">{eq_stats["low"]:.4f} ~ {eq_stats["high"]:.4f}</div></div>',
        "      </div>",
        "      <p class='note'>Large dataset tip: use filters/sort first, then export filtered or visible rows.</p>",
        "      <p class='hotkeys small'>Shortcuts: J/K or [] or ��/�� (page prev/next), ��/�� (row nav), Shift+Enter (copy first row on current page), Shift+Alt+Enter (copy last row on current page), Ctrl/Cmd+Shift+Enter (also copy first row), Home/End (jump first/last row), M (toggle compact), C/Enter (copy selected), F/T (focus symbol filters), R (reset active), 0 (clear filters)</p>",
        "    </section>",
        "    <section class='panel'>",
        "      <h3>2) Report summary</h3>",
        f"      <pre>{md}</pre>",
        "    </section>",
        "    <div class='layout'>",
        "      <section class='panel' id='equityPanel'>",
        "        <div class='panel-head'>",
        "          <h3>3) Equity Curve</h3>",
        "          <span class='panel-note'>Interactive render preview</span>",
        "          <button class='collapse-btn' data-collapsible='1' data-target='equityPanelBody' aria-expanded='true' title='Collapse section'>?</button>",
        "        </div>",
        "        <div class='panel-body' id='equityPanelBody'>",
        "        <div class='chart'>",
        f"          <svg id='equityChart' viewBox='0 0 1080 360' preserveAspectRatio='none' data-points='{len(equity_chart_points)}'>",
        f"            <path d='{path_d}' id='equityPath' fill='none' stroke='#38bdf8' stroke-width='2'/>",
        "            <line x1='36' y1='36' x2='36' y2='324' stroke='rgba(148,163,184,.28)' />",
        "            <line x1='36' y1='324' x2='1044' y2='324' stroke='rgba(148,163,184,.28)' />",
        "            <circle id='equityCursor' class='chart-marker' r='5' cx='36' cy='324' />",
        "            <line id='equityCrosshair' stroke='#93c5fd55' stroke-width='1' x1='36' y1='36' x2='36' y2='324' />",
        "          </svg>",
        "          <div id='chartMeta' class='small chart-meta'>No point selected.</div>",
        "        </div>",
        f"        <div class='small counter'>points: {eq_stats['sampled']}/{eq_stats['count']} | method={html.escape(equity_compress)} | epsilon={rdp_epsilon}</div>",
        "        </div>",
        "      </section>",
        "      <section class='panel' id='eventPanel'>",
        "        <div class='panel-head'>",
        "          <h3>4) Event Inspector</h3>",
        "          <span class='panel-note'>Filter by symbol/event / row preview</span>",
        "          <button class='collapse-btn' data-collapsible='1' data-target='eventInspectorPanelBody' aria-expanded='true' title='Collapse section'>?</button>",
        "        </div>",
        "        <div class='panel-body' id='eventInspectorPanelBody'>",
        "        <div class='toolbar inspector-meta'>",
        "          <input id='eventSymbol' placeholder='symbol contains'>",
        f"          <select id='eventName'><option value=''>All Events</option>{''.join([f'<option value=\"{n}\">{n}</option>' for n in event_names])}</select>",
        "          <select id='eventOrder'><option value='desc'>Newest first</option><option value='asc'>Oldest first</option></select>",
        "          <input id='eventFrom' placeholder='From (timestamp)'>",
        "          <input id='eventTo' placeholder='To (timestamp)'>",
        "          <button id='eventReset'>Reset</button>",
        "          <button id='eventExport'>Export visible CSV</button>",
        "          <select id='eventPageSize'><option value='25'>25</option><option value='50'>50</option><option value='100'>100</option><option value='200' selected>200</option><option value='500'>500</option><option value='1000'>1000</option></select>",
        "          <span id='eventCount' class='small counter'></span>",
        "        </div>",
        f"        <div class='chips' id='eventChips'>{chip_block}</div>",
        "        <div class='table-wrap'><table><thead><tr><th>event</th><th>symbol</th><th>timestamp</th><th>details</th></tr></thead><tbody id='eventBody'></tbody></table></div>",
        "        <div class='inspector-meta'>",
        "          <p id='eventMeta' class='small counter'>Selected event: none</p>",
        "          <span class='hint small counter'>Tip: click symbol to apply symbol filter.</span>",
        "        </div>",
        "        <pre id='eventDetail' class='detail'>Click an event row to inspect details.</pre>",
        "        <button id='eventCopyRow'>Copy selected row JSON</button>",
        "        <button id='eventExportAll'>Export filtered CSV</button>",
        "        <div class='pager'>",
        "          <button id='eventPrev'>Prev</button>",
        "          <span id='eventPager' class='small counter'>Page 1/1</span>",
        "          <button id='eventNext'>Next</button>",
        "          <span id='eventPageHint' class='small counter right'>rows 0-0</span>",
        "        </div>",
        "        <div id='eventEmpty' class='section-empty' style='display:none'>No events match current filter.</div>",
        "        </div>",
        "      </section>",
        "    </div>",
        "    <section class='panel' id='tradePanel'>",
        "      <div class='panel-head'>",
        "        <h3>5) Trades Inspector</h3>",
        "        <span class='panel-note'>Sort + symbol chips + pagination</span>",
        "        <button class='collapse-btn' data-collapsible='1' data-target='tradePanelBody' aria-expanded='true' title='Collapse section'>?</button>",
        "      </div>",
        "      <div class='panel-body' id='tradePanelBody'>",
        "        <div class='toolbar inspector-meta'>",
        "          <input id='tradeSymbol' placeholder='symbol contains'>",
        "          <select id='tradeDirection'><option value=''>All Directions</option>" +
        "".join([f"<option value='{d}'>{d}</option>" for d in trade_dirs]) +
        "</select>",
        "          <select id='tradeSort'><option value='pnl_desc'>PnL abs desc</option><option value='pnl_asc'>PnL abs asc</option><option value='qty_desc'>Quantity desc</option></select>",
        "          <input id='tradePnl' type='number' placeholder='Min abs(PnL)' step='0.01'>",
        "          <input id='tradeQty' type='number' placeholder='Min abs(quantity)' step='0.001'>",
        "          <button id='tradeReset'>Reset</button>",
        "          <button id='tradeExport'>Export visible CSV</button>",
        "          <button id='tradeExportAll'>Export filtered CSV</button>",
        "          <select id='tradePageSize'><option value='25'>25</option><option value='50'>50</option><option value='100'>100</option><option value='200' selected>200</option><option value='500'>500</option><option value='1000'>1000</option></select>",
        "          <span id='tradeCount' class='small counter'></span>",
        "        </div>",
        "        <div id='tradeChips' class='chips'></div>",
        "        <div class='table-wrap'><table><thead><tr><th>symbol</th><th>direction</th><th>quantity</th><th>entry</th><th>exit</th><th>pnl</th><th>entry_cost</th><th>exit_cost</th><th>start_ts</th><th>end_ts</th></tr></thead><tbody id='tradeBody'></tbody></table></div>",
        "        <p id='tradeMeta' class='small counter'>Selected trade: none</p>",
        "        <pre id='tradeDetail' class='detail'>Click a trade row to inspect details.</pre>",
        "        <button id='tradeCopyRow'>Copy selected row JSON</button>",
        "        <div class='pager'>",
        "            <button id='tradePrev'>Prev</button>",
        "            <span id='tradePager' class='small counter'>Page 1/1</span>",
        "            <button id='tradeNext'>Next</button>",
        "            <span id='tradePageHint' class='small counter right'>rows 0-0</span>",
        "        </div>",
        "        <div id='tradeEmpty' class='section-empty' style='display:none'>No trades match current filter.</div>",
        "      </div>",
        "    </section>",
        "  </div>",
        "  <script>",
        f"    const TRADE_DATA = {json.dumps(top_trades, ensure_ascii=False)};",
        f"    const EVENT_DATA = {json.dumps(recent_events, ensure_ascii=False)};",
        f"    const EQUITY_DATA = {json.dumps(equity_chart_points, ensure_ascii=False)};",
        "    const globalSearch = document.querySelector('#globalSearch');",
        "    const globalScope = document.querySelector('#globalScope');",
        "    const globalReset = document.querySelector('#globalReset');",
        "    const globalMatch = document.querySelector('#globalMatch');",
        "    const eventBody = document.querySelector('#eventBody');",
        "    const tradeBody = document.querySelector('#tradeBody');",
        "    const eventSymbol = document.querySelector('#eventSymbol');",
        "    const eventName = document.querySelector('#eventName');",
        "    const eventOrder = document.querySelector('#eventOrder');",
        "    const eventFrom = document.querySelector('#eventFrom');",
        "    const eventTo = document.querySelector('#eventTo');",
        "    const tradeSymbol = document.querySelector('#tradeSymbol');",
        "    const tradeSort = document.querySelector('#tradeSort');",
        "    const tradePnl = document.querySelector('#tradePnl');",
        "    const tradeQty = document.querySelector('#tradeQty');",
        "    const tradeDirection = document.querySelector('#tradeDirection');",
        "    const eventReset = document.querySelector('#eventReset');",
        "    const tradeReset = document.querySelector('#tradeReset');",
        "    const eventExport = document.querySelector('#eventExport');",
        "    const tradeExport = document.querySelector('#tradeExport');",
        "    const exportBundle = document.querySelector('#exportBundle');",
        "    const compactToggle = document.querySelector('#compactToggle');",
        "    const styleToggle = document.querySelector('#styleToggle');",
        "    const layoutSubtitle = document.querySelector('#layoutSubtitle');",
        f"    const bundleDownloadName = '{html.escape(bundle_download_name or '', quote=True)}';",
        "    const equityPanel = document.querySelector('#equityPanel');",
        "    const eventPanel = document.querySelector('#eventPanel');",
        "    const tradePanel = document.querySelector('#tradePanel');",
        "    const eventCount = document.querySelector('#eventCount');",
        "    const tradeCount = document.querySelector('#tradeCount');",
        "    const eventPageSize = document.querySelector('#eventPageSize');",
        "    const tradePageSize = document.querySelector('#tradePageSize');",
        "    const eventPrev = document.querySelector('#eventPrev');",
        "    const eventNext = document.querySelector('#eventNext');",
        "    const tradePrev = document.querySelector('#tradePrev');",
        "    const tradeNext = document.querySelector('#tradeNext');",
        "    const eventPager = document.querySelector('#eventPager');",
        "    const tradePager = document.querySelector('#tradePager');",
        "    const eventPageHint = document.querySelector('#eventPageHint');",
        "    const tradePageHint = document.querySelector('#tradePageHint');",
        "    const eventEmpty = document.querySelector('#eventEmpty');",
        "    const tradeEmpty = document.querySelector('#tradeEmpty');",
        "    const chartMeta = document.querySelector('#chartMeta');",
        "    const eqCursor = document.querySelector('#equityCursor');",
        "    const eqCrosshair = document.querySelector('#equityCrosshair');",
        "    const eqChart = document.querySelector('#equityChart');",
        "    const eventDetail = document.querySelector('#eventDetail');",
        "    const tradeDetail = document.querySelector('#tradeDetail');",
        "    const eventMeta = document.querySelector('#eventMeta');",
        "    const tradeMeta = document.querySelector('#tradeMeta');",
        "    const eventCopyRow = document.querySelector('#eventCopyRow');",
        "    const tradeCopyRow = document.querySelector('#tradeCopyRow');",
        "    const eventExportAll = document.querySelector('#eventExportAll');",
        "    const tradeExportAll = document.querySelector('#tradeExportAll');",
        "    const collapsibleButtons = document.querySelectorAll('[data-collapsible]');",
        "    const eventChips = document.querySelectorAll('#eventChips .chip');",
        "    let activeEventFilter = '';",
        "    let activeTradeFilter = '';",
        "    const eventPageState = { page: 1 };",
        "    const tradePageState = { page: 1 };",
        "    let eventFiltered = [];",
        "    let tradeFiltered = [];",
        "    let eventRows = [];",
        "    let tradeRows = [];",
        "    let selectedEventRow = null;",
        "    let selectedTradeRow = null;",
        "    let activeInspector = 'event';",
        "    let eventCursor = -1;",
        "    let tradeCursor = -1;",
        "    function setActiveInspector(kind) {",
        "      activeInspector = kind;",
        "      if (equityPanel) equityPanel.classList.remove('active');",
        "      eventPanel && eventPanel.classList.toggle('active', kind === 'event');",
        "      tradePanel && tradePanel.classList.toggle('active', kind === 'trade');",
        "    }",
        "    function num(v){ const n = Number(v); return Number.isFinite(n) ? n.toFixed(4) : String(v || ''); }",
        "    function safeText(v){ return String(v||'').replace(/[&<>\\\"']/g,(s)=>({\"&\":\"&amp;\",\"<\":\"&lt;\",\">\":\"&gt;\",\"\\\"\":\"&quot;\",\"'\":\"&#39;\"}[s])); }",
        "    function inRange(value, start, end){ const ts = String(value || ''); return (!start || ts >= start) && (!end || ts <= end); }",
        "    function clamp(n, min, max) { return Math.min(Math.max(n, min), max); }",
        "    function setCompactLayout(enabled) {",
        "      if (enabled) {",
        "        document.body.classList.add('compact');",
        "      } else {",
        "        document.body.classList.remove('compact');",
        "      }",
        "      if (compactToggle) {",
        "        compactToggle.textContent = enabled ? 'Compact View: On' : 'Compact View: Off';",
        "      }",
        "      try { localStorage.setItem('rrkal-compact', enabled ? '1' : '0'); } catch (e) {}",
        "    }",
        "    function setPhotoLayout(enabled) {",
        "      if (enabled) {",
        "        document.body.classList.add('photo');",
        "        document.body.classList.remove('classic');",
        "      } else {",
        "        document.body.classList.remove('photo');",
        "        document.body.classList.add('classic');",
        "      }",
        "      if (compactToggle) {",
        "        compactToggle.addEventListener('click', ()=>{",
        "          const isCompact = document.body.classList.contains('compact');",
        "          setCompactLayout(!isCompact);",
        "        });",
        "      }",
        "      if (styleToggle) {",
        "        styleToggle.textContent = enabled ? 'Photo Layout: On' : 'Photo Layout: Off';",
        "      }",
        "      if (layoutSubtitle) {",
        "        const modeText = enabled ? 'photo' : 'classic';",
        "        const compactText = document.body.classList.contains('compact') ? 'compact' : 'normal';",
        f"        layoutSubtitle.textContent = `RRKAL 2.0.0 photo-style pre-renderer mode=${{modeText}} | density=${{compactText}} | html rows: {inspect_cap_label}`;",
        "      }",
        "      try { localStorage.setItem('rrkal-photo-style', enabled ? '1' : '0'); } catch (e) {}",
        "    }",
        "    function normalizeText(value) { return String(value || '').toLowerCase(); }",
        "    function matchRowQuery(row, term) {",
        "      const blob = normalizeText(JSON.stringify(row || {}));",
        "      return normalizeText(row.symbol).includes(term) || blob.includes(term);",
        "    }",
        "    function globalMatches(row, kind) {",
        "      if (!globalSearch) return true;",
        "      const t = normalizeText(globalSearch.value || '');",
        "      if (!t) return true;",
        "      const scope = globalScope ? globalScope.value : 'all';",
        "      if (scope === 'events' && kind !== 'event') return false;",
        "      if (scope === 'trades' && kind !== 'trade') return false;",
        "      return matchRowQuery(row, t);",
        "    }",
        "    function parsePageSize(node){ const v = Number((node && node.value) || 0); return Number.isFinite(v) && v > 0 ? v : 200; }",
        "    function renderPageState(state, total, size){ return Math.max(1, Math.ceil(total / size)); }",
        "    function updatePager(nodePager, nodeHint, state, total, size) {",
        "      const pages = renderPageState(state, total, size);",
        "      state.page = clamp(state.page, 1, pages);",
        "      const offset = (state.page - 1) * size;",
        "      const end = Math.min(offset + size, total);",
        "      nodePager.textContent = `Page ${state.page}/${pages}`;",
        "      nodeHint.textContent = total === 0 ? 'rows 0-0' : `rows ${offset + 1}-${end}`;",
        "      return { offset, end };",
        "    }",
        "    function downloadBundle(filename='render_bundle.zip'){",
        "      const name = (typeof filename === 'string' && filename.trim()) ? filename.trim() : 'render_bundle.zip';",
        "      const link = document.createElement('a');",
        "      link.href = name;",
        "      link.download = name;",
        "      link.style.display = 'none';",
        "      document.body.appendChild(link);",
        "      link.click();",
        "      document.body.removeChild(link);",
        "    }",
        "    function downloadCSV(filename, rows){",
        "      if (!rows || rows.length === 0) return;",
        "      const headers = new Set();",
        "      rows.forEach((row)=>Object.keys(row).forEach((h)=>headers.add(h)));",
        "      const cols = Array.from(headers);",
        "      const esc = (v) => {",
        "        const t = String(v == null ? '' : v);",
        "        return /[\\\" ,\\n]/.test(t) ? '\"'+t.replace(/\"/g,'\"\"')+'\"' : t;",
        "      };",
        "      const lines = [cols.join(',')];",
        "      for (const row of rows) { lines.push(cols.map((h)=>esc(row[h])).join(',')); }",
        "      const blob = new Blob([lines.join('\\n')], { type: 'text/csv;charset=utf-8;' });",
        "      const link = document.createElement('a');",
        "      link.href = URL.createObjectURL(blob);",
        "      link.download = filename;",
        "      link.style.display = 'none';",
        "      document.body.appendChild(link);",
        "      link.click();",
        "      document.body.removeChild(link);",
        "      URL.revokeObjectURL(link.href);",
        "    }",
        "    function clearRowSelection(kind) {",
        "      const body = kind === 'event' ? eventBody : tradeBody;",
        "      if (!body) return;",
        "      body.querySelectorAll('tr.row-selected').forEach((row) => row.classList.remove('row-selected'));",
        "    }",
        "    function setSelectedEvent(row){",
        "      selectedEventRow = row;",
        "      eventCursor = row ? eventFiltered.indexOf(row) : -1;",
        "      eventDetail.textContent = row ? JSON.stringify(row, null, 2) : 'Click an event row to inspect details.';",
        "      eventMeta.textContent = row ? `Selected event: ${safeText(row.event || '')} @ ${safeText(row.timestamp || '')}` : 'Selected event: none';",
        "      if (row && row.timestamp) { jumpEquityCursorByTimestamp(row.timestamp, `sync from event`); }",
        "      clearRowSelection('event');",
        "      if (eventCursor >= 0) {",
        "        const tr = eventBody.querySelector(`tr[data-row-index='${eventCursor}']`);",
        "        if (tr) tr.classList.add('row-selected');",
        "      }",
        "    }",
        "    function setSelectedTrade(row){",
        "      selectedTradeRow = row;",
        "      tradeCursor = row ? tradeFiltered.indexOf(row) : -1;",
        "      tradeDetail.textContent = row ? JSON.stringify(row, null, 2) : 'Click a trade row to inspect details.';",
        "      tradeMeta.textContent = row ? `Selected trade: ${safeText(row.symbol || '')} ${safeText(row.direction || '')}` : 'Selected trade: none';",
        "      if (row) { const tradeTs = row.end_ts || row.start_ts; if (tradeTs) { jumpEquityCursorByTimestamp(tradeTs, `sync from trade`); } }",
        "      clearRowSelection('trade');",
        "      if (tradeCursor >= 0) {",
        "        const tr = tradeBody.querySelector(`tr[data-row-index='${tradeCursor}']`);",
        "        if (tr) tr.classList.add('row-selected');",
        "      }",
        "    }",
        "    function copyRowText(text) {",
        "      if (!text) return;",
        "      if (navigator.clipboard && navigator.clipboard.writeText) {",
        "        navigator.clipboard.writeText(text).catch(()=>{});",
        "        return;",
        "      }",
        "      const ta = document.createElement('textarea');",
        "      ta.value = text;",
        "      document.body.appendChild(ta);",
        "      ta.select();",
        "      document.execCommand('copy');",
        "      document.body.removeChild(ta);",
        "    }",
        "    function bindRowSelection(kind, rows, offset) {",
        "      const tableId = kind === 'event' ? '#eventBody' : '#tradeBody';",
        "      const body = document.querySelector(tableId);",
        "      if (!body) return;",
        "      const trs = body.querySelectorAll('tr[data-row-index]');",
        "      trs.forEach((tr) => {",
        "        const idx = Number(tr.getAttribute('data-row-index'));",
        "        const row = rows[idx - offset];",
        "        tr.addEventListener('click', () => {",
        "          setActiveInspector(kind);",
        "          if (kind === 'event') { setSelectedEvent(row || null); } else { setSelectedTrade(row || null); }",
        "        });",
        "      });",
        "    }",
        "    function bindSymbolFilter(kind) {",
        "      const body = document.querySelector(kind === 'event' ? '#eventBody' : '#tradeBody');",
        "      const targetInput = kind === 'event' ? eventSymbol : tradeSymbol;",
        "      const targetPageState = kind === 'event' ? eventPageState : tradePageState;",
        "      if (!body) return;",
        "      body.querySelectorAll('[data-symbol]').forEach((el) => {",
        "        const symbol = el.getAttribute('data-symbol') || '';",
        "        el.addEventListener('click', (ev) => {",
        "          ev.preventDefault();",
        "          ev.stopPropagation();",
        "          targetInput.value = symbol;",
        "          targetPageState.page = 1;",
        "          if (kind === 'event') { eventSymbol.value = symbol; tradeSymbol.value = symbol; renderEvents(); renderTrades(); } else { renderTrades(); }",
        "          updateGlobalBadge();",
        "        });",
        "      });",
        "    }",
        "    function renderEvents(){",
        "      const sym = (eventSymbol.value || '').toLowerCase().trim();",
        "      const name = eventName.value;",
        "      const asc = eventOrder.value === 'asc';",
        "      const start = String(eventFrom.value || '').trim();",
        "      const end = String(eventTo.value || '').trim();",
        "      let list = EVENT_DATA.filter((row)=>{",
        "        if (!globalMatches(row, 'event')) return false;",
        "        if (sym && !String(row.symbol || '').toLowerCase().includes(sym)) return false;",
        "        if (name && row.event !== name) return false;",
        "        if (activeEventFilter && row.event !== activeEventFilter) return false;",
        "        if (!inRange(row.timestamp, start, end)) return false;",
        "        return true;",
        "      });",
        "      list.sort((a,b)=>{ const aTs=String(a.timestamp||''); const bTs=String(b.timestamp||''); return asc ? (aTs > bTs ? 1 : -1) : (aTs < bTs ? 1 : -1); });",
        "      eventFiltered = list;",
        "      const size = parsePageSize(eventPageSize);",
        "      const { offset } = updatePager(eventPager, eventPageHint, eventPageState, list.length, size);",
        "      const page = list.slice(offset, offset + size);",
        "      eventRows = page;",
        "      if (selectedEventRow && list.indexOf(selectedEventRow) === -1) { setSelectedEvent(null); }",
        "      if (eventCursor >= list.length) { eventCursor = -1; }",
        "      if (eventCursor !== -1 && eventCursor < offset) { eventCursor = -1; }",
        "      eventBody.innerHTML = page.map((row, idx)=>`<tr data-row-index='${offset + idx}'><td>${safeText(row.event||'')}</td><td><span data-symbol='${safeText(row.symbol||'')}' class='symbol-link'>${safeText(row.symbol||'')}</span></td><td>${safeText(row.timestamp||'')}</td><td><pre>${safeText(JSON.stringify(row.details||{}))}</pre></td></tr>`).join('');",
        "      eventCount.textContent = `Matched: ${list.length} / ${EVENT_DATA.length}`;",
        "      eventEmpty.style.display = list.length === 0 ? 'block' : 'none';",
        "      if (list.length === 0) { eventBody.innerHTML = ''; }",
        "      const pages = renderPageState(eventPageState, list.length, size);",
        "      eventPrev.disabled = eventPageState.page <= 1 || pages <= 1;",
        "      eventNext.disabled = eventPageState.page >= pages || pages <= 1;",
        "      bindRowSelection('event', eventRows, offset);",
        "      applyCursorSelection('event');",
        "      bindSymbolFilter('event');",
        "      if (globalMatch) { updateGlobalBadge(); }",
        "      return list;",
        "    }",
        "    function renderTrades(){",
        "      const sym = (tradeSymbol.value || '').toLowerCase().trim();",
        "      const p = Number(tradePnl.value);",
        "      const q = Number(tradeQty.value);",
        "      const dir = (tradeDirection.value || '').trim().toLowerCase();",
        "      let list = TRADE_DATA.slice();",
        "      list = list.filter((row)=>globalMatches(row, 'trade'));",
        "      if (sym) list = list.filter((row)=>String(row.symbol || '').toLowerCase().includes(sym));",
        "      if (Number.isFinite(p)) list = list.filter((row)=>Math.abs(Number(row.pnl || 0)) >= Math.abs(p));",
        "      if (Number.isFinite(q)) list = list.filter((row)=>Math.abs(Number(row.quantity || 0)) >= Math.abs(q));",
        "      if (activeTradeFilter) list = list.filter((row)=>String(row.symbol || '').toLowerCase() === activeTradeFilter.toLowerCase());",
        "      if (dir) list = list.filter((row)=>String(row.direction || '').toLowerCase() === dir);",
        "      if (tradeSort.value === 'pnl_desc') list.sort((a,b)=>Math.abs(Number(b.pnl||0))-Math.abs(Number(a.pnl||0)));",
        "      if (tradeSort.value === 'pnl_asc') list.sort((a,b)=>Math.abs(Number(a.pnl||0))-Math.abs(Number(b.pnl||0)));",
        "      if (tradeSort.value === 'qty_desc') list.sort((a,b)=>Math.abs(Number(b.quantity||0))-Math.abs(Number(a.quantity||0)));",
        "      tradeFiltered = list;",
        "      const size = parsePageSize(tradePageSize);",
        "      const { offset } = updatePager(tradePager, tradePageHint, tradePageState, list.length, size);",
        "      const page = list.slice(offset, offset + size);",
        "      tradeRows = page;",
        "      if (selectedTradeRow && list.indexOf(selectedTradeRow) === -1) { setSelectedTrade(null); }",
        "      if (tradeCursor >= list.length) { tradeCursor = -1; }",
        "      if (tradeCursor !== -1 && tradeCursor < offset) { tradeCursor = -1; }",
        "      tradeBody.innerHTML = page.map((row, idx)=>{ const cls = Number(row.pnl||0)>=0 ? 'up' : 'down'; return `<tr data-row-index='${offset + idx}'><td><span data-symbol='${safeText(row.symbol||'')}' class='symbol-link'>${safeText(row.symbol||'')}</span></td><td>${safeText(row.direction||'')}</td><td>${num(row.quantity||0)}</td><td>${num(row.entry||0)}</td><td>${num(row.exit||0)}</td><td class='${cls}'>${num(row.pnl||0)}</td><td>${num(row.entry_cost||0)}</td><td>${num(row.exit_cost||0)}</td><td>${safeText(row.start_ts||'')}</td><td>${safeText(row.end_ts||'')}</td></tr>`; }).join('');",
        "      tradeCount.textContent = `Matched: ${list.length} / ${TRADE_DATA.length}`;",
        "      tradeEmpty.style.display = list.length === 0 ? 'block' : 'none';",
        "      if (list.length === 0) { tradeBody.innerHTML = ''; }",
        "      const pages = renderPageState(tradePageState, list.length, size);",
        "      tradePrev.disabled = tradePageState.page <= 1 || pages <= 1;",
        "      tradeNext.disabled = tradePageState.page >= pages || pages <= 1;",
        "      bindRowSelection('trade', tradeRows, offset);",
        "      applyCursorSelection('trade');",
        "      bindSymbolFilter('trade');",
        "      if (globalMatch) { updateGlobalBadge(); }",
        "      return list;",
        "    }",
        "    function renderTradeChips(){",
        "      const chipsNode = document.querySelector('#tradeChips');",
        "      if (!chipsNode) return;",
        f"      chipsNode.innerHTML = '';",
        f"      const tradeChipsSource = {json.dumps(trade_chip_names, ensure_ascii=False)};",
        "      tradeChipsSource.forEach((symbol)=>{",
        "        const chip = document.createElement('span');",
        "        chip.className = 'chip';",
        "        chip.setAttribute('data-symbol', symbol);",
        "        chip.textContent = symbol;",
        "        chipsNode.appendChild(chip);",
        "      });",
        "    }",
        "    function applyCursorSelection(kind) {",
        "      const cursor = kind === 'event' ? eventCursor : tradeCursor;",
        "      if (cursor < 0) return;",
        "      const pageSize = parsePageSize(kind === 'event' ? eventPageSize : tradePageSize);",
        "      const pageState = kind === 'event' ? eventPageState : tradePageState;",
        "      const targetPage = Math.floor(cursor / pageSize) + 1;",
        "      if (pageState.page !== targetPage) return;",
        "      const body = kind === 'event' ? eventBody : tradeBody;",
        "      const tr = body ? body.querySelector(`tr[data-row-index='${cursor}']`) : null;",
        "      if (!tr) return;",
        "      clearRowSelection(kind);",
        "      tr.classList.add('row-selected');",
        "    }",
        "    function updateGlobalBadge() {",
        "      if (!globalMatch || !globalSearch) return;",
        "      const q = normalizeText(globalSearch.value || '');",
        "      const scope = globalScope ? globalScope.value : 'all';",
        "      if (!q) {",
        "        globalMatch.textContent = 'No global query';",
        "        return;",
        "      }",
        "      if (scope === 'events') {",
        "        const e = EVENT_DATA.filter((row) => globalMatches(row, 'event')).length;",
        "        globalMatch.textContent = `Global: ${e}/${EVENT_DATA.length} events`;",
        "        return;",
        "      }",
        "      if (scope === 'trades') {",
        "        const t = TRADE_DATA.filter((row) => globalMatches(row, 'trade')).length;",
        "        globalMatch.textContent = `Global: ${t}/${TRADE_DATA.length} trades`;",
        "        return;",
        "      }",
        "      const e = EVENT_DATA.filter((row) => globalMatches(row, 'event')).length;",
        "      const t = TRADE_DATA.filter((row) => globalMatches(row, 'trade')).length;",
        "      globalMatch.textContent = `Global: ${e}/${EVENT_DATA.length} events, ${t}/${TRADE_DATA.length} trades`;",
        "    }",
        "    function buildChartScale() {",
        "      const n = EQUITY_DATA.length;",
        "      if (!n) return null;",
        "      const values = EQUITY_DATA.map((item) => Number(item.value) || 0);",
        "      const eqMin = Math.min(...values);",
        "      const eqMax = Math.max(...values);",
        "      return { n, eqMin: Number.isFinite(eqMin) ? eqMin : 0, eqMax: Number.isFinite(eqMax) ? eqMax : 0 };",
        "    }",
        "    function findNearestEquityIndexByTs(list, targetTs) {",
        "      if (!Array.isArray(list) || list.length === 0) return -1;",
        "      const target = Number(Date.parse(String(targetTs)));",
        "      if (!Number.isFinite(target)) return -1;",
        "      let bestIdx = -1;",
        "      let bestDistance = Infinity;",
        "      for (let i = 0; i < list.length; i++) {",
        "        const item = list[i] || {};",
        "        const ts = String(item.ts != null ? item.ts : item.label || '');",
        "        if (!ts) continue;",
        "        const parsed = Number(Date.parse(ts));",
        "        if (!Number.isFinite(parsed)) continue;",
        "        const d = Math.abs(parsed - target);",
        "        if (d < bestDistance) {",
        "          bestDistance = d;",
        "          bestIdx = i;",
        "        }",
        "      }",
        "      return bestIdx;",
        "    }",
        "    function applyEquityCursorFromData(point) {",
        "      if (!point || !eqCursor || !eqCrosshair || !chartMeta) return;",
        "      const { idx, row, value, px, py } = point;",
        "      eqCursor.setAttribute('cx', String(px));",
        "      eqCursor.setAttribute('cy', String(py));",
        "      eqCrosshair.setAttribute('x1', String(px));",
        "      eqCrosshair.setAttribute('x2', String(px));",
        "      eqCursor.style.opacity = '1';",
        "      const scale = buildChartScale();",
        "      if (!scale) {",
        "        chartMeta.textContent = 'No point data';",
        "        return;",
        "      }",
        "      chartMeta.textContent = `equity sample #${idx + 1}: ts=${safeText(row.label || '')} | value=${num(value)} | range=${num(scale.eqMin)} ~ ${num(scale.eqMax)}`;",
        "    }",
        "    function renderEquityCursor(evt) {",
        "      if (!eqCursor || !eqCrosshair || !chartMeta) return;",
        "      const point = getEquityPointFromEvent(evt);",
        "      if (!point || !point.row) return;",
        "      const scale = buildChartScale();",
        "      if (!scale) {",
        "        chartMeta.textContent = 'No point data';",
        "        return;",
        "      }",
        "      const { idx, row, value, px, py } = {",
        "        idx: point.idx, row: point.row, value: Number(point.row.value) || 0, px: point.px, py: point.py",
        "      };",
        "      applyEquityCursorFromData({ idx, row, value, px, py });",
        "    }",
        "    function jumpEquityCursorByTimestamp(ts, reason) {",
        "      if (!ts || !EQUITY_DATA.length) return;",
        "      const idx = findNearestEquityIndexByTs(EQUITY_DATA, ts);",
        "      if (idx < 0) return;",
        "      const scale = buildChartScale();",
        "      if (!scale) return;",
        "      const row = EQUITY_DATA[idx] || {};",
        "      const value = Number(row.value) || 0;",
        "      const denom = (scale.eqMax - scale.eqMin) === 0 ? 1 : (scale.eqMax - scale.eqMin);",
        "      const py = 324 - ((value - scale.eqMin) / denom) * 288;",
        "      const px = 36 + (1044 - 36) * (idx / Math.max(scale.n - 1, 1));",
        "      applyEquityCursorFromData({ idx, row, value, px, py });",
        "      if (reason && chartMeta) {",
        "        const marker = `| ${safeText(reason)}`;",
        "        const baseText = chartMeta.textContent || '';",
        "        chartMeta.textContent = baseText.includes(marker) ? baseText : `${baseText} ${marker}`;",
        "      }",
        "    }",
        "    function getEquityPointFromEvent(evt) {",
        "      if (!eqChart) return null;",
        "      const scale = buildChartScale();",
        "      if (!scale) return null;",
        "      const rect = eqChart.getBoundingClientRect();",
        "      const x = evt.clientX - rect.left;",
        "      const ratio = Math.max(0, Math.min(1, (x - 36) / (1044 - 36)));",
        "      const idx = Math.round(ratio * (scale.n - 1));",
        "      const row = EQUITY_DATA[idx];",
        "      const value = Number(row?.value) || 0;",
        "      const denom = (scale.eqMax - scale.eqMin) === 0 ? 1 : (scale.eqMax - scale.eqMin);",
        "      const py = 324 - ((value - scale.eqMin) / denom) * (288);",
        "      const px = 36 + (1044 - 36) * ratio;",
        "      return { idx, row, px, py, ratio };",
        "    }",
        "    function getPageState(kind) { return kind === 'event' ? eventPageState : tradePageState; }",
        "    function getPageSize(kind) { return parsePageSize(kind === 'event' ? eventPageSize : tradePageSize); }",
        "    function getPageData(kind) { return kind === 'event' ? eventFiltered : tradeFiltered; }",
        "    function copyCurrentPageRow(kind, useLast) {",
        "      const list = getPageData(kind);",
        "      const size = getPageSize(kind);",
        "      const state = getPageState(kind);",
        "      const totalPages = Math.max(1, Math.ceil((list.length || 0) / size));",
        "      const page = clamp(state.page, 1, totalPages) - 1;",
        "      const offset = page * size;",
        "      const idx = useLast ? Math.min(list.length - 1, offset + size - 1) : offset;",
        "      const row = list[idx] || null;",
        "      copyRowText(row ? JSON.stringify(row, null, 2) : '');",
        "    }",
        "    function syncEventWindowFromEquityPoint(idx, row) {",
        "      if (!row) return;",
        "      const ts = String(row.label || row.ts || '').trim();",
        "      if (!ts) return;",
        "      if (tradeBody) {",
        "        const nearestTrade = findNearestTradeIndexByTs(tradeFiltered, ts);",
        "        if (nearestTrade >= 0) {",
        "          const targetPage = Math.floor(nearestTrade / parsePageSize(tradePageSize)) + 1;",
        "          if (tradePageState.page !== targetPage) {",
        "            tradePageState.page = targetPage;",
        "            renderTrades();",
        "          }",
        "          const matchedTrade = tradeFiltered[nearestTrade] || null;",
        "          setSelectedTrade(matchedTrade);",
        "          tradeCursor = nearestTrade;",
        "          jumpCursor('trade', nearestTrade);",
        "        } else {",
        "          setSelectedTrade(null);",
        "          tradeCursor = -1;",
        "          applyCursorSelection('trade');",
        "        }",
        "      }",
        "      if (eventFrom) eventFrom.value = ts;",
        "      if (eventTo) eventTo.value = ts;",
        "      renderEvents();",
        "      setActiveInspector('event');",
        "      if (eventBody && eventBody.querySelector('tr[data-row-index]')) {",
        "        eventBody.querySelectorAll('tr[data-row-index]').forEach((tr)=>{ tr.classList.remove('row-selected'); });",
        "      }",
        "      const nearest = findNearestEventIndexByTs(eventFiltered, ts);",
        "      if (nearest >= 0) {",
        "        const targetPage = Math.floor(nearest / parsePageSize(eventPageSize)) + 1;",
        "        if (eventPageState.page !== targetPage) {",
        "          eventPageState.page = targetPage;",
        "          renderEvents();",
        "        }",
        "        const i = nearest;",
        "        const exact = eventFiltered[i] || null;",
        "        setSelectedEvent(exact);",
        "        eventCursor = i;",
        "        jumpCursor('event', i);",
        "        if (chartMeta) {",
        "          chartMeta.textContent = `equity sample #${idx + 1}: ts=${safeText(ts)} | event synced to row #${i + 1}`;",
        "        }",
        "      } else {",
        "        setSelectedEvent(null);",
        "        eventCursor = -1;",
        "        applyCursorSelection('event');",
        "        if (chartMeta) {",
        "          const current = chartMeta.textContent;",
        "          chartMeta.textContent = `${current} | event window synced to ${ts}`;",
        "        }",
        "      }",
        "    }",
        "    function findNearestTradeIndexByTs(list, targetTs) {",
        "      if (!Array.isArray(list) || list.length === 0) return -1;",
        "      const target = Number(Date.parse(String(targetTs)));",
        "      if (!Number.isFinite(target)) return -1;",
        "      let bestIdx = -1;",
        "      let bestDistance = Infinity;",
        "      for (let i = 0; i < list.length; i++) {",
        "        const item = list[i] || {};",
        "        const tsCandidates = [item.start_ts, item.end_ts];",
        "        let rowBestDistance = Infinity;",
        "        for (const tsRaw of tsCandidates) {",
        "          const ts = String(tsRaw || '');",
        "          if (!ts) continue;",
        "          const parsed = Number(Date.parse(ts));",
        "          if (!Number.isFinite(parsed)) continue;",
        "          const d = Math.abs(parsed - target);",
        "          if (d < rowBestDistance) rowBestDistance = d;",
        "        }",
        "        if (rowBestDistance < bestDistance) {",
        "          bestDistance = rowBestDistance;",
        "          bestIdx = i;",
        "        }",
        "      }",
        "      return bestIdx;",
        "    }",
        "    function findNearestEventIndexByTs(list, targetTs) {",
        "      if (!Array.isArray(list) || list.length === 0) return -1;",
        "      const target = Number(Date.parse(String(targetTs)));",
        "      let bestIdx = -1;",
        "      let bestDistance = Infinity;",
        "      const hasNumeric = Number.isFinite(target);",
        "      for (let i = 0; i < list.length; i++) {",
        "        const ts = String(list[i] && list[i].timestamp ? list[i].timestamp : '');",
        "        if (ts === String(targetTs)) return i;",
        "        if (!hasNumeric) continue;",
        "        const n = Number(Date.parse(ts));",
        "        if (!Number.isFinite(n)) continue;",
        "        const d = Math.abs(n - target);",
        "        if (d < bestDistance) { bestDistance = d; bestIdx = i; }",
        "      }",
        "      if (bestIdx >= 0) return bestIdx;",
        "      for (let i = 0; i < list.length; i++) {",
        "        const ts = String(list[i] && list[i].timestamp ? list[i].timestamp : '');",
        "        if (!ts) continue;",
        "        return i;",
        "      }",
        "      return -1;",
        "    }",
        "    function moveCursor(kind, delta) {",
        "      const pageSize = parsePageSize(kind === 'event' ? eventPageSize : tradePageSize);",
        "      const list = kind === 'event' ? eventFiltered : tradeFiltered;",
        "      const pageState = kind === 'event' ? eventPageState : tradePageState;",
        "      const cursorRef = kind === 'event' ? 'eventCursor' : 'tradeCursor';",
        "      if (!list.length) return;",
        "      if (window[cursorRef] < 0) {",
        "        window[cursorRef] = Math.min(list.length - 1, (pageState.page - 1) * pageSize);",
        "      }",
        "      window[cursorRef] = clamp(window[cursorRef] + delta, 0, list.length - 1);",
        "      const nextPage = Math.floor(window[cursorRef] / pageSize) + 1;",
        "      if (nextPage !== pageState.page) {",
        "        pageState.page = nextPage;",
        "        if (kind === 'event') { renderEvents(); } else { renderTrades(); }",
        "        return;",
        "      }",
        "      const cursorRow = list[window[cursorRef]] || null;",
        "      if (kind === 'event') { setSelectedEvent(cursorRow); }",
        "      if (kind === 'trade') { setSelectedTrade(cursorRow); }",
        "      applyCursorSelection(kind);",
        "    }",
        "    function jumpCursor(kind, targetIndex) {",
        "      const pageSize = parsePageSize(kind === 'event' ? eventPageSize : tradePageSize);",
        "      const list = kind === 'event' ? eventFiltered : tradeFiltered;",
        "      const pageState = kind === 'event' ? eventPageState : tradePageState;",
        "      const cursorRef = kind === 'event' ? 'eventCursor' : 'tradeCursor';",
        "      if (!list.length) return;",
        "      const target = clamp(Math.round(targetIndex), 0, list.length - 1);",
        "      window[cursorRef] = target;",
        "      const targetPage = Math.floor(target / pageSize) + 1;",
        "      if (targetPage !== pageState.page) {",
        "        pageState.page = targetPage;",
        "        if (kind === 'event') { renderEvents(); } else { renderTrades(); }",
        "      }",
        "      const row = list[target] || null;",
        "      if (kind === 'event') { setSelectedEvent(row); }",
        "      if (kind === 'trade') { setSelectedTrade(row); }",
        "      if (kind === 'event' || kind === 'trade') { applyCursorSelection(kind); }",
        "    }",
        "    function setPanelState(targetId, collapsed, persist) {",
        "      const body = targetId ? document.getElementById(targetId) : null;",
        "      const btn = document.querySelector(`[data-target=\\\"${targetId}\\\"]`);",
        "      if (!body || !btn) return;",
        "      body.classList.toggle('hidden', !!collapsed);",
        "      btn.setAttribute('aria-expanded', String(!collapsed));",
        "      btn.textContent = collapsed ? '?' : '?';",
        "      btn.title = collapsed ? 'Expand section' : 'Collapse section';",
        "      if (persist) {",
        "        try { localStorage.setItem(`rrkal-panel-${targetId}`, collapsed ? '1' : '0'); } catch (e) {}",
        "      }",
        "    }",
        "    function initPanelStates() {",
        "      collapsibleButtons.forEach((btn) => {",
        "        const targetId = btn.dataset.target;",
        "        try {",
        "          const saved = localStorage.getItem(`rrkal-panel-${targetId}`);",
        "          if (saved === '1') { setPanelState(targetId, true, false); return; }",
        "        } catch (e) {}",
        "        setPanelState(targetId, false, false);",
        "      });",
        "      collapsibleButtons.forEach((btn) => {",
        "        btn.addEventListener('click', () => {",
        "          const targetId = btn.dataset.target;",
        "          const body = document.getElementById(targetId);",
        "          if (!body) return;",
        "          setPanelState(targetId, !body.classList.contains('hidden'), true);",
        "        });",
        "      });",
        "    }",
        "    function refreshTradeChipsActive() {",
        "      const tradeChips = document.querySelectorAll('#tradeChips .chip');",
        "      tradeChips.forEach((el)=>el.setAttribute('data-active', el.getAttribute('data-symbol') === activeTradeFilter ? '1' : '0'));",
        "    }",
        "    function bind(){",
        "      if (globalSearch) {",
        "        globalSearch.addEventListener('input', ()=>{ eventPageState.page = 1; tradePageState.page = 1; renderEvents(); renderTrades(); updateGlobalBadge(); });",
        "        globalSearch.addEventListener('change', ()=>{renderEvents(); renderTrades(); updateGlobalBadge();});",
        "      }",
        "      if (globalScope) {",
        "        globalScope.addEventListener('change', ()=>{ eventPageState.page = 1; tradePageState.page = 1; renderEvents(); renderTrades(); updateGlobalBadge(); });",
        "      }",
        "      if (globalReset) {",
        "        globalReset.addEventListener('click', ()=>{",
        "          if (globalSearch) globalSearch.value = '';",
        "          if (globalScope) globalScope.value = 'all';",
        "          eventPageState.page = 1; tradePageState.page = 1; renderEvents(); renderTrades(); updateGlobalBadge();",
        "        });",
        "      }",
        "      if (eqChart) {",
        "        eqChart.addEventListener('mousemove', (evt)=>renderEquityCursor(evt));",
        "        eqChart.addEventListener('click', (evt)=>{",
        "          const point = getEquityPointFromEvent(evt);",
        "          if (!point) return;",
        "          renderEquityCursor(evt);",
        "          syncEventWindowFromEquityPoint(point.idx, point.row);",
        "        });",
        "        eqChart.addEventListener('mouseleave', ()=>{ if (chartMeta) { chartMeta.textContent = 'No point selected.'; } if (eqCursor) eqCursor.style.opacity = '0'; });",
        "      }",
        "      [eventSymbol, eventName, eventOrder, eventFrom, eventTo, tradeSymbol, tradeSort, tradePnl, tradeQty, tradeDirection].forEach((node)=>{",
        "        if (!node) return;",
        "        node.addEventListener('input', ()=>{",
        "          if (node === tradeSymbol) { activeTradeFilter = ''; refreshTradeChipsActive(); }",
        "          eventPageState.page = 1; tradePageState.page = 1; renderEvents(); renderTrades();",
        "          updateGlobalBadge();",
        "        });",
        "        node.addEventListener('change', ()=>{renderEvents(); renderTrades(); updateGlobalBadge();});",
        "      });",
        "      [eventPageSize, tradePageSize].forEach((node)=>{",
        "        if (!node) return;",
        "        node.addEventListener('change', ()=>{",
        "          if (node === eventPageSize) { eventPageState.page = 1; }",
        "          if (node === tradePageSize) { tradePageState.page = 1; }",
        "          renderEvents(); renderTrades();",
        "        });",
        "      });",
        "      eventPrev.addEventListener('click', ()=>{ if (eventPageState.page > 1) { eventPageState.page -= 1; renderEvents(); } });",
        "      eventNext.addEventListener('click', ()=>{",
        "        if (eventNext.disabled) return;",
        "        eventPageState.page += 1;",
        "        renderEvents();",
        "      });",
        "      tradePrev.addEventListener('click', ()=>{ if (tradePageState.page > 1) { tradePageState.page -= 1; renderTrades(); } });",
        "      tradeNext.addEventListener('click', ()=>{",
        "        if (tradeNext.disabled) return;",
        "        tradePageState.page += 1;",
        "        renderTrades();",
        "      });",
        "      eventChips.forEach((chip)=>chip.addEventListener('click', ()=>{",
        "        const v = chip.getAttribute('data-event') || '';",
        "        activeEventFilter = activeEventFilter === v ? '' : v;",
        "        eventChips.forEach((el)=>el.setAttribute('data-active', el.getAttribute('data-event') === activeEventFilter ? '1' : '0'));",
        "        eventPageState.page = 1;",
        "        renderEvents();",
        "        updateGlobalBadge();",
        "      }));",
        "      const tradeChips = document.querySelectorAll('#tradeChips .chip');",
        "      tradeChips.forEach((chip)=>chip.addEventListener('click', ()=>{",
        "        const v = chip.getAttribute('data-symbol') || '';",
        "        activeTradeFilter = activeTradeFilter === v ? '' : v;",
        "        refreshTradeChipsActive();",
        "        tradePageState.page = 1;",
        "        renderTrades();",
        "        updateGlobalBadge();",
        "      }));",
        "      eventReset.addEventListener('click', ()=>{",
        "        eventSymbol.value=''; eventName.value=''; eventOrder.value='desc'; eventFrom.value=''; eventTo.value='';",
        "        activeEventFilter='';",
        "        eventPageState.page = 1;",
        "        eventChips.forEach((el)=>el.setAttribute('data-active', '0'));",
        "        setSelectedEvent(null);",
        "        renderEvents();",
        "        updateGlobalBadge();",
        "      });",
        "      tradeReset.addEventListener('click', ()=>{",
        "        tradeSymbol.value=''; tradePnl.value=''; tradeQty.value=''; tradeDirection.value=''; tradeSort.value='pnl_desc';",
        "        activeTradeFilter='';",
        "        tradePageState.page = 1;",
        "        tradeChips.forEach((el)=>el.setAttribute('data-active', '0'));",
        "        setSelectedTrade(null);",
        "        renderTrades();",
        "        updateGlobalBadge();",
        "      });",
        "      eventExport.addEventListener('click', ()=>{ const filename = 'events_visible_' + Date.now() + '.csv'; downloadCSV(filename, eventRows); });",
        "      tradeExport.addEventListener('click', ()=>{ const filename = 'trades_visible_' + Date.now() + '.csv'; downloadCSV(filename, tradeRows); });",
        "      eventExportAll.addEventListener('click', ()=>{ const filename = 'events_filtered_' + Date.now() + '.csv'; downloadCSV(filename, eventFiltered); });",
        "      tradeExportAll.addEventListener('click', ()=>{ const filename = 'trades_filtered_' + Date.now() + '.csv'; downloadCSV(filename, tradeFiltered); });",
        "      if (exportBundle) { exportBundle.addEventListener('click', ()=>{ downloadBundle(bundleDownloadName || 'render_bundle.zip'); }); }",
        "      eventCopyRow.addEventListener('click', ()=> copyRowText(selectedEventRow ? JSON.stringify(selectedEventRow, null, 2) : ''));",
        "      tradeCopyRow.addEventListener('click', ()=> copyRowText(selectedTradeRow ? JSON.stringify(selectedTradeRow, null, 2) : ''));",
        "      if (styleToggle) {",
        "        styleToggle.addEventListener('click', ()=>{",
        "          const isPhoto = document.body.classList.contains('photo');",
        "          setPhotoLayout(!isPhoto);",
        "        });",
        "      }",
        "      setActiveInspector('event');",
        "      eventSymbol.addEventListener('focus', ()=> setActiveInspector('event'));",
        "      tradeSymbol.addEventListener('focus', ()=> setActiveInspector('trade'));",
        "      eventBody.addEventListener('focusin', ()=> setActiveInspector('event'));",
        "      tradeBody.addEventListener('focusin', ()=> setActiveInspector('trade'));",
        "      document.addEventListener('keydown', (evt)=>{",
        "        const tag = document.activeElement ? document.activeElement.tagName.toLowerCase() : '';",
        "        const isTyping = ['input', 'textarea', 'select'].includes(tag) || (document.activeElement && document.activeElement.isContentEditable);",
        "        const k = evt.key.toLowerCase();",
        "        if (evt.shiftKey && evt.altKey && k === 'enter') { evt.preventDefault(); copyCurrentPageRow(activeInspector, true); return; }",
        "        if ((evt.metaKey || evt.ctrlKey) && evt.shiftKey && k === 'enter') { evt.preventDefault(); copyCurrentPageRow(activeInspector, false); return; }",
        "        if (evt.metaKey || evt.ctrlKey || isTyping) { return; }",
        "        if (evt.altKey) { return; }",
        "        if (evt.shiftKey && k === 'enter') { evt.preventDefault(); copyCurrentPageRow(activeInspector, false); return; }",
        "        if (k === 'j' || k === 'arrowleft') { evt.preventDefault();",
        "          if (activeInspector === 'event' && !eventPrev.disabled) { eventPrev.click(); }",
        "          if (activeInspector === 'trade' && !tradePrev.disabled) { tradePrev.click(); }",
        "          return;",
        "        }",
        "        if (k === 'k' || k === 'arrowright') { evt.preventDefault();",
        "          if (activeInspector === 'event' && !eventNext.disabled) { eventNext.click(); }",
        "          if (activeInspector === 'trade' && !tradeNext.disabled) { tradeNext.click(); }",
        "          return;",
        "        }",
        "        if (k === '[') { evt.preventDefault();",
        "          if (activeInspector === 'event' && !eventPrev.disabled) { eventPrev.click(); }",
        "          if (activeInspector === 'trade' && !tradePrev.disabled) { tradePrev.click(); }",
        "          return;",
        "        }",
        "        if (k === ']') { evt.preventDefault();",
        "          if (activeInspector === 'event' && !eventNext.disabled) { eventNext.click(); }",
        "          if (activeInspector === 'trade' && !tradeNext.disabled) { tradeNext.click(); }",
        "          return;",
        "        }",
        "        if (k === 'arrowup') { evt.preventDefault(); moveCursor(activeInspector, -1); return; }",
        "        if (k === 'arrowdown') { evt.preventDefault(); moveCursor(activeInspector, 1); return; }",
        "        if (k === 'enter') { evt.preventDefault();",
        "          if (activeInspector === 'event') { copyRowText(selectedEventRow ? JSON.stringify(selectedEventRow, null, 2) : ''); }",
        "          if (activeInspector === 'trade') { copyRowText(selectedTradeRow ? JSON.stringify(selectedTradeRow, null, 2) : ''); }",
        "          return;",
        "        }",
        "        if (k === '/') { evt.preventDefault(); if (globalSearch) { globalSearch.focus(); } return; }",
        "        if (k === 'f') { evt.preventDefault(); setActiveInspector('event'); eventSymbol.focus(); return; }",
        "        if (k === 't') { evt.preventDefault(); setActiveInspector('trade'); tradeSymbol.focus(); return; }",
        "        if (k === 'p') { evt.preventDefault(); if (styleToggle) { styleToggle.click(); } return; }",
        "        if (k === 'm') { evt.preventDefault(); if (compactToggle) { compactToggle.click(); } return; }",
        "        if (k === 'c') { evt.preventDefault();",
        "          if (activeInspector === 'event') { copyRowText(selectedEventRow ? JSON.stringify(selectedEventRow, null, 2) : ''); }",
        "          else { copyRowText(selectedTradeRow ? JSON.stringify(selectedTradeRow, null, 2) : ''); }",
        "          return;",
        "        }",
        "        if (k === 'home') { evt.preventDefault(); jumpCursor(activeInspector, 0); return; }",
        "        if (k === 'end') { evt.preventDefault();",
        "          const list = activeInspector === 'event' ? eventFiltered : tradeFiltered;",
        "          jumpCursor(activeInspector, list.length - 1);",
        "          return;",
        "        }",
        "        if (k === 'r') { evt.preventDefault();",
        "          if (activeInspector === 'event') { eventReset.click(); }",
        "          if (activeInspector === 'trade') { tradeReset.click(); }",
        "          return;",
        "        }",
        "        if (k === '0') { evt.preventDefault();",
        "          eventReset.click();",
        "          tradeReset.click();",
        "          setSelectedEvent(null);",
        "          setSelectedTrade(null);",
        "          return;",
        "        }",
        "      });",
        "    }",
        "    initPanelStates();",
        "    renderTradeChips();",
        "    bind();",
        "    renderEvents();",
        "    renderTrades();",
        "    try {",
        "      const saved = localStorage.getItem('rrkal-photo-style');",
        "      if (saved === '0') { setPhotoLayout(false); }",
        "      else if (saved === '1') { setPhotoLayout(true); }",
        "      else { setPhotoLayout(document.body.classList.contains('photo')); }",
        "      const compactSaved = localStorage.getItem('rrkal-compact');",
        "      if (compactSaved === '1') { setCompactLayout(true); }",
        "      else if (compactSaved === '0') { setCompactLayout(false); }",
        "      else { setCompactLayout(document.body.classList.contains('compact')); }",
        "      setPhotoLayout(document.body.classList.contains('photo'));",
        "    } catch (e) {",
        "      setPhotoLayout(document.body.classList.contains('photo'));",
        "      setCompactLayout(false);",
        "    }",
        "    updateGlobalBadge();",
        "  </script>",
        "</body>",
        "</html>",
    ])

    return "\\n".join(html_lines)


def _write_svg(path: Path, points: List[Tuple[float, float, str]], width: int = 1080, height: int = 360) -> None:
    d = _svg_polyline(points, width=width, height=height)
    _write_text(
        path,
        f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <rect width="100%" height="100%" fill="#020617"/>
  <path d="{d}" fill="none" stroke="#38bdf8" stroke-width="1.5"/>
</svg>""",
    )


def _build_batch_index(output_root: Path, outputs: List[Path]) -> None:
    items = []
    for out_dir in outputs:
        name = out_dir.name
        title = "RRKAL Render"
        md_path = out_dir / "report.md"
        if md_path.exists():
            first = md_path.read_text(encoding="utf-8").splitlines()
            if first:
                title = first[0].lstrip("# ").strip()
        items.append(f"<li><a href='{name}/report.html'>{title}</a> ({name})</li>")
    _write_text(
        output_root / "index.html",
        "<!doctype html><html><meta charset='utf-8'><body style='font-family:system-ui;padding:1rem'>"
        + f"<h1>RRKAL Render Batch</h1><ul>{''.join(items)}</ul></body></html>",
    )


def _render_payload(
    artifact_name: str,
    payload: Dict[str, Any],
    args: argparse.Namespace,
    output_dir: str = "",
) -> Path:
    _validate(payload, strict=not args.lenient)
    evidence = _resolve_evidence(payload)
    run_id = _resolve_run_id(payload)
    out_dir = Path(output_dir) if output_dir else _default_output_dir(run_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    equity_points = _extract_equity_points(evidence)
    trades = _extract_trades(evidence)
    events = _extract_events(evidence)
    sampled_equity = _downsample_points(
        equity_points,
        max_points=args.equity_max_points,
        method=args.equity_compress,
        rdp_epsilon=args.equity_rdp_epsilon,
    )
    rendered: List[str] = []
    pdf_status: Dict[str, Any] = {"requested": args.format in ("all", "pdf"), "success": False, "error": None, "path": "report.pdf"}

    if args.format in ("all", "md"):
        _write_text(out_dir / "report.md", _summary_markdown(payload, len(equity_points), len(sampled_equity)))
        rendered.append("report.md")

    html_content: str | None = None
    should_bundle_auto = (
        args.format in ("all", "md", "html", "json", "pdf", "svg")
        or args.emit_svg
        or args.export_csv
        or args.export_jsonl
    )
    if args.bundle is True:
        should_bundle = True
    elif args.bundle is False:
        should_bundle = False
    else:
        should_bundle = should_bundle_auto

    should_bundle_zip = should_bundle and not args.bundle_manifest_only
    bundle_download_name: str | None = (
        "bundle_manifest.json"
        if should_bundle and args.bundle_manifest_only
        else ("render_bundle.zip" if should_bundle_zip else None)
    )
    if args.format in ("all", "html", "pdf"):
        html_content = _to_html(
            payload=payload,
            title=args.title,
            bundle_download_name=bundle_download_name,
            max_equity_points=args.equity_max_points,
            equity_compress=args.equity_compress,
            rdp_epsilon=args.equity_rdp_epsilon,
            trade_max_rows=args.trade_max_rows,
            event_max_rows=args.event_max_rows,
            html_row_cap=args.html_row_cap,
            photo_style=args.photo_style,
            compact_layout=args.compact_layout,
        )

    if args.format in ("all", "html"):
        _write_text(
            out_dir / "report.html",
            html_content or "",
        )
        rendered.append("report.html")

    if args.format in ("all", "pdf"):
        if not html_content:
            raise RuntimeError("Unable to generate HTML content for PDF export")
        pdf_html = _inject_pdf_metadata(
            html_content,
            title=args.pdf_title if args.pdf_title else args.title,
            note=args.pdf_meta,
        )
        pdf_error = _write_pdf(out_dir / "report.pdf", pdf_html, required=(args.format == "pdf"))
        if pdf_error:
            if args.format == "all":
                _write_text(out_dir / "pdf_export_error.txt", pdf_error)
                rendered.append("pdf_export_error.txt")
                pdf_status.update(success=False, error=pdf_error)
            else:
                raise RuntimeError(pdf_error)
        else:
            rendered.append("report.pdf")
            pdf_status.update(success=True, error=None)
    else:
        pdf_status["requested"] = False
        pdf_status["success"] = True

    if args.emit_svg or args.format in ("all", "svg"):
        _write_svg(out_dir / "equity_curve.svg", sampled_equity)
        rendered.append("equity_curve.svg")

    if args.export_csv:
        _emit_csv(out_dir / "trades.csv", trades if args.trade_max_rows <= 0 else trades[: args.trade_max_rows])
        rendered.append("trades.csv")
        _emit_csv(
            out_dir / "equity_curve.csv",
            [dict(timestamp=entry[2], timestamp_index=entry[0], equity=entry[1]) for entry in equity_points],
        )
        rendered.append("equity_curve.csv")
        _emit_csv(
            out_dir / "events.csv",
            events if args.event_max_rows <= 0 else events[: args.event_max_rows],
        )
        rendered.append("events.csv")

    if args.export_jsonl:
        _emit_jsonl(out_dir / "events.jsonl", events)
        rendered.append("events.jsonl")

    if args.format in ("all", "json"):
        _write_text(
            out_dir / "preflight.json",
            json.dumps(
                {
                    "render_meta": {
                        "artifact_name": artifact_name,
                        "format": args.format,
                        "equity_compress": args.equity_compress,
                        "equity_max_points": args.equity_max_points,
                        "equity_rdp_epsilon": args.equity_rdp_epsilon,
                        "trade_max_rows": args.trade_max_rows,
                        "event_max_rows": args.event_max_rows,
                        "rendered_equity_points": len(sampled_equity),
                        "source_equity_points": len(equity_points),
                        "source_trade_count": len(trades),
                        "source_event_count": len(events),
                        "generated_at": datetime.utcnow().isoformat() + "Z",
                        "schema_version": payload.get("schema_version"),
                    },
                    "artifact": payload,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
        )
        rendered.append("preflight.json")

    rendered.append("render_summary.json")
    rendered.append("render_summary.html")
    if should_bundle:
        if args.bundle_manifest_only:
            bundle_file = "bundle_manifest.json"
            bundle_info = {
                "mode": "manifest",
                "path": bundle_file,
                "requested": True,
                "available": False,
                "reason": "no files eligible for manifest generation",
            }
            if _build_bundle_manifest(
                out_dir,
                rendered,
                bundle_name="render_bundle.zip",
                manifest_name=bundle_file,
            ):
                rendered.append(bundle_file)
                bundle_info["available"] = True
                bundle_info["reason"] = "bundle manifest generated"
        else:
            bundle_file = "render_bundle.zip"
            bundle_info = {
                "mode": "zip",
                "path": bundle_file,
                "requested": True,
                "available": False,
                "reason": "no files eligible for zip bundling",
            }
            if _write_bundle(out_dir, rendered, bundle_name=bundle_file):
                rendered.append(bundle_file)
                bundle_info["available"] = True
                bundle_info["reason"] = "bundle zip generated"
    else:
        if args.bundle is False:
            reason = "disabled by --no-bundle"
        elif should_bundle_auto:
            reason = "bundle generation failed"
        else:
            reason = "not requested by format/export options"
        bundle_info = {
            "mode": "none",
            "path": "render_bundle.zip",
            "requested": should_bundle,
            "available": False,
            "reason": reason,
        }

    _write_render_summary(
        out_dir,
        artifact_name,
        run_id,
        payload,
        args,
        rendered=rendered,
        pdf_status=pdf_status,
        bundle_info=bundle_info,
    )

    return out_dir


def _render_from_input(args: argparse.Namespace) -> List[Path]:
    items = list(_iter_artifact_sources(args.input))
    if not items:
        raise SystemExit(f"no valid artifact object found in {args.input}")
    if len(items) == 1:
        artifact_name, payload = items[0]
        return [_render_payload(artifact_name, payload, args, args.output_dir)]

    root = Path(args.output_dir) if args.output_dir else _default_output_dir("batch")
    outputs: List[Path] = []
    for artifact_name, payload in items:
        out_dir = root / _slugify(artifact_name)
        outputs.append(_render_payload(artifact_name, payload, args, str(out_dir)))
    _build_batch_index(root, outputs)
    return outputs


def cmd_render(args: argparse.Namespace) -> int:
    out_dirs = _render_from_input(args)
    if len(out_dirs) == 1:
        print(f"Rendered report: {out_dirs[0]}")
    else:
        print(f"Rendered report batch: {len(out_dirs)} files -> {out_dirs[0].parent}")
    return 0


def cmd_render_batch(args: argparse.Namespace) -> int:
    input_dir = Path(args.input_dir)
    if not input_dir.exists():
        raise SystemExit(f"input_dir not found: {input_dir}")
    patterns = [p.strip() for p in args.pattern.split(",")]
    files: List[Path] = []
    for pattern in patterns:
        files.extend(input_dir.glob(pattern))

    files = sorted(set(f for f in files if f.is_file()))
    if not files:
        raise SystemExit(f"no files found in {input_dir} with pattern: {args.pattern}")

    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    outputs: List[Path] = []
    for file in files:
        if file.suffix.lower() == ".zip":
            for artifact_name, payload in _iter_artifact_sources(str(file)):
                out_dir = output_root / _slugify(f"{file.stem}_{artifact_name}")
                outputs.append(_render_payload(artifact_name, payload, args, str(out_dir)))
            continue

        artifact_name, payload = next(_iter_artifact_sources(str(file)))
        out_dir = output_root / file.stem
        outputs.append(_render_payload(artifact_name, payload, args, str(out_dir)))

    _build_batch_index(output_root, outputs)
    print(f"Batch rendered: {len(outputs)} files -> {output_root}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    payload = _load_json(args.input)
    _validate(payload, strict=not args.lenient)
    print(f"RRKAL artifact valid: schema_version={payload.get('schema_version')}")
    return 0


def _validate_bundle_contract(summary: Dict[str, Any], base_dir: Path) -> List[str]:
    errors: List[str] = []
    outputs = summary.get("outputs")
    render_settings = summary.get("render_settings")
    if not isinstance(render_settings, dict):
        errors.append("render_settings must be an object")
    else:
        compact_setting = render_settings.get("compact_layout")
        if not isinstance(compact_setting, bool):
            errors.append("render_settings.compact_layout must be boolean")

    if not isinstance(outputs, dict):
        errors.append("outputs must be an object")
        return errors

    bundle = outputs.get("bundle")
    if not isinstance(bundle, dict):
        errors.append("outputs.bundle must be an object")
        return errors

    mode = bundle.get("mode")
    if mode not in {"zip", "manifest", "none"}:
        errors.append("outputs.bundle.mode must be one of: zip, manifest, none")

    path = bundle.get("path")
    if not isinstance(path, str) or not path:
        errors.append("outputs.bundle.path must be a non-empty string")

    if not isinstance(bundle.get("requested"), bool):
        errors.append("outputs.bundle.requested must be boolean")
    if not isinstance(bundle.get("available"), bool):
        errors.append("outputs.bundle.available must be boolean")
    reason = bundle.get("reason")
    if not isinstance(reason, str) or not reason:
        errors.append("outputs.bundle.reason must be a non-empty string")

    if mode == "zip" and path != "render_bundle.zip":
        errors.append("when mode=zip, path should be 'render_bundle.zip'")
    if mode == "manifest" and path != "bundle_manifest.json":
        errors.append("when mode=manifest, path should be 'bundle_manifest.json'")

    if mode in {"zip", "manifest"} and bundle.get("available"):
        target = base_dir / str(path)
        if not target.exists():
            errors.append(f"declared artifact missing: {target}")

    if mode == "none" and bundle.get("available"):
        errors.append("mode=none must have available=false")
    if mode in {"zip", "manifest"} and not bundle.get("requested"):
        errors.append(f"mode={mode} usually implies requested=true")

    return errors


def cmd_validate_summary(args: argparse.Namespace) -> int:
    summary = _load_json(args.input)
    errors = _validate_bundle_contract(summary, Path(args.input).parent or Path("."))
    if errors:
        print(json.dumps({"valid": False, "errors": errors}, ensure_ascii=False, indent=2))
        return 1
    print(
        json.dumps(
            {
                "valid": True,
                "bundle_mode": summary.get("outputs", {})
                .get("bundle", {})
                .get("mode"),
                "bundle_path": summary.get("outputs", {})
                .get("bundle", {})
                .get("path"),
                "reason": summary.get("outputs", {})
                .get("bundle", {})
                .get("reason"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _add_render_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--format",
        choices=["all", "md", "html", "json", "svg", "pdf"],
        default="all",
        help="output artifacts (all/md/html/json/svg/pdf), bundle formats all/md/html/json/svg/pdf auto-enable render_bundle.zip",
    )
    bundle_mode = parser.add_mutually_exclusive_group()
    bundle_mode.add_argument(
        "--bundle",
        dest="bundle",
        action="store_true",
        help="force rendering render_bundle.zip",
    )
    bundle_mode.add_argument(
        "--no-bundle",
        dest="bundle",
        action="store_false",
        help="skip rendering render_bundle.zip",
    )
    parser.set_defaults(bundle=None)
    parser.add_argument("--title", default="RRKAL Render Report", help="html page title")
    parser.add_argument("--pdf-title", help="custom title used for report.pdf metadata")
    parser.add_argument("--pdf-meta", help="custom metadata note written into report.pdf metadata")
    parser.add_argument(
        "--photo-style",
        dest="photo_style",
        action="store_true",
        default=True,
        help="use photo-like inspector layout and interactions (default: on)",
    )
    parser.add_argument(
        "--no-photo-style",
        dest="photo_style",
        action="store_false",
        help="use compact plain layout",
    )
    compact_mode = parser.add_mutually_exclusive_group()
    compact_mode.add_argument(
        "--compact",
        dest="compact_layout",
        action="store_true",
        default=False,
        help="start with compact layout density enabled",
    )
    compact_mode.add_argument(
        "--no-compact",
        dest="compact_layout",
        action="store_false",
        help="start with normal layout density",
    )
    parser.add_argument("--equity-compress", choices=["auto", "rdp", "lttb", "uniform", "none"], default="auto", help="equity curve compression strategy")
    parser.add_argument("--equity-max-points", type=int, default=DEFAULT_EQUITY_MAX_POINTS, help="max points for html/svg equity rendering")
    parser.add_argument("--equity-rdp-epsilon", type=float, default=0.002, help="RDP epsilon when equity-compress=rdp")
    parser.add_argument(
        "--html-row-cap",
        type=int,
        default=5000,
        help="max rows loaded into HTML inspectors when --trade-max-rows/--event-max-rows are unset (0 = unlimited)",
    )
    parser.add_argument(
        "--bundle-manifest-only",
        dest="bundle_manifest_only",
        action="store_true",
        help="generate bundle_manifest.json only (no render_bundle.zip) when bundle is requested",
    )
    parser.add_argument(
        "--trade-max-rows",
        type=int,
        default=DEFAULT_TRADE_MAX_ROWS,
        help="max trades kept in html table and csv (0 = unlimited)",
    )
    parser.add_argument(
        "--event-max-rows",
        type=int,
        default=DEFAULT_EVENT_MAX_ROWS,
        help="max events kept in html table and events csv (0 = unlimited)",
    )
    parser.add_argument("--emit-svg", action="store_true", help="emit compact equity_curve.svg in output directory (also enables bundle)")
    parser.add_argument("--export-csv", action="store_true", help="export trades/equity/events csv (also enables bundle)")
    parser.add_argument("--export-jsonl", action="store_true", help="export events jsonl (also enables bundle)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="RRKAL RenderKit",
        epilog=(
            "Bundle behavior: render_bundle.zip is generated for format all/md/html/json/svg/pdf or when "
            "--emit-svg/--export-csv/--export-jsonl are enabled. Use --bundle/--no-bundle to force or skip. "
            "Use --bundle-manifest-only for manifest-first mode."
        ),
    )
    parser.add_argument("--lenient", action="store_true", help="skip strict schema_version check")
    sub = parser.add_subparsers(dest="command")

    p_validate = sub.add_parser("validate", help="validate RRKAL artifact")
    p_validate.add_argument("input", help="artifact json path")
    p_validate.set_defaults(func=cmd_validate)

    p_validate_summary = sub.add_parser("validate-summary", help="validate render_summary.json contract for RRKAL integration")
    p_validate_summary.add_argument("input", help="render_summary.json path")
    p_validate_summary.set_defaults(func=cmd_validate_summary)

    p_render = sub.add_parser("render", help="render one artifact")
    p_render.add_argument("input", help="artifact json path / .jsonl / .zip")
    p_render.add_argument("--output-dir", default="", help="output directory")
    _add_render_options(p_render)
    p_render.set_defaults(func=cmd_render)

    p_batch = sub.add_parser("render-batch", help="render all artifact files in directory")
    p_batch.add_argument("input_dir", help="directory containing artifacts")
    p_batch.add_argument("--pattern", default="*.json", help="glob pattern, multiple split by comma")
    p_batch.add_argument("--output-root", default="rrkal_render_batch", help="output root directory")
    _add_render_options(p_batch)
    p_batch.set_defaults(func=cmd_render_batch)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if not getattr(args, "command", None):
        parser.print_help()
        return 1
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())

