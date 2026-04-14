"""After-tax PnL CLI.

Reads resolved paper_trades + execution_records, splits by platform, applies
tax config, prints the benchmark comparison.

Usage:
    python scripts/aftertax_report.py --config config.yaml --window-days 30
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_config  # noqa: E402
from src.storage import state_db  # noqa: E402
from src.tax import compute_after_tax, default_us_nyc  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--window-days", type=int, default=30)
    args = p.parse_args()
    cfg = load_config(args.config)
    conn = state_db.connect(cfg.storage.state_db_path)
    state_db.init_schema(conn)

    since = (datetime.now(timezone.utc) - timedelta(days=args.window_days)).isoformat()
    rows = conn.execute(
        """
        SELECT platform, SUM(CAST(realized_pnl_usd AS REAL)) AS s,
               SUM(CAST(capital_locked_usd AS REAL)) AS c
        FROM paper_trades
        WHERE resolved = 1 AND resolved_at >= ?
        GROUP BY platform
        """,
        (since,),
    ).fetchall()
    by_platform = {r["platform"]: (Decimal(str(r["s"] or 0)), Decimal(str(r["c"] or 0))) for r in rows}

    polymarket_pnl, polymarket_cap = by_platform.get("polymarket", (Decimal(0), Decimal(0)))
    kalshi_pnl, kalshi_cap = by_platform.get("kalshi", (Decimal(0), Decimal(0)))
    total_cap = polymarket_cap + kalshi_cap

    tax_cfg = default_us_nyc()
    report = compute_after_tax(
        polymarket_pnl_usd=polymarket_pnl,
        kalshi_pnl_usd=kalshi_pnl,
        period_days=args.window_days,
        cfg=tax_cfg,
        capital_deployed_usd=total_cap,
    )

    print(f"# After-tax report — last {args.window_days} days")
    print()
    print(f"Gross PnL: ${report.gross_pnl:.2f}")
    print(f"  Polymarket: ${report.polymarket_pnl:.2f}  (ordinary income)")
    print(f"  Kalshi:     ${report.kalshi_pnl:.2f}  (Section 1256)")
    print()
    print(f"Estimated tax: ${report.estimated_tax_polymarket + report.estimated_tax_kalshi:.2f} "
          f"(effective {report.effective_tax_rate:.1%})")
    print()
    print(f"**Net PnL:** ${report.net_pnl:.2f}")
    print(f"**Annualized net return:** {report.annualized_net:.2%}")
    print(f"Benchmark (risk_free + premium): {report.benchmark_rate:.2%}")
    print(f"Excess over benchmark: {report.excess_over_benchmark:.2%}")
    print()
    if report.excess_over_benchmark < 0:
        print("🛑 Negative excess over benchmark. You're losing to T-bills after tax. "
              "Evaluate whether this strategy is worth continuing.")
    else:
        print("✅ Positive excess. Strategy clears the benchmark after tax.")


if __name__ == "__main__":
    main()
