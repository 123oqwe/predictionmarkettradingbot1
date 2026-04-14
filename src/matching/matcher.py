"""Deterministic matcher — pure code, no LLM.

Given two ResolutionCriteria JSONs, decide whether the markets resolve to the
same outcome on every relevant scenario. If so, MATCH. Otherwise list the
exact fields that differ — that list is directly debuggable.

Per-event-type date tolerance: Fed FOMC meetings can resolve within hours of
each other across platforms (tz differences, after-hours statement), so we
allow 6h. NFP / CPI releases happen in a single instant — tolerance = 0h.
Sports with regulation time varies with overtime — 2h tolerance is enough
to handle the end-of-game-vs-end-of-overtime corner.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List

from src.matching.schema import ResolutionCriteria, required_edge_cases

DATE_TOLERANCE_HOURS_BY_EVENT: Dict[str, int] = {
    "fed_rate_decision": 6,
    "election_outcome": 24,
    "sports_match": 2,
    "crypto_threshold": 0,
    "macro_release": 0,
}

_DEFAULT_TOLERANCE_HOURS = 6


@dataclass
class MatchResult:
    match: bool
    confidence: float  # derived from source confidences + diff count
    differences: List[str] = field(default_factory=list)
    requires_review: bool = False  # medium-confidence + minor differences → review queue

    def __str__(self) -> str:
        kind = "MATCH" if self.match else "NO-MATCH"
        if self.requires_review:
            kind = "REVIEW"
        diffs = ", ".join(self.differences) if self.differences else "none"
        return f"{kind} conf={self.confidence:.2f} diffs=[{diffs}]"


def _dates_overlap(
    a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime, tolerance: timedelta
) -> bool:
    """True if [a_start, a_end] and [b_start, b_end] are within `tolerance`."""
    return (a_start - tolerance) <= b_end and (b_start - tolerance) <= a_end


def _predicates_equivalent(a: str, b: str) -> bool:
    """Semantic equivalence check, conservative.

    Start with normalized-string equality. We keep this strict on purpose —
    "rate_cut" and "rate_decrease" should both be normalized upstream in the
    prompt to `rate_cut`. Divergence here is worth flagging.
    """
    return a.strip().lower().replace("-", "_") == b.strip().lower().replace("-", "_")


def _sources_compatible(a: str, b: str) -> bool:
    """Some resolution sources are interchangeable, others aren't.

    Example: `fomc_statement` ≈ `federal_reserve_announcement` (same data
    published minutes apart). `cme_settlement` ≠ `coinbase_spot_close`
    (different prices, different times).

    We keep this conservative: only mark equivalent when we've explicitly
    whitelisted the pair. Default is "not compatible" — prefer false negatives
    (rejected arbs) over false positives (losing money on rule divergence).
    """
    a_norm = a.strip().lower()
    b_norm = b.strip().lower()
    if a_norm == b_norm:
        return True

    equivalence_classes = [
        {"fomc_statement", "federal_reserve_announcement"},
        {"associated_press", "ap_election_call", "ap_race_call"},
        {"nba_box_score", "nba_official_scorebook"},
    ]
    return any(a_norm in cls and b_norm in cls for cls in equivalence_classes)


def compare(a: ResolutionCriteria, b: ResolutionCriteria) -> MatchResult:
    """Compare two extracted criteria. Returns MatchResult with explicit diffs."""
    differences: List[str] = []

    if a.event_type != b.event_type:
        return MatchResult(
            match=False,
            confidence=0.0,
            differences=[f"event_type: {a.event_type} vs {b.event_type}"],
        )

    tolerance = timedelta(
        hours=DATE_TOLERANCE_HOURS_BY_EVENT.get(a.event_type, _DEFAULT_TOLERANCE_HOURS)
    )
    if not _dates_overlap(a.event_date_start, a.event_date_end, b.event_date_start, b.event_date_end, tolerance):
        differences.append(
            f"event_date: [{a.event_date_start.isoformat()}, {a.event_date_end.isoformat()}] "
            f"vs [{b.event_date_start.isoformat()}, {b.event_date_end.isoformat()}]"
        )

    if not _predicates_equivalent(a.primary_predicate, b.primary_predicate):
        differences.append(f"primary_predicate: {a.primary_predicate} vs {b.primary_predicate}")

    if not _sources_compatible(a.resolution_source, b.resolution_source):
        differences.append(f"resolution_source: {a.resolution_source} vs {b.resolution_source}")

    if a.resolution_metric != b.resolution_metric:
        differences.append(f"resolution_metric: {a.resolution_metric} vs {b.resolution_metric}")

    if a.resolution_direction != b.resolution_direction:
        differences.append(f"resolution_direction: {a.resolution_direction} vs {b.resolution_direction}")

    # Edge cases: compare all common keys. Also flag any required key
    # missing from one side.
    required = set(required_edge_cases(a.event_type))
    a_keys = set(a.edge_cases.keys())
    b_keys = set(b.edge_cases.keys())

    for key in sorted(required & a_keys & b_keys):
        if a.edge_cases[key] != b.edge_cases[key]:
            differences.append(f"edge_case.{key}: {a.edge_cases[key]} vs {b.edge_cases[key]}")

    for key in sorted(required - (a_keys & b_keys)):
        differences.append(f"missing_edge_case.{key}")

    # Keys one side has but the other doesn't (and not in required vocab) are
    # low-severity warnings.
    extra_one_sided = (a_keys ^ b_keys) - required
    for key in sorted(extra_one_sided):
        differences.append(f"asymmetric_edge_case.{key}")

    # Derive confidence from (a) source confidences, (b) number of differences.
    min_src_conf = min(a.confidence_overall, b.confidence_overall)
    penalty = 0.1 * len(differences)
    confidence = max(0.0, min_src_conf - penalty)

    match = len(differences) == 0
    requires_review = (
        not match
        and confidence >= 0.6
        and all(not d.startswith("event_type") for d in differences)
    )

    return MatchResult(
        match=match,
        confidence=confidence,
        differences=differences,
        requires_review=requires_review,
    )
