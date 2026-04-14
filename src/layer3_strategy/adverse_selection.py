"""Adverse selection filters between detection and allocation.

Filters answer "why is this opportunity still here?" with skeptical defaults:

  - Age: opportunities visible for > N seconds are suspicious (faster bots
    rejected them; we should too).
  - News window: near a topic-relevant news event, one platform updates faster
    than the other — the "arb" you see is a stale price you're paying to take.
  - Young market: newly-listed markets have wide noisy spreads that evaporate
    once real participants arrive. Skip until they've stabilized.

Each filter accepts an Opportunity and external state (history, current time,
news config) and returns either accepted, or a structured rejection.

Layer 3 still pure: filters only read inputs, no I/O. The age filter requires
a `MarketHistory` passed in by the caller — typically the orchestrator
maintains a small in-memory rolling window.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional, Tuple

from src.layer3_strategy.models import Opportunity


# A "first seen" record per (pair_id or market_id, direction-ish-key).
# Keyed simply by Opportunity.event_id which is unique per pair / per intra-market.
@dataclass
class OpportunityHistory:
    """Tracks first-seen timestamps for an opportunity-fingerprint.

    Keys are derived from `_fingerprint(opp)` — a coarse identity that's stable
    across small price/size variations within the same alert window.
    """

    first_seen_by_fingerprint: Dict[str, datetime] = field(default_factory=dict)
    max_age_seconds: int = 600  # forget entries older than 10 min to bound memory

    def _fingerprint(self, opp: Opportunity) -> str:
        return f"{opp.strategy}|{opp.event_id}|{opp.market_id}"

    def observe(self, opp: Opportunity) -> datetime:
        """Record this observation; return the first-seen timestamp."""
        fp = self._fingerprint(opp)
        first = self.first_seen_by_fingerprint.get(fp)
        if first is None:
            self.first_seen_by_fingerprint[fp] = opp.detected_at
            return opp.detected_at
        return first

    def gc(self, now: datetime) -> None:
        """Drop entries older than max_age_seconds."""
        cutoff = now - timedelta(seconds=self.max_age_seconds)
        self.first_seen_by_fingerprint = {
            k: v for k, v in self.first_seen_by_fingerprint.items() if v >= cutoff
        }


@dataclass(frozen=True)
class NewsWindow:
    """A configured blackout window around news events on certain topic tags."""

    topic_tags: Tuple[str, ...]
    blackout_minutes_before: int
    blackout_minutes_after: int


@dataclass(frozen=True)
class NewsEvent:
    """A scheduled news event (e.g. CPI 2026-04-15 12:30 UTC). Keyed by topic tag."""

    topic_tag: str
    occurs_at: datetime


@dataclass(frozen=True)
class FilterDecision:
    accepted: bool
    reason: Optional[str] = None  # human-readable rejection reason

    @classmethod
    def accept(cls) -> "FilterDecision":
        return cls(accepted=True)

    @classmethod
    def reject(cls, reason: str) -> "FilterDecision":
        return cls(accepted=False, reason=reason)


def age_filter(
    opp: Opportunity,
    history: OpportunityHistory,
    threshold_seconds: int,
) -> FilterDecision:
    """Reject opportunities visible longer than threshold_seconds.

    NOTE on lookahead bias: if the history is empty (just started recording),
    every opportunity will look "young" because we haven't seen it before. The
    orchestrator should warm up the history for at least `threshold_seconds`
    of wall-clock before treating age decisions as meaningful. We add a guard
    here: history.observe always records `opp.detected_at` as first-seen on
    first encounter, so brand-new histories never falsely reject.
    """
    first_seen = history.observe(opp)
    age_seconds = (opp.detected_at - first_seen).total_seconds()
    if age_seconds > threshold_seconds:
        return FilterDecision.reject(f"too_old(age={age_seconds:.0f}s)")
    return FilterDecision.accept()


def news_window_filter(
    opp: Opportunity,
    topic_tags_for_opp: List[str],
    windows: List[NewsWindow],
    upcoming_events: List[NewsEvent],
    now: datetime,
) -> FilterDecision:
    """Reject if a news event for any matching topic_tag is within blackout window.

    `topic_tags_for_opp` is the list of tags attached to the opportunity's pair
    (cross-market) or market category (intra-market). Caller resolves it.
    """
    if not topic_tags_for_opp:
        return FilterDecision.accept()

    for ev in upcoming_events:
        if ev.topic_tag not in topic_tags_for_opp:
            continue
        for w in windows:
            if ev.topic_tag not in w.topic_tags:
                continue
            before = ev.occurs_at - timedelta(minutes=w.blackout_minutes_before)
            after = ev.occurs_at + timedelta(minutes=w.blackout_minutes_after)
            if before <= now <= after:
                return FilterDecision.reject(
                    f"news_window(tag={ev.topic_tag},event={ev.occurs_at.isoformat()})"
                )
    return FilterDecision.accept()


def young_market_filter(
    opp: Opportunity,
    market_listed_at: Optional[datetime],
    min_age_hours: int,
    now: datetime,
) -> FilterDecision:
    """Reject markets younger than min_age_hours.

    `market_listed_at` is None when we don't know the listing time; we conservatively
    accept in that case (rejecting on missing data would mute too many real opps).
    """
    if market_listed_at is None:
        return FilterDecision.accept()
    age = now - market_listed_at
    if age < timedelta(hours=min_age_hours):
        return FilterDecision.reject(
            f"young_market(age_h={age.total_seconds()/3600:.1f})"
        )
    return FilterDecision.accept()


@dataclass
class FilterStats:
    accepted: int = 0
    rejected: Dict[str, int] = field(default_factory=dict)

    def record(self, decision: FilterDecision) -> None:
        if decision.accepted:
            self.accepted += 1
        else:
            key = (decision.reason or "unknown").split("(")[0]
            self.rejected[key] = self.rejected.get(key, 0) + 1


def apply_filters(
    opportunities: List[Opportunity],
    *,
    history: OpportunityHistory,
    age_threshold_seconds: int,
    topic_tags_for: Callable[[Opportunity], List[str]],
    news_windows: List[NewsWindow],
    upcoming_news: List[NewsEvent],
    market_listed_at_for: Callable[[Opportunity], Optional[datetime]],
    min_market_age_hours: int,
    now: Optional[datetime] = None,
) -> Tuple[List[Opportunity], FilterStats]:
    """Run all filters; return accepted opportunities + per-reason stats."""
    if now is None:
        now = datetime.now(timezone.utc)
    accepted: List[Opportunity] = []
    stats = FilterStats()
    for opp in opportunities:
        for decision in (
            age_filter(opp, history, age_threshold_seconds),
            news_window_filter(opp, topic_tags_for(opp), news_windows, upcoming_news, now),
            young_market_filter(opp, market_listed_at_for(opp), min_market_age_hours, now),
        ):
            stats.record(decision)
            if not decision.accepted:
                break
        else:
            accepted.append(opp)
    history.gc(now)
    return accepted, stats
