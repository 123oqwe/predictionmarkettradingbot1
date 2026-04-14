"""Data models shared across layers.

Key discipline:
- Every monetary or size quantity is Decimal, never float.
- Pydantic models validate types at construction time.
- OrderBookSide.weighted_fill_price is where the "size-weighted fill" math lives.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator


class OrderBookLevel(BaseModel):
    """One price level in an order book. Decimal everywhere."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    price: Decimal
    size_contracts: Decimal

    @field_validator("price", "size_contracts", mode="before")
    @classmethod
    def _coerce_decimal(cls, v):
        if isinstance(v, float):
            raise TypeError(
                "float value passed where Decimal is required. "
                "Always construct with Decimal(str(...)) to avoid float contamination."
            )
        if isinstance(v, Decimal):
            return v
        return Decimal(str(v))


class OrderBookSide(BaseModel):
    """A single side of an order book, sorted best-first.

    For ASKS: best-first = lowest price first.
    For BIDS: best-first = highest price first.
    Callers must sort before constructing.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    levels: List[OrderBookLevel] = Field(default_factory=list)

    def total_size(self) -> Decimal:
        return sum((lv.size_contracts for lv in self.levels), Decimal(0))

    def weighted_fill_price(
        self, desired_contracts: Decimal
    ) -> Tuple[Decimal, Decimal]:
        """Compute size-weighted average fill price and actually-fillable contracts.

        Walks the book from best to worst, accumulating cost and filled size until
        either the desired amount is reached or the book is exhausted.

        Returns:
          (weighted_avg_price, actually_fillable_contracts)

          If no liquidity: (Decimal(0), Decimal(0)).
          If partial fill: (weighted_avg of what was filled, filled_size).

        Why this matters: detecting `yes_ask = 0.45` with 1000 size looks like you can
        buy 1000 at 0.45, but that size is usually split across levels. True fill price
        is higher. Failing to model this turns "arbitrage" into a loss.
        """
        if desired_contracts <= 0:
            return (Decimal(0), Decimal(0))

        filled = Decimal(0)
        total_cost = Decimal(0)
        remaining = desired_contracts

        for level in self.levels:
            if remaining <= 0:
                break
            take = min(level.size_contracts, remaining)
            total_cost += take * level.price
            filled += take
            remaining -= take

        if filled == 0:
            return (Decimal(0), Decimal(0))

        avg_price = total_cost / filled
        return (avg_price, filled)


class Market(BaseModel):
    """A snapshot of one market at a moment in time.

    Fetched by Layer 1; consumed by Layer 3. Layer 3 must treat this as immutable.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    platform: str
    market_id: str
    event_id: str  # groups markets belonging to the same underlying event
    title: str
    yes_bids: OrderBookSide
    yes_asks: OrderBookSide
    no_bids: OrderBookSide
    no_asks: OrderBookSide
    fee_bps: int
    resolution_date: datetime
    resolution_source: str
    fetched_at: datetime
    active: bool = True
    resolved: bool = False
    # Raw liquidity hint in USD, from the exchange. Used for min-liquidity filter.
    liquidity_usd: Decimal = Decimal(0)


class Opportunity(BaseModel):
    """A detected intra-market YES + NO arbitrage.

    All values are after-fees, after-gas, size-weighted. The annualized_return is
    the gate: it must be >= threshold, computed on capital-at-risk.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    opportunity_id: str
    strategy: str  # "intra_market" for Phase 0; "cross_market" in Phase 1
    platform: str
    market_id: str
    event_id: str
    title: str
    detected_at: datetime  # MUST come from snapshot, never datetime.now()

    size_contracts: Decimal
    yes_fill_price: Decimal  # size-weighted
    no_fill_price: Decimal

    gross_cost_usd: Decimal  # size * (yes_fill + no_fill)
    fee_cost_usd: Decimal
    gas_cost_usd: Decimal

    # Capital locked until resolution. This is what matters for annualization.
    # It equals what you actually pay out: gross_cost + fees + gas.
    capital_at_risk_usd: Decimal

    days_to_resolution: Decimal
    expected_profit_usd: Decimal  # payout (size * $1) minus capital_at_risk
    profit_pct_absolute: Decimal  # profit / capital_at_risk
    annualized_return: Decimal  # thresholds compare against THIS

    # Provenance tags (attached at detection time).
    config_hash: str
    git_hash: str

    @field_validator(
        "size_contracts",
        "yes_fill_price",
        "no_fill_price",
        "gross_cost_usd",
        "fee_cost_usd",
        "gas_cost_usd",
        "capital_at_risk_usd",
        "days_to_resolution",
        "expected_profit_usd",
        "profit_pct_absolute",
        "annualized_return",
        mode="before",
    )
    @classmethod
    def _forbid_float(cls, v):
        if isinstance(v, float):
            raise TypeError(
                "float value passed where Decimal is required in Opportunity."
            )
        if isinstance(v, Decimal):
            return v
        return Decimal(str(v))


class Allocation(BaseModel):
    """A capital allocation decision for a detected opportunity."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    opportunity: Opportunity
    allocated_capital_usd: Decimal
    allocated_size_contracts: Decimal
    allocation_reason: str  # "full" | "resized_by_trade_cap" | "resized_by_event_cap"

    @field_validator("allocated_capital_usd", "allocated_size_contracts", mode="before")
    @classmethod
    def _forbid_float(cls, v):
        if isinstance(v, float):
            raise TypeError("float not allowed in Allocation")
        if isinstance(v, Decimal):
            return v
        return Decimal(str(v))


class PaperPosition(BaseModel):
    """A filled paper position awaiting resolution."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    client_order_id: str  # idempotency key
    opportunity_id: str
    platform: str
    market_id: str
    event_id: str
    size_contracts: Decimal
    yes_fill_price: Decimal
    no_fill_price: Decimal
    capital_locked_usd: Decimal
    expected_profit_usd: Decimal
    opened_at: datetime
    resolution_date: datetime
    resolved: bool = False
    realized_pnl_usd: Optional[Decimal] = None
    resolved_at: Optional[datetime] = None
