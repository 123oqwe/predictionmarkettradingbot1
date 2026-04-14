"""Tiered safety rules for auto-discovered pairs.

Tier A — Phase 1 hand-verified pairs. Normal cross-market threshold (28%
         annualized), normal per-trade caps.
Tier B — Phase 4 auto-approved pairs that have completed a 48h paper-only
         cooling window with no issues. Live trading allowed but with
         stricter threshold (35%) and smaller per-trade cap (half of A).
Tier C — Freshly extracted auto-approved pairs. Paper-only for 48h. No
         live trading.

Rules live in code, not docs, so they can't silently drift.

Promotion:
  C → B: automatic after cooling_hours, no kill switch trips against the pair
  B → A: manual via a script; the doc says this should be "a few weeks of
         clean live history".
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Dict


class Tier(str, enum.Enum):
    A = "A"
    B = "B"
    C = "C"


@dataclass(frozen=True)
class TierConfig:
    annualized_threshold: Decimal
    per_trade_cap_multiplier: Decimal  # applied to base per-trade cap
    cooling_hours_for_promotion: int
    live_trading_allowed: bool


DEFAULT_TIERS: Dict[Tier, TierConfig] = {
    Tier.A: TierConfig(
        annualized_threshold=Decimal("0.28"),
        per_trade_cap_multiplier=Decimal("1.00"),
        cooling_hours_for_promotion=0,
        live_trading_allowed=True,
    ),
    Tier.B: TierConfig(
        annualized_threshold=Decimal("0.35"),
        per_trade_cap_multiplier=Decimal("0.50"),
        cooling_hours_for_promotion=0,  # B is promoted manually, not on a timer
        live_trading_allowed=True,
    ),
    Tier.C: TierConfig(
        annualized_threshold=Decimal("0.40"),  # still needs to be good to waste attention
        per_trade_cap_multiplier=Decimal("0"),  # no live trading
        cooling_hours_for_promotion=48,
        live_trading_allowed=False,
    ),
}


@dataclass
class PairTierState:
    pair_id: str
    tier: Tier
    entered_tier_at: datetime
    incidents: int = 0  # any kill switch trip or reconcile mismatch against this pair

    def hours_at_tier(self, now: datetime) -> float:
        return (now - self.entered_tier_at).total_seconds() / 3600.0

    def eligible_for_c_to_b(self, now: datetime) -> bool:
        if self.tier != Tier.C:
            return False
        if self.incidents > 0:
            return False
        cfg = DEFAULT_TIERS[Tier.C]
        return self.hours_at_tier(now) >= cfg.cooling_hours_for_promotion

    def promote(self, to: Tier, now: datetime) -> None:
        self.tier = to
        self.entered_tier_at = now
        self.incidents = 0

    def record_incident(self) -> None:
        self.incidents += 1


def effective_threshold(tier: Tier) -> Decimal:
    return DEFAULT_TIERS[tier].annualized_threshold


def effective_per_trade_cap(
    tier: Tier, base_cap_usd: Decimal
) -> Decimal:
    return base_cap_usd * DEFAULT_TIERS[tier].per_trade_cap_multiplier


def live_trading_allowed(tier: Tier) -> bool:
    return DEFAULT_TIERS[tier].live_trading_allowed
