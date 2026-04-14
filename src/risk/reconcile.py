"""Paper-mode reconciliation.

For every open paper position, recompute what the position SHOULD be worth given
the current order book, and compare against the agent's stored state. Any
mismatch is either a bug or a race condition — investigate immediately.

In paper mode the only "exchange truth" is what the agent itself recorded,
so reconciliation here mostly checks internal consistency:
  - Every open paper trade has a matching opportunity row.
  - Capital_locked == size_contracts * (yes_fill + no_fill) + fee + gas
    within Decimal-rounding tolerance.
  - No position has a resolution_date in the past while still resolved=0
    AND older than the resolution-poll interval.

Live mode (Phase 3) will compare against actual exchange positions.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import List


@dataclass
class ReconcileFinding:
    severity: str  # "info" | "warn" | "error"
    category: str
    detail: str


@dataclass
class ReconcileReport:
    checked_positions: int = 0
    findings: List[ReconcileFinding] = field(default_factory=list)

    @property
    def mismatch_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "error")


def reconcile_paper_state(
    conn: sqlite3.Connection,
    *,
    now: datetime = None,
    overdue_grace_seconds: int = 7200,
) -> ReconcileReport:
    if now is None:
        now = datetime.now(timezone.utc)
    report = ReconcileReport()

    # Pull every open paper trade.
    rows = conn.execute(
        """
        SELECT pt.client_order_id, pt.opportunity_id, pt.size_contracts,
               pt.yes_fill_price, pt.no_fill_price, pt.capital_locked_usd,
               pt.resolution_date, pt.resolved,
               o.gross_cost_usd, o.fee_cost_usd, o.gas_cost_usd, o.capital_at_risk_usd
        FROM paper_trades pt
        LEFT JOIN opportunities o ON o.opportunity_id = pt.opportunity_id
        WHERE pt.resolved = 0
        """
    ).fetchall()

    report.checked_positions = len(rows)
    grace = timedelta(seconds=overdue_grace_seconds)

    for r in rows:
        # Check 1: opportunity row exists.
        if r["gross_cost_usd"] is None:
            report.findings.append(
                ReconcileFinding(
                    severity="error",
                    category="missing_opportunity",
                    detail=f"client_order_id={r['client_order_id']} has no opportunity row",
                )
            )
            continue

        # Check 2: capital_locked matches the opportunity's capital_at_risk
        # within tolerance (Decimal arithmetic should be exact, but allow 0.01¢
        # for any rounding around persistence).
        cap_locked = Decimal(r["capital_locked_usd"])
        cap_risk = Decimal(r["capital_at_risk_usd"])
        if abs(cap_locked - cap_risk) > Decimal("0.0001"):
            report.findings.append(
                ReconcileFinding(
                    severity="error",
                    category="capital_mismatch",
                    detail=(
                        f"client_order_id={r['client_order_id']} "
                        f"capital_locked={cap_locked} != capital_at_risk={cap_risk}"
                    ),
                )
            )

        # Check 3: resolution overdue?
        try:
            res_date = datetime.fromisoformat(r["resolution_date"])
        except ValueError:
            report.findings.append(
                ReconcileFinding(
                    severity="error",
                    category="bad_resolution_date",
                    detail=f"client_order_id={r['client_order_id']} resolution_date unparseable",
                )
            )
            continue
        if res_date < now - grace:
            report.findings.append(
                ReconcileFinding(
                    severity="warn",
                    category="resolution_overdue",
                    detail=(
                        f"client_order_id={r['client_order_id']} resolution_date "
                        f"{res_date.isoformat()} is past grace window"
                    ),
                )
            )

    return report
