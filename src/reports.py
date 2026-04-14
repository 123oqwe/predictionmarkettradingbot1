"""Daily / per-cycle report generation.

Pulls from SQLite + in-memory cycle state to produce a scannable summary with
annualized-return histograms split by strategy.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, List

from src.layer3_strategy.adverse_selection import FilterStats
from src.layer3_strategy.models import Opportunity

_BUCKETS = [
    (Decimal("0"), Decimal("0.20"), "0-20%"),
    (Decimal("0.20"), Decimal("0.30"), "20-30%"),
    (Decimal("0.30"), Decimal("0.50"), "30-50%"),
    (Decimal("0.50"), Decimal("1.00"), "50-100%"),
    (Decimal("1.00"), Decimal("5.00"), "100-500%"),
    (Decimal("5.00"), Decimal("1000"), ">500%"),
]


@dataclass
class CycleReport:
    """Per-cycle stats. Maintained in-memory and rendered by the orchestrator."""

    markets_polymarket: int = 0
    markets_kalshi: int = 0
    intra_detected: List[Opportunity] = field(default_factory=list)
    cross_detected: List[Opportunity] = field(default_factory=list)
    intra_filter_stats: FilterStats = field(default_factory=FilterStats)
    cross_filter_stats: FilterStats = field(default_factory=FilterStats)
    intra_passed: List[Opportunity] = field(default_factory=list)
    cross_passed: List[Opportunity] = field(default_factory=list)
    allocations_count: int = 0
    capital_allocated_this_cycle: Decimal = Decimal(0)


def _bucket(opps: List[Opportunity]) -> Dict[str, int]:
    counts: Dict[str, int] = {label: 0 for _, _, label in _BUCKETS}
    for o in opps:
        for lo, hi, label in _BUCKETS:
            if lo <= o.annualized_return < hi:
                counts[label] += 1
                break
    return counts


def _bar(n: int, scale: int = 1) -> str:
    return "█" * min(n // scale, 30)


def render_cycle(report: CycleReport, *, header: str) -> str:
    lines = [header]
    lines.append(
        f"Markets scanned: polymarket={report.markets_polymarket}  kalshi={report.markets_kalshi}"
    )
    lines.append(
        f"Detected (raw):  intra={len(report.intra_detected)}  cross={len(report.cross_detected)}"
    )
    intra_rejs = ", ".join(f"{k}:{v}" for k, v in report.intra_filter_stats.rejected.items())
    cross_rejs = ", ".join(f"{k}:{v}" for k, v in report.cross_filter_stats.rejected.items())
    lines.append(
        f"After filters:   intra={len(report.intra_passed)} (rej: {intra_rejs or 'none'})"
    )
    lines.append(
        f"                 cross={len(report.cross_passed)} (rej: {cross_rejs or 'none'})"
    )

    if report.intra_passed:
        lines.append("Intra annualized distribution:")
        for label, n in _bucket(report.intra_passed).items():
            if n > 0:
                lines.append(f"  {label:>8}: {_bar(n)} {n}")

    if report.cross_passed:
        lines.append("Cross annualized distribution:")
        for label, n in _bucket(report.cross_passed).items():
            if n > 0:
                lines.append(f"  {label:>8}: {_bar(n)} {n}")

    lines.append(
        f"Allocations: {report.allocations_count}  "
        f"capital_committed=${report.capital_allocated_this_cycle:.2f}"
    )
    return "\n".join(lines)
