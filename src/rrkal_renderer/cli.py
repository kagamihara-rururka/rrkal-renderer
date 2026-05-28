from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Dict, List


def _load_json(path: str) -> Dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _emit_csv(path: str, rows: List[Dict]) -> None:
    if not rows:
        Path(path).write_text("", encoding="utf-8")
        return
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = sorted({k for row in rows for k in row.keys()})
    with output.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})


def _summary_markdown(artifact: Dict) -> str:
    evidence = artifact.get("evidence", artifact.get("artifacts", {})) or {}
    summary_run = evidence.get("summary", {}).get("run", {})
    run_meta = evidence.get("run_metadata", {})
    lines = [
        "# RRKAL Render Report",
        "",
        "## Meta",
        f"platform_id: `{run_meta.get('platform_id', 'N/A')}`",
        f"market_id: `{run_meta.get("market_id", 'N/A')}`",
        f"provider_id: `{run_meta.get('provider_id', 'N/A')}`",
        "",
        "## Summary",
        f"initial_cash: {summary_run.get('initial_cash', 'N/A')}",
        f"final_cash: {summary_run.get('final_cash', 'N/A')}",
        f"total_pnl: {summary_run.get('total_pnl', 'N/A')}",
        f"total_trades: {summary_run.get('total_trades', 'N/A')}",
        f"max_drawdown_seen: {summary_run.get('max_drawdown_seen', 'N/A')}",
    ]

    event_counter: Counter[str] = Counter(item.get("event", "") for item in evidence.get("events", []) if item.get("event"))
    if event_counter:
        lines.extend(["", "## Events", "| event | count |", "|---|---:|"])
        lines.extend([f"| {name} | {count} |" for name, count in event_counter.most_common()])
    return "\n".join(lines)


def _to_html(artifact: Dict) -> str:
    md = _summary_markdown(artifact)
    return f"""<!doctype html>
<html><head><meta charset=\"utf-8\"/><title>RRKAL Render Report</title>
<style>
body {{ font-family: -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; margin: 2rem; line-height: 1.5; }}
pre {{ background: #f7f7f9; padding: 1rem; border-radius: 8px; }}
</style></head><body><pre>{md}</pre></body></html>
"""


def cmd_render(args: argparse.Namespace) -> int:
    artifact = _load_json(args.input)
    ts = datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
    output_dir = Path(args.output_dir or f"rendered_{ts}")
    output_dir.mkdir(parents=True, exist_ok=True)

    md_path = output_dir / "report.md"
    html_path = output_dir / "report.html"
    md_content = _summary_markdown(artifact)
    md_path.write_text(md_content, encoding="utf-8")
    html_path.write_text(_to_html(artifact), encoding="utf-8")

    evidence = artifact.get("evidence", artifact.get("artifacts", {})) or {}
    if args.export_csv:
        _emit_csv(str(output_dir / "trades.csv"), evidence.get("trades", []))
        _emit_csv(str(output_dir / "equity_curve.csv"), evidence.get("equity_curve", []))
        _emit_csv(str(output_dir / "events.csv"), evidence.get("events", []))
    print(f"Rendered report: {output_dir}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    artifact = _load_json(args.input)
    if not isinstance(artifact, dict):
        raise SystemExit("invalid artifact format")
    for key in ["schema_version", "intent", "plan", "evidence"]:
        if key not in artifact:
            raise SystemExit(f"missing key: {key}")
    print(f"RRKAL artifact valid (schema_version={artifact.get('schema_version')})")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RRKAL RenderKit")
    sub = parser.add_subparsers(dest="command")

    p_validate = sub.add_parser("validate", help="validate RRKAL artifact")
    p_validate.add_argument("input", help="artifact json path")
    p_validate.set_defaults(func=cmd_validate)

    p_render = sub.add_parser("render", help="render markdown/html report")
    p_render.add_argument("input", help="artifact json path")
    p_render.add_argument("--output-dir", default="", help="output directory")
    p_render.add_argument("--export-csv", action="store_true", help="export trades/equity/events csv")
    p_render.set_defaults(func=cmd_render)

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
