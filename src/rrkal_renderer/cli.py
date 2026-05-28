from __future__ import annotations

import argparse
import csv
import html
import json
import math
import re
import zipfile
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Tuple


SUPPORTED_SCHEMA_VERSION = {"2.0.0"}
DEFAULT_EQUITY_MAX_POINTS = 5000
DEFAULT_TRADE_MAX_ROWS = 4000
DEFAULT_EVENT_MAX_ROWS = 2000


def _slugify(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "-", value.strip())
    safe = re.sub(r"-{2,}", "-", safe).strip("-")
    return safe[:120] if safe else "artifact"


def _load_json(path: str) -> Dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
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
    max_equity_points: int,
    equity_compress: str,
    rdp_epsilon: float,
    trade_max_rows: int,
    event_max_rows: int,
    photo_style: bool,
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

    top_trades = sorted(
        trades,
        key=lambda row: abs(_as_float(row.get("pnl", 0.0), 0.0)),
        reverse=True,
    )[: trade_max_rows]
    recent_events = sorted(
        events,
        key=lambda row: _as_text(row.get("timestamp", "")),
        reverse=True,
    )[:event_max_rows]

    md = _summary_markdown(payload, len(equity_points), len(sampled))
    symbols = sorted({trade.get("symbol", "") for trade in trades if trade.get("symbol")})
    event_names = sorted({event.get("event", "") for event in events if event.get("event")})

    safe_title = html.escape(title, quote=True)
    safe_run_id = html.escape(_resolve_run_id(payload), quote=True)
    body_class = "photo" if photo_style else "classic"

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
        "    .toolbar input,.toolbar select{background:#020617;color:var(--text);border:1px solid #334155;border-radius:8px;padding:.35rem .5rem}",
        "    .toolbar .mono{font-family:ui-monospace, SFMono-Regular, Menlo, Consolas, monospace}",
        "    .container{max-width:1320px;margin:0 auto}",
        "    .title{font-size:1.35rem;font-weight:700;letter-spacing:.02em;margin:.2rem 0 .5rem}",
        "    .subtitle{color:var(--text-dim);margin-bottom:1rem}",
        "    .panel{background:var(--panel);border:1px solid #334155;border-radius:12px;padding:.85rem;margin-bottom:1rem}",
        "    .panel h3{margin:0 0 .6rem;font-size:1rem}",
        "    .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:.6rem}",
        "    .snapshot{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:.6rem}",
        "    .kpi{background:#0f172ab3;border:1px solid #334155;border-radius:10px;padding:.55rem .65rem}",
        "    .kpi .name{color:var(--text-dim);font-size:.82rem}",
        "    .kpi .val{margin-top:.2rem;font-weight:700;word-break:break-all}",
        "    .layout{display:grid;grid-template-columns:1.1fr 1fr;gap:0.9rem}",
        "    .chart{width:100%;height:380px;border:1px solid #334155;border-radius:10px;padding:10px;background:#02061788;position:relative;overflow:hidden}",
        "    .table-wrap{overflow:auto;max-height:360px}",
        "    table{width:100%;border-collapse:collapse;font-size:.85rem}",
        "    th,td{padding:.4rem .35rem;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}",
        "    th{color:var(--text-dim);font-weight:600;position:sticky;top:0;background:#0f172ab3}",
        "    .up{color:var(--ok)}",
        "    .down{color:var(--bad)}",
        "    pre{background:#02061788;padding:.75rem;border-radius:10px;border:1px solid #334155;overflow:auto;white-space:pre-wrap;max-height:240px}",
        "    .chips{display:flex;flex-wrap:wrap;gap:.4rem;margin:.2rem 0}",
        "    .chip{padding:.18rem .55rem;border-radius:999px;border:1px solid #334155;color:var(--text-dim);font-size:.78rem;cursor:pointer}",
        "    .chip:hover{border-color:#93c5fd;color:#bfdbfe}",
        "    .small{font-size:.8rem;color:var(--text-dim)}",
        "    .note{font-size:.8rem;color:var(--text-dim);margin-top:.4rem}",
        "    .photo .layout{grid-template-columns:1.1fr 1fr}",
        "    .photo .panel{backdrop-filter:blur(2px)}",
        "    @media (max-width: 1024px){.layout{grid-template-columns:1fr;}}",
        "    @media (max-width: 640px){body{padding:0.8rem;}}",
        "  </style>",
        "</head>",
        f"<body class=\"{body_class}\">",
        "  <div class='container'>",
        f"    <div class='title'>{safe_title}</div>",
        f"    <div class='subtitle'>RRKAL 2.0.0 • photo-style pre-renderer • mode={body_class}</div>",
        "    <section class='panel'>",
        "      <h3>1) Run Snapshot</h3>",
        "      <div class='snapshot'>",
        f'        <div class="kpi"><div class="name">run_id</div><div class="val mono">{safe_run_id}</div></div>',
        f'        <div class="kpi"><div class="name">schema_version</div><div class="val">{payload.get("schema_version", "N/A")}</div></div>',
        f'        <div class="kpi"><div class="name">equity points</div><div class="val">{len(equity_points)}</div></div>',
        f'        <div class="kpi"><div class="name">render points</div><div class="val">{len(sampled)}</div></div>',
        f'        <div class="kpi"><div class="name">symbols</div><div class="val">{len(symbols)}</div></div>',
        f'        <div class="kpi"><div class="name">events</div><div class="val">{len(events)}</div></div>',
        "      </div>",
        "      <p class='note'>Large dataset tip: use keyword filters or sorting to inspect quickly.</p>",
        "    </section>",
        "    <section class='panel'>",
        "      <h3>2) Report summary</h3>",
        f"      <pre>{md}</pre>",
        "    </section>",
        "    <div class='layout'>",
        "      <section class='panel'>",
        "        <h3>3) Equity Curve</h3>",
        "        <div class='chart'>",
        "          <svg viewBox='0 0 1080 360' preserveAspectRatio='none' style='width:100%;height:100%'>",
        f"            <path d='{path_d}' fill='none' stroke='#38bdf8' stroke-width='2'/>",
        "          </svg>",
        "        </div>",
        "      </section>",
        "      <section class='panel'>",
        "        <h3>4) Event Inspector</h3>",
        "        <div class='toolbar'>",
        "          <input id='eventSymbol' placeholder='symbol contains'>",
        f"          <select id='eventName'><option value=''>All Events</option>{''.join([f'<option value=\"{n}\">{n}</option>' for n in event_names])}</select>",
        "          <select id='eventOrder'><option value='desc'>Newest first</option><option value='asc'>Oldest first</option></select>",
        "        </div>",
        f"        <div class='chips' id='eventChips'>{''.join([f'<span class=\"chip\" data-event=\"{name}\">{name}</span>' for name in event_names[:12]])}</div>",
        "        <div class='table-wrap'><table><thead><tr><th>event</th><th>symbol</th><th>timestamp</th><th>details</th></tr></thead><tbody id='eventBody'></tbody></table></div>",
        "      </section>",
        "    </div>",
        "    <section class='panel'>",
        "      <h3>5) Top Trades</h3>",
        "      <div class='toolbar'>",
        "        <input id='tradeSymbol' placeholder='symbol contains'>",
        "        <select id='tradeSort'><option value='pnl_desc'>PnL abs desc</option><option value='pnl_asc'>PnL abs asc</option><option value='qty_desc'>Quantity desc</option></select>",
        "        <input id='tradePnl' type='number' placeholder='Min abs(PnL)' step='0.01'>",
        "      </div>",
        "      <div class='table-wrap'><table><thead><tr><th>symbol</th><th>direction</th><th>quantity</th><th>entry</th><th>exit</th><th>pnl</th><th>entry_cost</th><th>exit_cost</th><th>start_ts</th><th>end_ts</th></tr></thead><tbody id='tradeBody'></tbody></table></div>",
        "    </section>",
        "  </div>",
        "  <script>",
        f"    const TRADE_DATA = {json.dumps(top_trades, ensure_ascii=False)};",
        f"    const EVENT_DATA = {json.dumps(recent_events, ensure_ascii=False)};",
        "    const eventBody = document.querySelector('#eventBody');",
        "    const tradeBody = document.querySelector('#tradeBody');",
        "    const eventSymbol = document.querySelector('#eventSymbol');",
        "    const eventName = document.querySelector('#eventName');",
        "    const eventOrder = document.querySelector('#eventOrder');",
        "    const tradeSymbol = document.querySelector('#tradeSymbol');",
        "    const tradeSort = document.querySelector('#tradeSort');",
        "    const tradePnl = document.querySelector('#tradePnl');",
        "    const eventChips = document.querySelectorAll('#eventChips .chip');",
        "    function num(v){ const n = Number(v); return Number.isFinite(n) ? n.toFixed(4) : String(v || ''); }",
        "    function safeText(v){ return String(v||'').replace(/[&<>\"']/g,(s)=>({\"&\":\"&amp;\",\"<\":\"&lt;\",\">\":\"&gt;\",\"\\\"\":\"&quot;\",\"'\":\"&#39;\"}[s])); }",
        "    function renderEvents(){",
        "      const sym = (eventSymbol.value || '').toLowerCase().trim();",
        "      const name = eventName.value;",
        "      const asc = eventOrder.value === 'asc';",
        "      let list = EVENT_DATA.filter((row)=>{",
        "        if (sym && !String(row.symbol || '').toLowerCase().includes(sym)) return false;",
        "        if (name && row.event !== name) return false;",
        "        return true;",
        "      });",
        "      list.sort((a,b)=>{ const aTs=String(a.timestamp||''); const bTs=String(b.timestamp||''); return asc ? (aTs > bTs ? 1 : -1) : (aTs < bTs ? 1 : -1); });",
        "      eventBody.innerHTML = list.map((row)=>`<tr><td>${safeText(row.event||'')}</td><td>${safeText(row.symbol||'')}</td><td>${safeText(row.timestamp||'')}</td><td><pre>${safeText(JSON.stringify(row.details||{}))}</pre></td></tr>`).join('');",
        "    }",
        "    function renderTrades(){",
        "      const sym = (tradeSymbol.value || '').toLowerCase().trim();",
        "      const p = Number(tradePnl.value);",
        "      let list = TRADE_DATA.slice();",
        "      if (sym) list = list.filter((row)=>String(row.symbol || '').toLowerCase().includes(sym));",
        "      if (Number.isFinite(p)) list = list.filter((row)=>Math.abs(Number(row.pnl || 0)) >= Math.abs(p));",
        "      if (tradeSort.value === 'pnl_desc') list.sort((a,b)=>Math.abs(Number(b.pnl||0))-Math.abs(Number(a.pnl||0)));",
        "      if (tradeSort.value === 'pnl_asc') list.sort((a,b)=>Math.abs(Number(a.pnl||0))-Math.abs(Number(b.pnl||0)));",
        "      if (tradeSort.value === 'qty_desc') list.sort((a,b)=>Math.abs(Number(b.quantity||0))-Math.abs(Number(a.quantity||0)));",
        "      tradeBody.innerHTML = list.map((row)=>{ const cls = Number(row.pnl||0)>=0 ? 'up' : 'down'; return `<tr><td>${safeText(row.symbol||'')}</td><td>${safeText(row.direction||'')}</td><td>${num(row.quantity||0)}</td><td>${num(row.entry||0)}</td><td>${num(row.exit||0)}</td><td class='${cls}'>${num(row.pnl||0)}</td><td>${num(row.entry_cost||0)}</td><td>${num(row.exit_cost||0)}</td><td>${safeText(row.start_ts||'')}</td><td>${safeText(row.end_ts||'')}</td></tr>`; }).join('');",
        "    }",
        "    function bind(){",
        "      [eventSymbol, eventName, eventOrder, tradeSymbol, tradeSort, tradePnl].forEach((node)=>{",
        "        if (!node) return;",
        "        node.addEventListener('input', ()=>{renderEvents(); renderTrades();});",
        "        node.addEventListener('change', ()=>{renderEvents(); renderTrades();});",
        "      });",
        "      eventChips.forEach((chip)=>chip.addEventListener('click', ()=>{ const v = chip.getAttribute('data-event') || ''; if (eventName){ eventName.value = eventName.value === v ? '' : v; renderEvents(); }}));",
        "    }",
        "    bind();",
        "    renderEvents();",
        "    renderTrades();",
        "  </script>",
        "</body>",
        "</html>",
    ]

    return "\\n".join(html_lines)
\ndef _write_svg(path: Path, points: List[Tuple[float, float, str]], width: int = 1080, height: int = 360) -> None:
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

    if args.format in ("all", "md"):
        _write_text(out_dir / "report.md", _summary_markdown(payload, len(equity_points), len(sampled_equity)))

    if args.format in ("all", "html"):
        _write_text(
            out_dir / "report.html",
            _to_html(
                payload=payload,
                title=args.title,
                max_equity_points=args.equity_max_points,
                equity_compress=args.equity_compress,
                rdp_epsilon=args.equity_rdp_epsilon,
                trade_max_rows=args.trade_max_rows,
                event_max_rows=args.event_max_rows,
                photo_style=args.photo_style,
            ),
        )

    if args.emit_svg or args.format in ("all", "svg"):
        _write_svg(out_dir / "equity_curve.svg", sampled_equity)

    if args.export_csv:
        _emit_csv(out_dir / "trades.csv", trades[: args.trade_max_rows])
        _emit_csv(
            out_dir / "equity_curve.csv",
            [dict(timestamp=entry[2], timestamp_index=entry[0], equity=entry[1]) for entry in equity_points],
        )
        _emit_csv(out_dir / "events.csv", events[: args.event_max_rows])

    if args.export_jsonl:
        _emit_jsonl(out_dir / "events.jsonl", events)

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


def _add_render_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--format", choices=["all", "md", "html", "json", "svg"], default="all", help="output artifacts")
    parser.add_argument("--title", default="RRKAL Render Report", help="html page title")
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
    parser.add_argument("--equity-compress", choices=["auto", "rdp", "lttb", "uniform", "none"], default="auto", help="equity curve compression strategy")
    parser.add_argument("--equity-max-points", type=int, default=DEFAULT_EQUITY_MAX_POINTS, help="max points for html/svg equity rendering")
    parser.add_argument("--equity-rdp-epsilon", type=float, default=0.002, help="RDP epsilon when equity-compress=rdp")
    parser.add_argument("--trade-max-rows", type=int, default=DEFAULT_TRADE_MAX_ROWS, help="max trades kept in html table and csv")
    parser.add_argument("--event-max-rows", type=int, default=DEFAULT_EVENT_MAX_ROWS, help="max events kept in html table and events csv")
    parser.add_argument("--emit-svg", action="store_true", help="emit compact equity_curve.svg in output directory")
    parser.add_argument("--export-csv", action="store_true", help="export trades/equity/events csv")
    parser.add_argument("--export-jsonl", action="store_true", help="export events jsonl")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RRKAL RenderKit")
    parser.add_argument("--lenient", action="store_true", help="skip strict schema_version check")
    sub = parser.add_subparsers(dest="command")

    p_validate = sub.add_parser("validate", help="validate RRKAL artifact")
    p_validate.add_argument("input", help="artifact json path")
    p_validate.set_defaults(func=cmd_validate)

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

