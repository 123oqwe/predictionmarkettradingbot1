"""Partial-fill imbalance handling.

The doc warns: "Decide now, implement now, test now. Don't decide in the moment."
This is the "now".

Default policy (code path, not documentation):

  1. If leg_imbalance_contracts <= `max_leg_imbalance_contracts` (default 5),
     accept the paired position as-is. Minor imbalance gets absorbed into PnL.

  2. Otherwise, compute the delta (YES - NO). Submit a corrective order on
     whichever side is short, at a marketable price defined by the current
     best ask + `imbalance_retry_slippage_bps` (default 50 bps).

  3. Retry up to `max_retries` times (default 3). If still imbalanced after
     retries, trip the `position_mismatch` kill switch (the orchestrator reads
     this signal and halts further fills until manual intervention).

Why marketable limit instead of market order: market orders on thin books can
walk the ask an arbitrary distance. A limit at ask + 50bps is a well-defined
upper bound on how much the rebalance can cost.

Why not cancel the over-filled leg: you can't "unwind" a fill at the same price
you got it — the spread is against you. Adding to the underfilled side is
cheaper on average than trying to reverse.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from decimal import Decimal

import structlog

from src.layer4_execution.exchange import (
    ExchangeClient,
    OrderRequest,
    OrderResult,
    OrderSide,
    OrderStatus,
)
from src.layer4_execution.live import FillOutcome

logger = structlog.get_logger(__name__)


class ImbalanceResolution(str, enum.Enum):
    BALANCED = "balanced"  # within tolerance, no action
    RESOLVED_BY_RETRY = "resolved_by_retry"
    FAILED_TRIP_KILL_SWITCH = "failed_trip_kill_switch"


@dataclass(frozen=True)
class PartialFillConfig:
    max_leg_imbalance_contracts: Decimal = Decimal(5)
    imbalance_retry_slippage_bps: int = 50
    max_retries: int = 3


@dataclass
class PartialFillReport:
    resolution: ImbalanceResolution
    retries_used: int
    final_imbalance: Decimal
    retry_orders: list


async def resolve_imbalance(
    outcome: FillOutcome,
    client: ExchangeClient,
    cfg: PartialFillConfig,
) -> PartialFillReport:
    """Rebalance the two legs if their fill sizes diverged.

    Returns a report; caller is responsible for tripping kill switches if
    resolution is FAILED.
    """
    imbalance = outcome.leg_imbalance_contracts
    report = PartialFillReport(
        resolution=ImbalanceResolution.BALANCED,
        retries_used=0,
        final_imbalance=imbalance,
        retry_orders=[],
    )
    if imbalance <= cfg.max_leg_imbalance_contracts:
        return report

    yes_filled = outcome.yes_leg.result.filled_size
    no_filled = outcome.no_leg.result.filled_size
    short_side = "no" if yes_filled > no_filled else "yes"
    needed = abs(yes_filled - no_filled)

    base_price = (
        outcome.alloc.opportunity.no_fill_price
        if short_side == "no"
        else outcome.alloc.opportunity.yes_fill_price
    )
    slip = base_price * Decimal(cfg.imbalance_retry_slippage_bps) / Decimal(10000)
    retry_price = base_price + slip
    if retry_price >= Decimal(1):
        retry_price = Decimal("0.999")

    for attempt in range(1, cfg.max_retries + 1):
        report.retries_used = attempt
        logger.warning(
            "partial_fill_retry",
            attempt=attempt,
            short_side=short_side,
            needed=str(needed),
            retry_price=str(retry_price),
        )
        req = OrderRequest(
            client_order_id=f"rebalance_{outcome.alloc.opportunity.opportunity_id}_{short_side}_{attempt}",
            platform=outcome.yes_leg.result.client_order_id.split("_")[0],
            market_id=outcome.alloc.opportunity.market_id,
            side=OrderSide.BUY,
            token="NO" if short_side == "no" else "YES",
            limit_price=retry_price,
            size_contracts=needed,
            time_in_force="IOC",
        )
        res: OrderResult = await client.place_order(req)
        report.retry_orders.append(res)
        if res.status == OrderStatus.FILLED and res.filled_size >= needed:
            report.resolution = ImbalanceResolution.RESOLVED_BY_RETRY
            report.final_imbalance = Decimal(0)
            return report
        # Walk the price up for the next attempt.
        needed -= res.filled_size
        if needed <= 0:
            report.resolution = ImbalanceResolution.RESOLVED_BY_RETRY
            report.final_imbalance = Decimal(0)
            return report
        retry_price += slip
        if retry_price >= Decimal(1):
            break

    report.resolution = ImbalanceResolution.FAILED_TRIP_KILL_SWITCH
    report.final_imbalance = needed
    logger.error(
        "partial_fill_unresolved",
        opportunity_id=outcome.alloc.opportunity.opportunity_id,
        remaining_imbalance=str(needed),
    )
    return report
