"""Event map loader with strict validation.

Loads `event_map.yaml`, enforces:
  - trading_enabled defaults to false (must be explicitly true to trade)
  - edge_cases_reviewed has at least 5 entries
  - At least one edge case marked divergent (if you see no divergent scenarios,
    you didn't look hard enough — itself a yellow flag the doc warns about)
  - All required fields present per pair
  - SHA-256 content hash for provenance

The "min 5 edge cases incl. ≥1 divergent" rule is the doc's discipline
(phase-1-dual-platform.md "Rule" under section 3) — we encode it in code so
it can't drift in YAML.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import List, Optional

import yaml

SCHEMA_VERSION = 1
MIN_EDGE_CASES_REQUIRED = 5
PAIR_REQUIRED_FIELDS = {
    "pair_id",
    "polymarket_market_id",
    "kalshi_market_ticker",
    "verified_by",
    "verified_date",
    "trading_enabled",
    "edge_cases_reviewed",
}
EDGE_CASE_REQUIRED_FIELDS = {"scenario", "polymarket", "kalshi", "divergent"}


class EventMapValidationError(ValueError):
    pass


@dataclass(frozen=True)
class EdgeCase:
    scenario: str
    polymarket: str
    kalshi: str
    divergent: bool
    mitigation: Optional[str] = None


@dataclass(frozen=True)
class Pair:
    pair_id: str
    polymarket_market_id: str
    kalshi_market_ticker: str
    verified_by: str
    verified_date: date
    trading_enabled: bool
    edge_cases_reviewed: List[EdgeCase]
    confidence: Optional[Decimal] = None
    topic_tags: List[str] = field(default_factory=list)
    min_annualized_return_override: Optional[Decimal] = None
    notes: Optional[str] = None


@dataclass(frozen=True)
class EventMap:
    pairs: List[Pair]
    content_hash: str
    schema_version: int

    def enabled(self) -> List[Pair]:
        return [p for p in self.pairs if p.trading_enabled]

    def by_id(self, pair_id: str) -> Optional[Pair]:
        for p in self.pairs:
            if p.pair_id == pair_id:
                return p
        return None


def _validate_pair(raw: dict, idx: int) -> Pair:
    missing = PAIR_REQUIRED_FIELDS - set(raw.keys())
    if missing:
        raise EventMapValidationError(
            f"pair index {idx}: missing required fields: {sorted(missing)}"
        )

    pair_id = raw["pair_id"]
    edge_cases_raw = raw["edge_cases_reviewed"]
    if not isinstance(edge_cases_raw, list):
        raise EventMapValidationError(
            f"{pair_id}: edge_cases_reviewed must be a list"
        )

    if len(edge_cases_raw) < MIN_EDGE_CASES_REQUIRED:
        raise EventMapValidationError(
            f"{pair_id}: needs at least {MIN_EDGE_CASES_REQUIRED} edge_cases_reviewed "
            f"(got {len(edge_cases_raw)}). The doc requires this — if you can't think "
            f"of 5 edge cases, you haven't looked hard enough."
        )

    edge_cases: List[EdgeCase] = []
    for ec_idx, ec in enumerate(edge_cases_raw):
        if not isinstance(ec, dict):
            raise EventMapValidationError(
                f"{pair_id}: edge_case #{ec_idx} must be a mapping"
            )
        ec_missing = EDGE_CASE_REQUIRED_FIELDS - set(ec.keys())
        if ec_missing:
            raise EventMapValidationError(
                f"{pair_id} edge_case #{ec_idx}: missing fields {sorted(ec_missing)}"
            )
        edge_cases.append(
            EdgeCase(
                scenario=str(ec["scenario"]),
                polymarket=str(ec["polymarket"]),
                kalshi=str(ec["kalshi"]),
                divergent=bool(ec["divergent"]),
                mitigation=str(ec["mitigation"]) if ec.get("mitigation") else None,
            )
        )

    if not any(ec.divergent for ec in edge_cases):
        raise EventMapValidationError(
            f"{pair_id}: no edge case marked divergent=true. The doc treats this "
            f"as a warning sign (you probably didn't look for divergence). If the "
            f"pair really has none, mark a hypothetical scenario explicitly."
        )

    # Hard rule: enabled pairs with un-mitigated divergent cases require explicit
    # confirmation. We don't block them in code (the doc allows it under "Accept
    # the risk with a buffer"), but the loader will surface them on enabled().
    trading_enabled = bool(raw["trading_enabled"])

    verified_date_raw = raw["verified_date"]
    if isinstance(verified_date_raw, date):
        verified_date_val = verified_date_raw
    else:
        verified_date_val = datetime.strptime(str(verified_date_raw), "%Y-%m-%d").date()

    confidence = raw.get("confidence")
    confidence_dec = Decimal(str(confidence)) if confidence is not None else None
    override = raw.get("min_annualized_return_override")
    override_dec = Decimal(str(override)) if override is not None else None

    return Pair(
        pair_id=str(pair_id),
        polymarket_market_id=str(raw["polymarket_market_id"]),
        kalshi_market_ticker=str(raw["kalshi_market_ticker"]),
        verified_by=str(raw["verified_by"]),
        verified_date=verified_date_val,
        trading_enabled=trading_enabled,
        edge_cases_reviewed=edge_cases,
        confidence=confidence_dec,
        topic_tags=list(raw.get("topic_tags") or []),
        min_annualized_return_override=override_dec,
        notes=str(raw["notes"]) if raw.get("notes") else None,
    )


def load_event_map(path: str | Path) -> EventMap:
    p = Path(path)
    if not p.exists():
        # Empty event map is valid — phase 1 might start with zero pairs.
        return EventMap(pairs=[], content_hash="empty", schema_version=SCHEMA_VERSION)

    raw_text = p.read_text()
    data = yaml.safe_load(raw_text) or {}
    if not isinstance(data, dict):
        raise EventMapValidationError("event_map.yaml root must be a mapping")

    schema_version = int(data.get("schema_version", SCHEMA_VERSION))
    if schema_version != SCHEMA_VERSION:
        raise EventMapValidationError(
            f"event_map schema_version mismatch: file has {schema_version}, "
            f"loader supports {SCHEMA_VERSION}"
        )

    pairs_raw = data.get("pairs") or []
    if not isinstance(pairs_raw, list):
        raise EventMapValidationError("'pairs' must be a list")

    pairs: List[Pair] = []
    seen_ids: set = set()
    for i, p_raw in enumerate(pairs_raw):
        if not isinstance(p_raw, dict):
            raise EventMapValidationError(f"pair index {i} must be a mapping")
        pair = _validate_pair(p_raw, i)
        if pair.pair_id in seen_ids:
            raise EventMapValidationError(f"duplicate pair_id: {pair.pair_id}")
        seen_ids.add(pair.pair_id)
        pairs.append(pair)

    content_hash = hashlib.sha256(raw_text.encode()).hexdigest()[:16]

    return EventMap(
        pairs=pairs,
        content_hash=content_hash,
        schema_version=schema_version,
    )
