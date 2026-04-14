"""Phase 3 calibration dashboard.

Pulls execution_records from the window, computes:
  - calibration statistic: % of live fills within paper [p05, p95]
  - "vanished" rate: ratio of opps detected but not filled
  - divergence distribution
  - latency percentiles
  - unexplained count

Doc target: calibration >= 85%. Below: model's uncertainty was too tight.

Usage:
    python scripts/calibration_report.py --config config.yaml --window-days 14
"""
from __future__ import annotations

import argparse
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_config  # noqa: E402
from src.storage import state_db  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--window-days", type=int, default=14)
    p.add_argument("--output", default=None)
    args = p.parse_args()

    cfg = load_config(args.config)
    conn = state_db.connect(cfg.storage.state_db_path)
    state_db.init_schema(conn)

    since = datetime.now(timezone.utc) - timedelta(days=args.window_days)
    since_iso = since.isoformat()
    records = state_db.execution_records_since(conn, since_iso)

    total = len(records)
    resolved = [r for r in records if r["within_p5_p95"] is not None]
    resolved_n = len(resolved)
    within_n = sum(1 for r in resolved if r["within_p5_p95"] == 1)
    coverage = (within_n / resolved_n) if resolved_n else None

    latencies = [r["live_fill_latency_ms"] for r in records if r["live_fill_latency_ms"] is not None]
    slippages = [r["live_slippage_bps"] for r in records if r["live_slippage_bps"] is not None]
    partial_fills = sum(1 for r in records if r["live_partial_fill"] == 1)
    unexplained = sum(
        1
        for r in resolved
        if r["within_p5_p95"] == 0 and not (r["explanation"] or "").strip()
    )

    def _p(values, q):
        if not values:
            return 0
        srt = sorted(values)
        idx = min(len(srt) - 1, max(0, int(q * (len(srt) - 1))))
        return srt[idx]

    out = []
    out.append(f"# Calibration Report — last {args.window_days} days")
    out.append("")
    out.append(f"Total execution records: {total}")
    out.append(f"Resolved (live PnL known): {resolved_n}")
    if coverage is None:
        out.append("")
        out.append("**No resolved records yet.** Run at Gate 1 or later and wait for resolution.")
    else:
        out.append("")
        out.append(f"## Calibration statistic: **{within_n}/{resolved_n} = {coverage:.1%}**")
        target = 0.85
        if coverage >= target:
            out.append(f"✅ At or above target ({target:.0%}). Model is well-calibrated.")
        elif coverage >= 0.75:
            out.append(f"⚠ Below target ({target:.0%}) but above 75%. MARGINAL. "
                       f"Run another 2 weeks at current gate, investigate unexplained rows.")
        else:
            out.append("🛑 Below 75%. REDESIGN: model uncertainty is materially wrong.")

    out.append("")
    out.append("## Fill latency")
    out.append(f"- p50: {_p(latencies, 0.5)} ms")
    out.append(f"- p95: {_p(latencies, 0.95)} ms")
    out.append(f"- p99: {_p(latencies, 0.99)} ms")
    out.append("")
    out.append("## Slippage (live - paper, bps)")
    if slippages:
        out.append(f"- mean: {statistics.mean(slippages):.1f}")
        out.append(f"- p95: {_p(slippages, 0.95)}")
        out.append(f"- max: {max(slippages)}")
    else:
        out.append("No slippage data yet.")

    out.append("")
    out.append(f"Partial fills: {partial_fills}")
    out.append(f"Unexplained divergences: {unexplained}")
    if unexplained > 0:
        out.append("⚠ Investigate these before accumulating more data. "
                   "Unexplained is always worse than expected.")

    text = "\n".join(out) + "\n"
    if args.output:
        Path(args.output).write_text(text)
        print(f"wrote {args.output}")
    else:
        print(text)


if __name__ == "__main__":
    main()
