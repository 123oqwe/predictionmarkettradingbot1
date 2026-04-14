"""Round B #6: close the review queue loop.

Previous: review.py logged decisions to JSONL but the operator manually
copied approved pairs into event_map.yaml. Loop broken.

Now: approvals automatically append to event_map.yaml with
`trading_enabled: false` (conservative — operator still must flip true
after a final glance). Review log remains append-only for audit.

Decisions:
  a = approve → write to event_map.yaml, trading_enabled=false
  m = conditional → same but with mitigation note
  r = reject → log only, never added to map
  s = skip → no state change
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, Optional

import yaml


@dataclass(frozen=True)
class ReviewDecision:
    pair_id: str
    decision: str  # "approve" | "conditional" | "reject" | "skip"
    decided_at: datetime
    decided_by: str
    note: Optional[str] = None


def write_decision_log(log_path: Path, decision: ReviewDecision) -> None:
    """Append the decision as a JSON line. Idempotent-ish — same pair_id
    written twice will produce two lines (audit trail)."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a") as f:
        f.write(json.dumps({
            "pair_id": decision.pair_id,
            "decision": decision.decision,
            "decided_at": decision.decided_at.isoformat(),
            "decided_by": decision.decided_by,
            "note": decision.note,
        }) + "\n")


def append_approved_to_event_map(
    event_map_path: Path,
    candidate: Dict,
    *,
    mitigation_note: Optional[str] = None,
) -> None:
    """Write an approved candidate into event_map.yaml.

    SAFETY: always writes `trading_enabled: false`. The operator must
    manually flip to true after reviewing the edge cases one more time.
    This is the belt-and-suspenders: automation brought it here,
    human-in-the-loop opens the gate.
    """
    if event_map_path.exists():
        data = yaml.safe_load(event_map_path.read_text()) or {}
    else:
        data = {}

    data.setdefault("schema_version", 1)
    pairs = data.setdefault("pairs", [])

    # Dedup by pair_id — if already present, skip (don't overwrite operator tweaks).
    existing_ids = {p.get("pair_id") for p in pairs if isinstance(p, dict)}
    if candidate.get("pair_id") in existing_ids:
        return

    new_pair = {
        "pair_id": candidate["pair_id"],
        "polymarket_market_id": candidate["polymarket_market_id"],
        "kalshi_market_ticker": candidate["kalshi_market_ticker"],
        "verified_by": candidate.get("verified_by", "auto_discovery"),
        "verified_date": str(candidate.get("verified_date") or date.today()),
        "trading_enabled": False,  # ALWAYS false on auto-approval
        "topic_tags": list(candidate.get("topic_tags", [])),
        "edge_cases_reviewed": list(candidate.get("edge_cases_reviewed", [])),
        "notes": candidate.get("notes", "Auto-approved via review queue; verify before enabling."),
    }
    if mitigation_note:
        new_pair["notes"] = (new_pair["notes"] or "") + f"\nMitigation: {mitigation_note}"

    pairs.append(new_pair)
    event_map_path.write_text(yaml.safe_dump(data, sort_keys=False))


def process_decision(
    candidate: Dict,
    decision_choice: str,
    *,
    event_map_path: Path,
    log_path: Path,
    decided_by: str,
    mitigation_note: Optional[str] = None,
) -> ReviewDecision:
    """Main entry point: take a candidate + operator choice → full loop."""
    choice_map = {"a": "approve", "m": "conditional", "r": "reject", "s": "skip"}
    decision = choice_map.get(decision_choice.lower(), "skip")

    result = ReviewDecision(
        pair_id=candidate["pair_id"],
        decision=decision,
        decided_at=datetime.now(timezone.utc),
        decided_by=decided_by,
        note=mitigation_note,
    )
    write_decision_log(log_path, result)

    if decision in ("approve", "conditional"):
        append_approved_to_event_map(
            event_map_path, candidate, mitigation_note=mitigation_note if decision == "conditional" else None
        )

    return result
