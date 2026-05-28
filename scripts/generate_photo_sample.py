"""Generate a deterministic photo-style RRKAL sample artifact for quick UI checks."""

from __future__ import annotations

import argparse
import json
import math
import random
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List


def _to_float(value: float) -> float:
    return float(value)


def _build_sample(
    trade_count: int,
    event_count: int,
    equity_count: int,
    start_cash: float,
) -> Dict[str, Any]:
    rng = random.Random(42)
    start_time = datetime(2025, 1, 1, 9, 30)

    trades: List[Dict[str, Any]] = []
    run_equity: List[float] = [start_cash]

    symbols = ["AAPL", "MSFT", "NVDA", "TSLA", "ETHUSDT"]
    directions = ["long", "short"]

    for i in range(trade_count):
        symbol = symbols[i % len(symbols)]
        direction = directions[i % len(directions)]
        quantity = round(rng.uniform(0.1, 8.0), 3)
        entry = round(rng.uniform(120, 360), 4)
        delta = round(rng.uniform(-35, 45), 4)
        exit = round(entry + delta, 4)
        pnl = round((exit - entry) * quantity * (1 if direction == "long" else -1), 4)
        entry_cost = round(quantity * entry * 0.001, 4)
        exit_cost = round(quantity * exit * 0.001, 4)
        start_ts = (start_time + timedelta(minutes=i * 7)).strftime("%Y-%m-%dT%H:%M:%SZ")
        end_ts = (start_time + timedelta(minutes=i * 7 + 3)).strftime("%Y-%m-%dT%H:%M:%SZ")

        run_equity.append(run_equity[-1] + pnl - entry_cost - exit_cost)

        trades.append(
            {
                "symbol": symbol,
                "direction": direction,
                "quantity": _to_float(quantity),
                "entry": _to_float(entry),
                "exit": _to_float(exit),
                "pnl": _to_float(pnl),
                "entry_cost": _to_float(entry_cost),
                "exit_cost": _to_float(exit_cost),
                "start_ts": start_ts,
                "end_ts": end_ts,
            }
        )

    events: List[Dict[str, Any]] = []
    for i in range(event_count):
        ts = (start_time + timedelta(minutes=i * 4)).strftime("%Y-%m-%dT%H:%M:%SZ")
        symbol = symbols[i % len(symbols)]
        kind = "order_fill" if i % 2 == 0 else "risk_check"
        events.append(
            {
                "event_type": kind,
                "symbol": symbol,
                "timestamp": ts,
                "details": {
                    "step": i,
                    "note": f"simulated {kind} event",
                    "price": round(100 + 0.85 * i + rng.uniform(-1, 1), 4),
                },
            }
        )

    final_cash = run_equity[-1]
    total_pnl = final_cash - start_cash
    gross_pnl = sum(t["pnl"] for t in trades)
    equity_curve = []
    for i in range(equity_count):
        value = round(start_cash + (gross_pnl / equity_count) * i + math.sin(i / 12.0) * 8, 4)
        ts = (start_time + timedelta(minutes=i)).strftime("%Y-%m-%dT%H:%M:%SZ")
        equity_curve.append({"timestamp": ts, "equity": value, "balance": value})

    return {
        "schema_version": "2.0.0",
        "intent": {"name": "photo-sample", "version": "1"},
        "plan": {"name": "photo-preview", "version": "1"},
        "evidence": {
            "run_metadata": {
                "run_id": "photo-preview-202601",
                "platform_id": "mock",
                "market_id": "demo",
                "provider_id": "sample",
                "strategy_id": "photo-inspector",
                "created_at": start_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
            "summary": {
                "run": {
                    "initial_cash": start_cash,
                    "final_cash": final_cash,
                    "total_pnl": total_pnl,
                    "max_drawdown_seen": -14.3,
                }
            },
            "trades": trades,
            "events": events,
            "equity_curve": equity_curve,
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create a sample RRKAL artifact for photo-style preview")
    parser.add_argument("--output", default="artifacts/photo_sample.json", help="artifact output path")
    parser.add_argument("--trade-count", type=int, default=96, help="number of sample trades")
    parser.add_argument("--event-count", type=int, default=120, help="number of sample events")
    parser.add_argument("--equity-count", type=int, default=700, help="equity curve point count")
    parser.add_argument("--start-cash", type=float, default=20000.0, help="initial cash")
    parser.add_argument("--render", action="store_true", help="render html sample immediately after artifact creation")
    parser.add_argument("--output-dir", default="outputs/photo_sample", help="html output dir when --render used")
    parser.add_argument(
        "--photo-preset",
        choices=["photo", "photo-compact", "classic"],
        default="photo-compact",
        help="initial ui preset for rendered html",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="overwrite artifact even if path exists",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    out_path = Path(args.output)
    if out_path.exists() and not args.overwrite:
        print(f"artifact exists: {out_path} (use --overwrite)")
        return 1

    out_path.parent.mkdir(parents=True, exist_ok=True)
    sample = _build_sample(
        trade_count=args.trade_count,
        event_count=args.event_count,
        equity_count=args.equity_count,
        start_cash=args.start_cash,
    )
    out_path.write_text(json.dumps(sample, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"sample artifact written: {out_path}")

    if args.render:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "rrkal_renderer.cli",
                "render",
                str(out_path),
                "--format",
                "html",
                "--photo-preset",
                args.photo_preset,
                "--output-dir",
                args.output_dir,
            ],
            check=False,
        )
        print(f"sample rendered html: {args.output_dir}/report.html")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
