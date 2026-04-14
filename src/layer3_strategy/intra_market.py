"""Intra-market detection (YES_ask + NO_ask < 1, after fees/gas/slippage).

This module is PURE. No I/O, no datetime.now(), no network. All inputs are passed in;
all time references come from the snapshot's fetched_at. This is what makes replay
deterministic and Layer 3 testable in isolation.

Key math references (see phase-0-mvp.md section 3):
  capital_at_risk = gross_cost + fee + gas
  expected_profit = (size * $1 payout) - capital_at_risk
  profit_pct = expected_profit / capital_at_risk
  annualized  = (1 + profit_pct)^(365/days) - 1

Gate: annualized >= config.min_annualized_return AND days_to_resolution >= min_days.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal, getcontext
from typing import List, Optional

from src.config import IntraMarketConfig
from src.layer3_strategy.models import Market, Opportunity

# 28 digits is Python's default. Explicit for documentation.
getcontext().prec = 28


@dataclass(frozen=True)
class StrategyContext:
    """Everything Layer 3 needs besides the snapshot. Kept pure — no I/O in any field."""

    config: IntraMarketConfig
    gas_cost_usd: Decimal
    config_hash: str
    git_hash: str


def _opportunity_id(market: Market, size: Decimal, snapshot_ts_iso: str) -> str:
    """Deterministic ID: same snapshot + same size → same id. Enables replay dedup."""
    payload = f"intra|{market.platform}|{market.market_id}|{size}|{snapshot_ts_iso}"
    return "opp_" + hashlib.sha256(payload.encode()).hexdigest()[:16]


def _compute_for_size(
    market: Market, size: Decimal, ctx: StrategyContext
) -> Optional[Opportunity]:
    """Compute profitability at a specific desired size. Returns None if below gate."""
    cfg = ctx.config

    # Step 1: size-weighted fill prices, capped by each side's fillable size.
    yes_price, yes_filled = market.yes_asks.weighted_fill_price(size)
    no_price, no_filled = market.no_asks.weighted_fill_price(size)
    fillable = min(yes_filled, no_filled)

    if fillable < cfg.min_trade_size_contracts:
        return None

    # If fillable dropped below requested, recompute fills at that smaller size
    # (because at a smaller size we may get a *better* blended price by not
    # walking as far up the book).
    if fillable < size:
        yes_price, _ = market.yes_asks.weighted_fill_price(fillable)
        no_price, _ = market.no_asks.weighted_fill_price(fillable)
    size = fillable

    # Step 2: costs.
    gross_cost = size * (yes_price + no_price)
    fee_cost = gross_cost * Decimal(market.fee_bps) / Decimal(10000)
    gas_cost = ctx.gas_cost_usd  # per-trade, not per-contract

    # Step 3: capital-at-risk = everything you pay out to open the position.
    capital_at_risk = gross_cost + fee_cost + gas_cost

    # Step 4: payout at resolution — each matched pair pays exactly $1.
    payout = size * Decimal("1")
    expected_profit = payout - capital_at_risk
    if expected_profit <= 0:
        return None

    # Step 5: time to resolution, from the snapshot's clock (NOT datetime.now()).
    # Using snapshot time is what makes replay deterministic.
    delta = market.resolution_date - market.fetched_at
    days_to_resolution = Decimal(delta.total_seconds()) / Decimal(86400)
    if days_to_resolution <= 0:
        return None  # already past resolution

    # Critical cap: reject trades resolving in < min_days. Annualizing a 1% profit
    # on a 1-day trade gives ~3700% annualized — mathematically valid but the
    # capacity to execute 365 such trades per year does not exist.
    if days_to_resolution < cfg.min_days_to_resolution:
        return None

    # Step 6: annualize on capital-at-risk.
    profit_pct = expected_profit / capital_at_risk
    # (1 + r)^(365/days) - 1. Decimal ** Decimal works on Python Decimal.
    exponent = Decimal(365) / days_to_resolution
    annualized = (Decimal(1) + profit_pct) ** exponent - Decimal(1)

    # Step 7: the gate.
    if annualized < cfg.min_annualized_return:
        return None

    return Opportunity(
        opportunity_id=_opportunity_id(market, size, market.fetched_at.isoformat()),
        strategy="intra_market",
        platform=market.platform,
        market_id=market.market_id,
        event_id=market.event_id,
        title=market.title,
        detected_at=market.fetched_at,
        size_contracts=size,
        yes_fill_price=yes_price,
        no_fill_price=no_price,
        gross_cost_usd=gross_cost,
        fee_cost_usd=fee_cost,
        gas_cost_usd=gas_cost,
        capital_at_risk_usd=capital_at_risk,
        days_to_resolution=days_to_resolution,
        expected_profit_usd=expected_profit,
        profit_pct_absolute=profit_pct,
        annualized_return=annualized,
        config_hash=ctx.config_hash,
        git_hash=ctx.git_hash,
    )


def compute_opportunity(market: Market, ctx: StrategyContext) -> Optional[Opportunity]:
    """Find the largest size at which `annualized_return >= threshold`.

    Search strategy: try max_trade_size_contracts; if it passes, take it. Otherwise
    binary-search downward until we find the edge. The returned Opportunity is the
    best-sized version of the trade (not necessarily the deepest, not necessarily the
    smallest — the sweet spot where threshold is just met).

    Why search: as size grows, fill price walks up the book, profit_pct drops,
    annualized drops. But smaller size has more annualized headroom. We want the
    largest size where the threshold still holds, because bigger = more absolute profit.
    """
    cfg = ctx.config

    # Skip dead markets cheaply.
    yes_liquidity = market.yes_asks.total_size()
    no_liquidity = market.no_asks.total_size()
    if yes_liquidity < cfg.min_trade_size_contracts:
        return None
    if no_liquidity < cfg.min_trade_size_contracts:
        return None

    # Skip markets the exchange has already resolved.
    if market.resolved or not market.active:
        return None

    # First try the max size. If it passes, done.
    best = _compute_for_size(market, cfg.max_trade_size_contracts, ctx)
    if best is not None:
        return best

    # Binary search for the largest passing size. Integer-contract granularity.
    lo = cfg.min_trade_size_contracts
    hi = min(cfg.max_trade_size_contracts, yes_liquidity, no_liquidity)
    if hi < lo:
        return None

    # Early out: even at min size it fails.
    candidate = _compute_for_size(market, lo, ctx)
    if candidate is None:
        return None
    best = candidate

    # Binary search upward for the largest size that still passes.
    # Granularity: integer contracts is plenty for Phase 0. We walk in whole contracts
    # since real exchanges don't support fractional contracts anyway.
    lo_i = int(lo)
    hi_i = int(hi)
    while lo_i + 1 < hi_i:
        mid_i = (lo_i + hi_i) // 2
        candidate = _compute_for_size(market, Decimal(mid_i), ctx)
        if candidate is not None:
            best = candidate
            lo_i = mid_i
        else:
            hi_i = mid_i

    # Try the high end exactly.
    candidate_hi = _compute_for_size(market, Decimal(hi_i), ctx)
    if candidate_hi is not None:
        best = candidate_hi

    return best


def find_opportunities(
    markets: List[Market], ctx: StrategyContext
) -> List[Opportunity]:
    """Scan a list of market snapshots and return all passing opportunities.

    Deterministic: output order matches input market order, and repeated calls on
    identical inputs produce identical output (Layer 3 has no hidden state).
    """
    results: List[Opportunity] = []
    for market in markets:
        opp = compute_opportunity(market, ctx)
        if opp is not None:
            results.append(opp)
    return results
