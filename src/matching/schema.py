"""Structured extraction schema (ResolutionCriteria) + per-event-type vocabularies.

The doc's core design decision: the LLM does NOT classify "are these the same
market". The LLM EXTRACTS a structured JSON representation of the resolution
criteria for each market independently. Then deterministic code compares.

Why: extraction is a stable LLM task. Classification with confidence scores
drifts silently and is hard to audit. Extraction failures are visible — you
look at the JSON and see which field is wrong.

Controlled vocabularies per event_type: each Fed market must have the same
set of edge_case keys (e.g., "meeting_postponed", "asymmetric_range_change");
a sports market has a different fixed set. The extractor is prompted with
these vocabularies. Missing keys → low confidence → review queue.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = 1


class ResolutionCriteria(BaseModel):
    """The structured extraction of one market's resolution rules."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    event_type: str
    event_date_start: datetime
    event_date_end: datetime
    primary_predicate: str
    resolution_source: str
    resolution_metric: str
    resolution_threshold: Optional[Decimal] = None
    resolution_direction: str  # "greater_than" | "less_than" | "equal_to" | "binary" | "less_than_previous"
    edge_cases: Dict[str, str] = Field(default_factory=dict)

    # Meta
    confidence_overall: float = 0.0
    confidence_per_field: Dict[str, float] = Field(default_factory=dict)
    raw_rules_hash: str = ""
    description_hash: str = ""
    llm_model_version: str = ""
    schema_version: int = SCHEMA_VERSION

    def canonical_fields(self) -> dict:
        """Subset used by the deterministic matcher. Drops confidence/meta fields."""
        return {
            "event_type": self.event_type,
            "primary_predicate": self.primary_predicate,
            "resolution_source": self.resolution_source,
            "resolution_metric": self.resolution_metric,
            "resolution_direction": self.resolution_direction,
            "resolution_threshold": str(self.resolution_threshold)
            if self.resolution_threshold is not None
            else None,
            "edge_cases": dict(self.edge_cases),
        }


# ---- Event-type vocabularies ----
# Each event_type defines the required edge_case keys that the extractor must
# populate. The matcher compares these keys across two extracted criteria and
# a difference on ANY one of them is a divergence (which becomes a match
# rejection or review-queue entry).


EVENT_TYPE_VOCABULARIES: Dict[str, List[str]] = {
    "fed_rate_decision": [
        "standard_cut",
        "standard_hold",
        "inter_meeting_cut_before",
        "inter_meeting_cut_after",
        "meeting_postponed",
        "asymmetric_range_change",
        "meeting_statement_delayed_past_midnight",
    ],
    "election_outcome": [
        "primary_winner",
        "runoff",
        "death_of_candidate",
        "withdrawal",
        "ballot_disputes",
        "certification_delayed",
    ],
    "sports_match": [
        "regulation_winner",
        "overtime",
        "shootout",
        "postponement",
        "forfeit",
        "tie_allowed",
    ],
    "crypto_threshold": [
        "above_at_moment",
        "above_at_close",
        "flash_crash",
        "exchange_settlement_source",
        "leap_second_ambiguity",
    ],
    "macro_release": [
        "value_above_threshold",
        "revision_after_release",
        "release_delayed",
        "partial_release",
    ],
}


def required_edge_cases(event_type: str) -> List[str]:
    """Return the required edge_case keys for a given event type.

    If event_type is unknown, returns empty list (caller should flag for review).
    """
    return list(EVENT_TYPE_VOCABULARIES.get(event_type, []))


def known_event_types() -> List[str]:
    return sorted(EVENT_TYPE_VOCABULARIES.keys())
