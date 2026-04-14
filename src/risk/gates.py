"""Three-gate approval system for Phase 3 live transition.

From phase-3-paper-to-live.md: gate 1 → gate 2 → gate 3, ending in normal ops.
We encode the thresholds + caps + graduation rules in code so human "gut feel"
can't shortcut discipline. Each gate has:

  - annualized_threshold: only opportunities at or above this clear the gate
  - size_cap_usd: hard cap per trade
  - post_fill_pause_seconds: sleep after each fill
  - graduation: how many successful fills must accumulate AND (for gates 2/3)
    what calibration coverage must hold.

Graduation is evaluated each cycle against the rolling window. If satisfied,
the current_gate advances. Downgrade never happens automatically — a
calibration regression in gate 3 triggers a CRITICAL alert and suggests a
manual restart at gate 2, but the code doesn't silently downgrade (too
dangerous).
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional


class Gate(str, enum.Enum):
    GATE_1 = "gate_1"
    GATE_2 = "gate_2"
    GATE_3 = "gate_3"


@dataclass(frozen=True)
class GateConfig:
    annualized_threshold: Decimal
    size_cap_usd: Decimal
    post_fill_pause_seconds: int
    min_days_at_gate: int
    min_successful_fills: int
    min_calibration_coverage: Optional[float] = None  # None means skip calibration check


DEFAULT_GATE_CONFIGS = {
    Gate.GATE_1: GateConfig(
        annualized_threshold=Decimal("0.60"),
        size_cap_usd=Decimal("10"),
        post_fill_pause_seconds=10,
        min_days_at_gate=3,
        min_successful_fills=5,
        min_calibration_coverage=None,  # sample too small for calibration in Gate 1
    ),
    Gate.GATE_2: GateConfig(
        annualized_threshold=Decimal("0.30"),
        size_cap_usd=Decimal("20"),
        post_fill_pause_seconds=5,
        min_days_at_gate=4,
        min_successful_fills=15,
        min_calibration_coverage=0.85,
    ),
    Gate.GATE_3: GateConfig(
        annualized_threshold=Decimal("0.20"),
        size_cap_usd=Decimal("25"),
        post_fill_pause_seconds=0,
        min_days_at_gate=7,
        min_successful_fills=0,  # no further graduation — stay at 3
        min_calibration_coverage=0.85,
    ),
}


@dataclass
class GateState:
    """Running state. Persisted in SQLite as serialized fields.

    The orchestrator owns one of these. Each cycle it calls evaluate_for_graduation
    to see if conditions for advancing are met.
    """

    current_gate: Gate = Gate.GATE_1
    gate_entered_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    successful_fills_at_gate: int = 0
    calibration_coverage_recent: Optional[float] = None

    def days_at_gate(self, now: datetime) -> float:
        return (now - self.gate_entered_at).total_seconds() / 86400.0

    def record_fill(self) -> None:
        self.successful_fills_at_gate += 1

    def set_calibration_coverage(self, coverage: float) -> None:
        self.calibration_coverage_recent = coverage

    def config(
        self, configs: Optional[dict] = None
    ) -> GateConfig:
        return (configs or DEFAULT_GATE_CONFIGS)[self.current_gate]

    def evaluate_for_graduation(
        self, now: datetime, configs: Optional[dict] = None
    ) -> Optional[Gate]:
        """Return the next gate if conditions are met, else None.

        Does NOT mutate state. Caller decides whether to advance.
        """
        cfg = (configs or DEFAULT_GATE_CONFIGS)[self.current_gate]
        # Gate 3 has no further graduation target.
        if self.current_gate == Gate.GATE_3:
            return None

        if self.days_at_gate(now) < cfg.min_days_at_gate:
            return None
        if self.successful_fills_at_gate < cfg.min_successful_fills:
            return None
        if (
            cfg.min_calibration_coverage is not None
            and (
                self.calibration_coverage_recent is None
                or self.calibration_coverage_recent < cfg.min_calibration_coverage
            )
        ):
            return None

        next_gate = {
            Gate.GATE_1: Gate.GATE_2,
            Gate.GATE_2: Gate.GATE_3,
        }[self.current_gate]
        return next_gate

    def advance_to(self, gate: Gate, now: datetime) -> None:
        self.current_gate = gate
        self.gate_entered_at = now
        self.successful_fills_at_gate = 0
        self.calibration_coverage_recent = None
