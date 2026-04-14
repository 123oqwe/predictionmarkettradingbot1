"""Live execution module. Phase 3.

For each allocated opportunity we must place TWO orders (one per leg). The
critical reliability concerns:

  1. Idempotency: client_order_ids per leg are deterministic. Retries are safe.
  2. Partial fills: if leg A fills 100 but leg B fills 63, we have unmatched
     directional risk. The partial_fill module decides how to rebalance.
  3. Order timing: for IOC orders we don't wait; we submit both in parallel
     and reconcile after.

This module is deliberately small. It sequences the two legs, interprets the
results, and hands off to partial_fill for imbalance handling.

Nothing in this module ever touches real exchange HTTP — that job belongs to
the concrete ExchangeClient implementations (e.g., PolymarketLiveClient)
once the operator writes them. The module works with any ExchangeClient.
"""
from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from decimal import Decimal

import structlog

from src.layer3_strategy.models import Allocation
from src.layer4_execution.exchange import (
    ExchangeClient,
    OrderRequest,
    OrderResult,
    OrderSide,
    OrderStatus,
)

logger = structlog.get_logger(__name__)


def _client_order_id(alloc: Allocation, leg: str) -> str:
    """Deterministic id per leg. Separate ids so each leg is independently idempotent."""
    opp = alloc.opportunity
    payload = (
        f"live|{leg}|{opp.opportunity_id}|{opp.detected_at.isoformat()}"
        f"|{opp.market_id}|{alloc.allocated_size_contracts}"
    )
    return f"co_{leg}_" + hashlib.sha256(payload.encode()).hexdigest()[:14]


@dataclass
class LegOutcome:
    leg: str  # "yes" or "no"
    result: OrderResult


@dataclass
class FillOutcome:
    """What the live executor produced for a single allocation."""

    alloc: Allocation
    yes_leg: LegOutcome
    no_leg: LegOutcome

    @property
    def both_filled(self) -> bool:
        return (
            self.yes_leg.result.status == OrderStatus.FILLED
            and self.no_leg.result.status == OrderStatus.FILLED
        )

    @property
    def leg_imbalance_contracts(self) -> Decimal:
        return abs(self.yes_leg.result.filled_size - self.no_leg.result.filled_size)

    @property
    def min_filled(self) -> Decimal:
        return min(self.yes_leg.result.filled_size, self.no_leg.result.filled_size)


class LiveExecutor:
    """Places two-leg orders via the platform's ExchangeClient."""

    def __init__(self, client: ExchangeClient, *, platform: str, time_in_force: str = "IOC"):
        self.client = client
        self.platform = platform
        self.time_in_force = time_in_force

    async def execute(self, alloc: Allocation) -> FillOutcome:
        opp = alloc.opportunity
        yes_req = OrderRequest(
            client_order_id=_client_order_id(alloc, "yes"),
            platform=self.platform,
            market_id=opp.market_id,
            side=OrderSide.BUY,
            token="YES",
            limit_price=opp.yes_fill_price,
            size_contracts=alloc.allocated_size_contracts,
            time_in_force=self.time_in_force,
        )
        no_req = OrderRequest(
            client_order_id=_client_order_id(alloc, "no"),
            platform=self.platform,
            market_id=opp.market_id,
            side=OrderSide.BUY,
            token="NO",
            limit_price=opp.no_fill_price,
            size_contracts=alloc.allocated_size_contracts,
            time_in_force=self.time_in_force,
        )

        # Submit both legs in parallel. IOC means the exchange either fills
        # immediately or cancels — no resting orders.
        yes_res, no_res = await asyncio.gather(
            self.client.place_order(yes_req),
            self.client.place_order(no_req),
        )

        outcome = FillOutcome(
            alloc=alloc,
            yes_leg=LegOutcome(leg="yes", result=yes_res),
            no_leg=LegOutcome(leg="no", result=no_res),
        )
        logger.info(
            "live_execute",
            opportunity_id=opp.opportunity_id,
            yes_status=yes_res.status.value,
            yes_filled=str(yes_res.filled_size),
            no_status=no_res.status.value,
            no_filled=str(no_res.filled_size),
            imbalance=str(outcome.leg_imbalance_contracts),
        )
        return outcome
