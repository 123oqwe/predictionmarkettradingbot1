"""Fill models for backtesting.

Three models, from phase-2.5-backtest.md:

  OPTIMISTIC  — top of book, no slippage. Use only for upper-bound sanity.
  REALISTIC   — top of book + N bps slippage (default 2 ticks). Default.
  PESSIMISTIC — next level down, or top-of-book + extra slippage when only one
                level exists. Size capped by what's available.

Important adjustment from the doc: doc's pessimistic says "full size only if
next level exists; otherwise skip". Skipping produces artificially LOW trade
counts in pessimistic mode, which looks like "no edge" when really you just
lost every trade to skipping. We instead apply a configurable extra slippage
(default 100 bps) on top-of-book when second level is absent. This keeps the
trade count honest while still being pessimistic on price.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from decimal import Decimal
from typing import Tuple

from src.layer3_strategy.models import OrderBookSide


class FillModelKind(str, enum.Enum):
    OPTIMISTIC = "optimistic"
    REALISTIC = "realistic"
    PESSIMISTIC = "pessimistic"


@dataclass(frozen=True)
class FillModelConfig:
    kind: FillModelKind
    realistic_slippage_bps: int = 20  # 2 ticks at 1¢ on a $0.50 price
    pessimistic_extra_slippage_bps: int = 100  # when single-level book falls back
    tick_size: Decimal = Decimal("0.01")


def fill_price(
    side: OrderBookSide,
    desired_contracts: Decimal,
    model: FillModelConfig,
) -> Tuple[Decimal, Decimal]:
    """Return (effective_fill_price, fillable_contracts) for a given model.

    All three models operate on the same input side (asks). The math produces
    conservative prices as `kind` moves from optimistic to pessimistic:

      optimistic  → size-weighted from top of book, no slippage
      realistic   → same, plus `realistic_slippage_bps` added to each contract's price
      pessimistic → drop the first level; if absent, add extra slippage to top-of-book
    """
    if desired_contracts <= 0 or not side.levels:
        return (Decimal(0), Decimal(0))

    if model.kind == FillModelKind.OPTIMISTIC:
        avg, filled = side.weighted_fill_price(desired_contracts)
        return (avg, filled)

    if model.kind == FillModelKind.REALISTIC:
        avg, filled = side.weighted_fill_price(desired_contracts)
        if filled == 0:
            return (Decimal(0), Decimal(0))
        slip = avg * Decimal(model.realistic_slippage_bps) / Decimal(10000)
        effective = avg + slip
        # Cap at 1.0 — a price > 1.0 can never be paid on a contract worth <= $1.
        if effective >= Decimal(1):
            return (Decimal(0), Decimal(0))
        return (effective, filled)

    # PESSIMISTIC
    if len(side.levels) >= 2:
        # Drop the first level to simulate "we didn't get top of book" and walk
        # from the second level onward. Available size is capped accordingly.
        remaining = side.levels[1:]
        total = sum((lv.size_contracts for lv in remaining), Decimal(0))
        if total < desired_contracts:
            desired_contracts = total
        acc_cost = Decimal(0)
        acc_size = Decimal(0)
        target = desired_contracts
        for lv in remaining:
            if target <= 0:
                break
            take = min(lv.size_contracts, target)
            acc_cost += take * lv.price
            acc_size += take
            target -= take
        if acc_size == 0:
            return (Decimal(0), Decimal(0))
        avg = acc_cost / acc_size
        return (avg, acc_size)
    # Only one level: use it + extra slippage to stay pessimistic.
    avg, filled = side.weighted_fill_price(desired_contracts)
    if filled == 0:
        return (Decimal(0), Decimal(0))
    slip = avg * Decimal(model.pessimistic_extra_slippage_bps) / Decimal(10000)
    effective = avg + slip
    if effective >= Decimal(1):
        return (Decimal(0), Decimal(0))
    return (effective, filled)
