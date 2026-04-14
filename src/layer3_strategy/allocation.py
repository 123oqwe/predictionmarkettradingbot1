"""Greedy capital allocator with per-trade and per-event caps, including resize.

The resize logic is the non-trivial part: when a per-trade or per-event cap limits
the capital we can assign to an opportunity, we must recompute the math at the
smaller size. Naive "just take less" without recomputing can fund a trade whose
annualized return drops below threshold once size shrinks (walking back down the
order book gives a *better* blended price on each side, but the per-contract fee
and gas structure may now make smaller trades unprofitable).

Pure function: no I/O, no hidden state.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Dict, List

from src.config import AllocationConfig, IntraMarketConfig
from src.layer3_strategy.intra_market import StrategyContext, _compute_for_size
from src.layer3_strategy.models import Allocation, Market, Opportunity


def allocate_capital(
    opportunities: List[Opportunity],
    markets_by_id: Dict[str, Market],
    ctx: StrategyContext,
    alloc_cfg: AllocationConfig,
    reserved_capital_usd: Decimal = Decimal(0),
) -> List[Allocation]:
    """Greedy allocation by annualized return.

    Args:
      opportunities: detected opportunities (any order — we sort here)
      markets_by_id: map from market_id to the Market snapshot used to detect the
        opportunity. Required so we can re-run the math during resize.
      ctx: strategy context (config, gas cost, provenance).
      alloc_cfg: capital caps.
      reserved_capital_usd: capital already locked in open positions.

    Returns: allocations in the order they were committed (highest-annualized first).

    Why greedy-by-annualized: the naive "first come first served" puts capital into
    whatever opportunities arrived earliest, which are typically the long-tail slow
    ones the market already priced in. Greedy-by-annualized is the minimum correct
    approach; better allocations (Kelly, portfolio) can come later.
    """
    remaining_capital = alloc_cfg.total_capital_usd - reserved_capital_usd
    if remaining_capital <= 0:
        return []

    # Sort highest-annualized first. Ties broken by smaller capital-at-risk so we
    # pack more trades before hitting the global budget.
    ranked = sorted(
        opportunities,
        key=lambda o: (-o.annualized_return, o.capital_at_risk_usd),
    )

    allocations: List[Allocation] = []
    committed_per_event: Dict[str, Decimal] = defaultdict(lambda: Decimal(0))

    for opp in ranked:
        if remaining_capital <= 0:
            break

        # Per-trade cap.
        trade_cap = min(alloc_cfg.max_capital_per_trade_usd, remaining_capital)

        # Per-event cap.
        event_headroom = alloc_cfg.max_capital_per_event_usd - committed_per_event[
            opp.event_id
        ]
        if event_headroom <= 0:
            continue
        trade_cap = min(trade_cap, event_headroom)

        if trade_cap <= 0:
            continue

        # If capital-at-risk as detected fits under all caps, take it whole.
        if opp.capital_at_risk_usd <= trade_cap:
            allocations.append(
                Allocation(
                    opportunity=opp,
                    allocated_capital_usd=opp.capital_at_risk_usd,
                    allocated_size_contracts=opp.size_contracts,
                    allocation_reason="full",
                )
            )
            remaining_capital -= opp.capital_at_risk_usd
            committed_per_event[opp.event_id] += opp.capital_at_risk_usd
            continue

        # Need to resize down. Gas is a fixed per-trade cost, so capital does NOT
        # scale linearly with size — naive ratio scaling can overshoot the cap.
        # Approach: binary-search the largest integer size whose recomputed
        # capital_at_risk fits under trade_cap AND still passes the annualized gate.
        market = markets_by_id.get(opp.market_id)
        if market is None:
            continue

        lo_i = int(ctx.config.min_trade_size_contracts)
        hi_i = int(opp.size_contracts)
        if hi_i < lo_i:
            continue

        # Quick check: even min-size trade doesn't fit → skip.
        min_check = _compute_for_size(market, Decimal(lo_i), ctx)
        if min_check is None or min_check.capital_at_risk_usd > trade_cap:
            continue

        best_fit = min_check
        # Binary search for the largest size whose capital_at_risk <= trade_cap
        # AND whose gate still passes.
        while lo_i + 1 < hi_i:
            mid_i = (lo_i + hi_i) // 2
            candidate = _compute_for_size(market, Decimal(mid_i), ctx)
            if candidate is not None and candidate.capital_at_risk_usd <= trade_cap:
                best_fit = candidate
                lo_i = mid_i
            else:
                hi_i = mid_i

        # Try the upper bound exactly.
        final = _compute_for_size(market, Decimal(hi_i), ctx)
        if (
            final is not None
            and final.capital_at_risk_usd <= trade_cap
            and final.annualized_return >= ctx.config.min_annualized_return
        ):
            best_fit = final

        resized = best_fit

        reason = (
            "resized_by_event_cap"
            if event_headroom < alloc_cfg.max_capital_per_trade_usd
            else "resized_by_trade_cap"
        )
        allocations.append(
            Allocation(
                opportunity=resized,
                allocated_capital_usd=resized.capital_at_risk_usd,
                allocated_size_contracts=resized.size_contracts,
                allocation_reason=reason,
            )
        )
        remaining_capital -= resized.capital_at_risk_usd
        committed_per_event[opp.event_id] += resized.capital_at_risk_usd

    return allocations


def _resize_config(
    base: IntraMarketConfig, max_size: Decimal
) -> IntraMarketConfig:
    """Unused helper reserved for future per-call threshold overrides. Kept for clarity."""
    return IntraMarketConfig(
        min_annualized_return=base.min_annualized_return,
        min_days_to_resolution=base.min_days_to_resolution,
        min_trade_size_contracts=base.min_trade_size_contracts,
        max_trade_size_contracts=max_size,
        min_market_liquidity_usd=base.min_market_liquidity_usd,
        stale_snapshot_threshold_seconds=base.stale_snapshot_threshold_seconds,
    )
