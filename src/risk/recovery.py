"""Crash recovery + state-loaded gate.

On startup the orchestrator must:
  1. Load the kill switch state. If anything is tripped in enforce mode,
     refuse to begin trading and alert.
  2. Run reconciliation immediately. If mismatches surface, refuse to begin.
  3. Confirm the in-memory state mirrors the DB (open positions, capital
     locked) BEFORE the first scan cycle.

Step 3 is the "benign restart race" guard from the doc — without it, a fast
restart can launch a scan cycle before paper position state has loaded, and
the allocator double-commits capital it doesn't actually have.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

from src.risk.reconcile import reconcile_paper_state
from src.storage import state_db


@dataclass
class RecoveryReport:
    tripped_triggers: List[str]
    reconcile_errors: int
    open_positions: int
    capital_locked_usd: float

    @property
    def safe_to_trade(self) -> bool:
        return not self.tripped_triggers and self.reconcile_errors == 0


def perform_recovery(conn) -> RecoveryReport:
    """Run all the post-restart safety checks. Returns a report.

    Caller (orchestrator) decides what to do — typically:
      - if not safe_to_trade: log + alert + sleep instead of scanning
      - else: proceed with normal cycle
    """
    state_db.init_schema(conn)
    tripped = state_db.any_kill_switch_tripped(conn)
    rec = reconcile_paper_state(conn)
    open_pos = state_db.open_positions(conn)
    capital = float(state_db.total_capital_locked(conn))
    return RecoveryReport(
        tripped_triggers=tripped,
        reconcile_errors=rec.mismatch_count,
        open_positions=len(open_pos),
        capital_locked_usd=capital,
    )
