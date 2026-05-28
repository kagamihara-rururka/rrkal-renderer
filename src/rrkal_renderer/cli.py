from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List


SUPPORTED_SCHEMA_VERSION = {"2.0.0"}


def _load_json(path: str) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


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


def _resolve_evidence(payload: Dict[str, Any]) -> Dict[str, Any]:
    evidence = payload.get("evidence")
    if isinstance(evidence, dict):
        return evidence
    return payload.get("artifacts", {}) if isinstance(payload.get("artifacts"), dict) else {}


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
    event_counter: Counter[str] = Counter(item.get("event", "") for item in events if isinstance(item, dict) and item.get("event"))

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


def _summary_markdown(payload: Dict[str, Any]) -> str:
    stats = _collect_stats(payload)
    run_meta = stats["run_meta"]
    summary_run = stats["summary_run"]

    lines = [
        "# RRKAL 預渲染報告",
        "",
        "## 1) 作業摘要",
        f"schema_version: `{payload.get('schema_version', 'N/A')}`",
        f"run_id: `{_resolve_run_id(payload)}`",
        f"platform_id: `{run_meta.get('platform_id', 'N/A')}`",
        f"market_id: `{run_meta.get('market_id', 'N/A')}`",
        f"provider_id: `{run_meta.get('provider_id', 'N/A')}`",
        f"strategy_id: `{run_meta.get('strategy_id', 'N/A')}`",
        "",
        "## 2) 績效摘要",
        "| 指標 | 值 |",
        "| --- | ---: |",
        f"| initial_cash | {_as_text(summary_run.get('initial_cash', 'N/A'))} |",
        f"| final_cash | {_as_text(summary_run.get('final_cash', 'N/A'))} |",
        f"| total_pnl | {_as_text(summary_run.get('total_pnl', 'N/A'))} |",
        f"| total_trades | {stats['trade_count']} |",
        f"| win_rate | {stats['win_rate']}% |",
        f"| gross_pnl | {stats['gross_pnl']} |",
        f"| avg_pnl | {stats['avg_pnl']} |",
        f"| max_drawdown_seen | {_as_text(stats['max_drawdown_seen'])} |",
        "",
        "## 3) 事件彙總",
    ]

    if stats["event_counter"]:
        lines.extend(["| event | count |", "| --- | ---: |"])
        for name, count in stats["event_counter"].most_common():
            lines.append(f"| {name} | {count} |")
    else:
        lines.append("(No event records)")

    return "\n".join(lines)


def _to_html(payload: Dict[str, Any], title: str) -> str:
    evidence = _resolve_evidence(payload)
    md = _summary_markdown(payload)
    trades = evidence.get("trades", []) if isinstance(evidence.get("trades"), list) else []
    equity = evidence.get("equity_curve", []) if isinstance(evidence.get("equity_curve"), list) else []

    equity_points = [
        {"x": row.get("timestamp"), "y": _as_float(row.get("equity", 0.0), 0.0)}
        for row in equity
        if row.get("timestamp") is not None and row.get("equity") is not None
    ]

    trades_rows = [
        {
            "symbol": _as_text(t.get("symbol", "")),
            "direction": _as_text(t.get("direction", "")),
            "quantity": _as_float(t.get("quantity", 0), 0),
            "entry": _as_float(t.get("entry", 0), 0.0),
            "exit": _as_float(t.get("exit", 0), 0.0),
            "pnl": _as_float(t.get("pnl", 0), 0.0),
            "start_ts": _as_text(t.get("start_ts", "")),
            "end_ts": _as_text(t.get("end_ts", "")),
        }
        for t in trades
        if isinstance(t, dict)
    ]

    lines = [
        "<!doctype html>",
        "<html>",
        "<head>",
        '  <meta charset="utf-8" />',
        f"  <title>{title}</title>",
        "  <style>",
        "    body {",
        "      font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, sans-serif;",
        "      margin: 2rem;",
        "      line-height: 1.5;",
        "      color: #1e1e1e;",
        "      background: #f6f7fb;",
        "    }",
        "    .card {",
        "      background: white;",
        "      border-radius: 10px;",
        "      border: 1px solid #dfe3ee;",
        "      padding: 1rem;",
        "      margin: 1rem 0;",
        "      box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);",
        "    }",
        "    pre {",
        "      white-space: pre-wrap;",
        "      background: #0f172a;",
        "      color: #f8fafc;",
        "      border-radius: 10px;",
        "      padding: 1rem;",
        "      overflow-x: auto;",
        "    }",
        "    table {",
        "      width: 100%;",
        "      border-collapse: collapse;",
        "    }",
        "    th, td {",
        "      border-bottom: 1px solid #e5e7eb;",
        "      padding: 0.4rem;",
        "      text-align: right;",
        "    }",
        "    th { background: #f1f5f9; text-align: left; }",
        "    .positive { color: #16a34a; }",
        "    .negative { color: #dc2626; }",
        "    svg { width: 100%; max-width: 100%; height: 320px; display: block; }",
        "  </style>",
        "</head>",
        "<body>",
        f"  <h1>{title}</h1>",
        "  <section class=\"card\">",
        "    <h2>Summary</h2>",
        f"    <pre>{md}</pre>",
        "  </section>",
        "  <section class=\"card\">",
        "    <h2>Equity Curve</h2>",
        "    <svg id=\"equity_chart\" viewBox=\"0 0 960 280\" preserveAspectRatio=\"none\"></svg>",
        "  </section>",
        "  <section class=\"card\">",
        "    <h2>Top Trades</h2>",
        "    <table id=\"trade_table\">",
        "      <thead><tr><th>symbol</th><th>direction</th><th>qty</th><th>entry</th><th>exit</th><th>pnl</th><th>start_ts</th><th>end_ts</th></tr></thead>",
        "      <tbody></tbody>",
        "    </table>",
        "  </section>",
        "  <script>",
        "    const equity = " + json.dumps(equity_points, ensure_ascii=False) + ";",
        "    const trades = " + json.dumps(trades_rows, ensure_ascii=False) + ";",
        "",
        "    function drawEquity() {",
        "      const svg = document.getElementById('equity_chart');",
        "      if (!svg || equity.length === 0) {",
        "        return;",
        "      }",
        "      const ys = equity.map((d) => Number(d.y));",
        "      const minY = Math.min(...ys);",
        "      const maxY = Math.max(...ys);",
        "      const xScale = (i) => 24 + i * (912 / Math.max(1, (equity.length - 1)));",
        "      const yScale = (v) => 250 - ((v - minY) / (maxY - minY || 1)) * 200;",
        "      let path = '';",
        "      equity.forEach((point, i) => {",
        "        const x = xScale(i);",
        "        const y = yScale(Number(point.y));",
        "        path += (i === 0 ? 'M' : 'L') + x.toFixed(2) + ' ' + y.toFixed(2) + ' ';",
        "      });",
        "      const line = document.createElementNS('http://www.w3.org/2000/svg', 'path');",
        "      line.setAttribute('d', path);",
        "      line.setAttribute('fill', 'none');",
        "      line.setAttribute('stroke', '#2563eb');",
        "      line.setAttribute('stroke-width', '2');",
        "      svg.appendChild(line);",
        "    }",
        "",
        "    function renderTrades() {",
        "      const body = document.querySelector('#trade_table tbody');",
        "      if (!body) return;",
        "      trades.forEach((t) => {",
        "        const tr = document.createElement('tr');",
        "        tr.innerHTML = `",
        "          <td>${t.symbol}</td><td>${t.direction}</td><td>${t.quantity}</td>",
        "          <td>${t.entry}</td><td>${t.exit}</td>",
        "          <td class=\"${Number(t.pnl) >= 0 ? 'positive' : 'negative'}\">${t.pnl}</td>",
        "          <td>${t.start_ts}</td><td>${t.end_ts}</td>",
        "        `;",
        "        body.appendChild(tr);",
        "      });",
        "    }",
        "",
        "    drawEquity();",
        "    renderTrades();",
        "  </script>",
        "</body>",
        "</html>",
    ]

    return "\n".join(lines)


def _render_one(args: argparse.Namespace) -> Path:
    payload = _load_json(args.input)
    _validate(payload, strict=not args.lenient)
    evidence = _resolve_evidence(payload)

    run_id = _resolve_run_id(payload)
    if args.output_dir:
        out_dir = Path(args.output_dir)
    else:
        ts = datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
        out_dir = Path(f"rrkal_render_{ts}_{run_id[:8] if len(run_id) >= 8 else run_id}")

    out_dir.mkdir(parents=True, exist_ok=True)

    _write_text(out_dir / "report.md", _summary_markdown(payload))

    if args.format in ("all", "html", "md"):
        _write_text(out_dir / "report.html", _to_html(payload, title=args.title))

    if args.export_csv:
        _emit_csv(str(out_dir / "trades.csv"), evidence.get("trades", []) if isinstance(evidence.get("trades"), list) else [])
        _emit_csv(str(out_dir / "equity_curve.csv"), evidence.get("equity_curve", []) if isinstance(evidence.get("equity_curve"), list) else [])
        _emit_csv(str(out_dir / "events.csv"), evidence.get("events", []) if isinstance(evidence.get("events"), list) else [])

    if args.export_jsonl:
        _emit_jsonl(str(out_dir / "events.jsonl"), evidence.get("events", []) if isinstance(evidence.get("events"), list) else [])

    if args.format == "json":
        _write_text(out_dir / "preflight.json", json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))

    return out_dir


def cmd_render(args: argparse.Namespace) -> int:
    out_dir = _render_one(args)
    print(f"Rendered report: {out_dir}")
    return 0


def cmd_render_batch(args: argparse.Namespace) -> int:
    input_dir = Path(args.input_dir)
    if not input_dir.exists():
        raise SystemExit(f"input_dir not found: {input_dir}")

    patterns = [p.strip() for p in args.pattern.split(",")]
    files = []
    for pattern in patterns:
        files.extend(input_dir.glob(pattern))

    files = sorted({f for f in files if f.suffix.lower() == ".json" and f.is_file()})
    if not files:
        raise SystemExit(f"no json files found in {input_dir} with pattern: {args.pattern}")

    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    index: List[str] = []

    for file in files:
        local_out = output_root / file.stem
        args.input = str(file)
        args.output_dir = str(local_out)
        out = _render_one(args)
        rel = out.name
        index.append(f"<li><a href='{rel}/report.html'>{_as_text(file.name)}</a> ({rel})</li>")

    index_html = "<h1>RRKAL Render Batch</h1><ul>" + "".join(index) + "</ul>"
    _write_text(output_root / "index.html", index_html)
    print(f"Batch rendered: {len(index)} files -> {output_root}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    payload = _load_json(args.input)
    _validate(payload, strict=not args.lenient)
    print(f"RRKAL artifact valid: schema_version={payload.get('schema_version')}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RRKAL RenderKit")
    parser.add_argument("--lenient", action="store_true", help="skip strict schema_version check")
    sub = parser.add_subparsers(dest="command")

    p_validate = sub.add_parser("validate", help="validate RRKAL artifact")
    p_validate.add_argument("input", help="artifact json path")
    p_validate.set_defaults(func=cmd_validate)

    p_render = sub.add_parser("render", help="render one artifact")
    p_render.add_argument("input", help="artifact json path")
    p_render.add_argument("--output-dir", default="", help="output directory")
    p_render.add_argument("--format", choices=["all", "md", "html", "json"], default="all", help="output artifacts")
    p_render.add_argument("--title", default="RRKAL Render Report", help="html page title")
    p_render.add_argument("--export-csv", action="store_true", help="export trades/equity/events csv")
    p_render.add_argument("--export-jsonl", action="store_true", help="export events jsonl")
    p_render.set_defaults(func=cmd_render)

    p_batch = sub.add_parser("render-batch", help="render all artifact files in directory")
    p_batch.add_argument("input_dir", help="directory containing artifact jsons")
    p_batch.add_argument("--pattern", default="*.json", help="glob pattern, multiple split by comma")
    p_batch.add_argument("--output-root", default="rrkal_render_batch", help="output root directory")
    p_batch.add_argument("--format", choices=["all", "md", "html", "json"], default="all")
    p_batch.add_argument("--title", default="RRKAL Render Report", help="html page title prefix")
    p_batch.add_argument("--export-csv", action="store_true")
    p_batch.add_argument("--export-jsonl", action="store_true")
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
