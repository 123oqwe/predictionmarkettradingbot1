"""Capacity constraint diagnostic.

The doc requires you to diagnose BEFORE expanding:
  - Capital constraint → add edge (new strategy/platform)
  - Edge constraint → increase per-trade size OR find higher-capacity platforms
  - Latency constraint → infra work, not new platforms
  - Attention constraint → automation, not new strategies

We classify by reading the last 30 days of DB stats:
  - unused_capital_fraction = avg(free_capital_hours / total_capital_hours)
  - opportunities_rejected_due_to_capital / opportunities_total
  - paper_vs_live_vanish_rate (live side populated from execution_records)
  - manual_intervention_count (from alerts or review queue log)

Not perfect but gets the rough answer. The doc says even a rough diagnosis
beats "just add more stuff".

Usage:
    python scripts/capacity_report.py --config config.yaml --window-days 30
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_config  # noqa: E402
from src.storage import state_db  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--window-days", type=int, default=30)
    p.add_argument("--output", default=None)
    args = p.parse_args()

    cfg = load_config(args.config)
    conn = state_db.connect(cfg.storage.state_db_path)
    state_db.init_schema(conn)

    since = (datetime.now(timezone.utc) - timedelta(days=args.window_days)).isoformat()

    # Utilization: rough estimate using resolved paper trades.
    # dollar-days deployed vs dollar-days available.
    total_capital = cfg.allocation.total_capital_usd
    window_days = args.window_days
    total_capital_dollar_days = float(total_capital) * window_days

    rows = conn.execute(
        """
        SELECT
            SUM(CAST(capital_locked_usd AS REAL)
                * ((julianday(COALESCE(resolved_at, resolution_date)) - julianday(opened_at)))) AS dollar_days
        FROM paper_trades
        WHERE opened_at >= ?
        """,
        (since,),
    ).fetchone()
    deployed = float(rows["dollar_days"] or 0)
    utilization = deployed / total_capital_dollar_days if total_capital_dollar_days > 0 else 0
    unused = 1 - min(1.0, utilization)

    # Trade count per day.
    cnt = conn.execute(
        "SELECT COUNT(*) AS c FROM paper_trades WHERE opened_at >= ?", (since,)
    ).fetchone()
    trades = cnt["c"] or 0
    trades_per_day = trades / max(1, window_days)

    # Live data, if any.
    live_rows = conn.execute(
        "SELECT COUNT(*) AS total, SUM(CASE WHEN live_partial_fill=1 THEN 1 ELSE 0 END) AS partial FROM execution_records WHERE executed_at >= ?",
        (since,),
    ).fetchone()
    live_total = live_rows["total"] or 0
    live_partials = live_rows["partial"] or 0

    # Vanish rate not directly queryable in paper mode (no "detected but not
    # filled" counter here); for Phase 3+ with paired logging this would be
    # enriched. Placeholder: 0 until live data arrives.
    vanish_rate = 0.0
    if live_total > 0:
        # Rough: if partial_fill rate is high, call it a latency proxy.
        vanish_rate = (live_partials or 0) / live_total

    # Errors as a weak "attention" signal.
    err_row = conn.execute(
        "SELECT COUNT(*) AS c FROM errors WHERE occurred_at >= ?", (since,)
    ).fetchone()
    errors = err_row["c"] or 0
    error_rate_per_day = errors / max(1, window_days)

    # Classification. Priority order mirrors the doc.
    diagnosis = []
    if unused > 0.6:
        diagnosis.append(("capital", "Unused capital fraction > 60%. Bottleneck is edge, not capital. "
                                     "Adding more $ won't help; add a new strategy or platform."))
    if trades_per_day > 30:
        diagnosis.append(("edge", "30+ trades/day suggests the opportunity pool is saturating your capital. "
                                  "Consider raising per-trade size OR a higher-capacity platform."))
    if vanish_rate > 0.4:
        diagnosis.append(("latency", "40%+ partial fills or vanished opportunities. Infrastructure work "
                                     "(colocation, faster clients, pre-signed txs) is the move. Adding "
                                     "platforms repeats the same latency problem."))
    if error_rate_per_day > 5:
        diagnosis.append(("attention", "5+ errors/day implies operational load is climbing. Automate "
                                       "(Phase 4-style tooling) before adding strategies."))

    if not diagnosis:
        diagnosis.append(("none_detected",
                          "No single constraint dominates. Either steady state or not enough data. "
                          "Collect more weeks before expanding."))

    out = []
    out.append(f"# Capacity Report — last {args.window_days} days")
    out.append("")
    out.append(f"Total capital configured: ${total_capital}")
    out.append(f"Capital dollar-days deployed: {deployed:,.1f}")
    out.append(f"Capital dollar-days available: {total_capital_dollar_days:,.1f}")
    out.append(f"**Utilization:** {utilization:.1%}  (unused: {unused:.1%})")
    out.append("")
    out.append(f"Trades (resolved + open) in window: {trades}  (≈{trades_per_day:.1f}/day)")
    out.append(f"Live records: {live_total}  (partial fills: {live_partials})")
    out.append(f"Errors logged: {errors}  (≈{error_rate_per_day:.1f}/day)")
    out.append("")
    out.append("## Diagnosis")
    for name, msg in diagnosis:
        out.append(f"- **{name}**: {msg}")
    out.append("")
    out.append("## Doc rule reminder")
    out.append("> Diagnose before expanding. Expansion without diagnosis adds complexity without adding edge.")

    text = "\n".join(out) + "\n"
    if args.output:
        Path(args.output).write_text(text)
        print(f"wrote {args.output}")
    else:
        print(text)

    # Also emit machine-readable for automation.
    data = {
        "window_days": args.window_days,
        "utilization_pct": utilization,
        "unused_pct": unused,
        "trades": trades,
        "trades_per_day": trades_per_day,
        "live_records": live_total,
        "live_partial_fills": live_partials,
        "vanish_rate": vanish_rate,
        "errors_per_day": error_rate_per_day,
        "diagnosis": [{"category": n, "message": m} for n, m in diagnosis],
    }
    if args.output:
        Path(args.output + ".json").write_text(json.dumps(data, indent=2))


if __name__ == "__main__":
    main()
