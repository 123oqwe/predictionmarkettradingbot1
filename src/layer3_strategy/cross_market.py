"""Cross-market arbitrage detection.

For each enabled pair (Polymarket market A, Kalshi market B), check both
directions of the arb:
  Direction 1: buy YES on A + buy NO on B
  Direction 2: buy NO on A + buy YES on B

If either direction has cost-of-pair < $1 after fees/gas/slippage AND clears
the cross-market annualized threshold, we have a candidate.

Threshold: per-pair override > config default.

PURE function. No I/O.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, List, Optional

from src.config import IntraMarketConfig
from src.layer1_data_recording.kalshi_fetcher import quantize_to_kalshi_tick
from src.layer3_strategy.intra_market import StrategyContext
from src.layer3_strategy.models import Market, Opportunity, OrderBookSide
from src.matching.event_map import Pair


@dataclass(frozen=True)
class CrossMarketContext:
    """Cross-market settings: thresholds + min size + provenance.

    Built on top of the intra-market context so we share size/days settings.
    """

    intra: StrategyContext
    cross_min_annualized_return: Decimal
    polymarket_gas_usd: Decimal
    kalshi_gas_usd: Decimal  # typically 0; Kalshi has no gas concept
    config_hash: str
    git_hash: str


def _opp_id(pair_id: str, direction: str, size: Decimal, ts_iso: str) -> str:
    payload = f"cross|{pair_id}|{direction}|{size}|{ts_iso}"
    return "opp_" + hashlib.sha256(payload.encode()).hexdigest()[:16]


def _quantize_for_platform(price: Decimal, platform: str) -> Decimal:
    """Round price to the smallest tick the platform supports.

    Kalshi: 1 cent. Polymarket: finer (we don't quantize).
    """
    if platform == "kalshi":
        return quantize_to_kalshi_tick(price)
    return price


def _compute_cross_for_size(
    *,
    leg_a_market: Market,
    leg_a_side: OrderBookSide,
    leg_b_market: Market,
    leg_b_side: OrderBookSide,
    size: Decimal,
    ctx: CrossMarketContext,
    intra_cfg: IntraMarketConfig,
    threshold: Decimal,
    pair: Pair,
    direction_label: str,
) -> Optional[Opportunity]:
    """Compute one direction's profitability at a specific size."""
    a_price, a_filled = leg_a_side.weighted_fill_price(size)
    b_price, b_filled = leg_b_side.weighted_fill_price(size)
    fillable = min(a_filled, b_filled)
    if fillable < intra_cfg.min_trade_size_contracts:
        return None

    # Recompute fills at the smaller fillable size.
    if fillable < size:
        a_price, _ = leg_a_side.weighted_fill_price(fillable)
        b_price, _ = leg_b_side.weighted_fill_price(fillable)
    size = fillable

    # Apply platform tick rounding (Kalshi 1¢ rounds asks UP — pessimistic).
    a_price_eff = _quantize_for_platform(a_price, leg_a_market.platform)
    b_price_eff = _quantize_for_platform(b_price, leg_b_market.platform)
    # If quantization made price unrealistic (>=1), reject.
    if a_price_eff >= Decimal(1) or b_price_eff >= Decimal(1):
        return None

    gross_cost = size * (a_price_eff + b_price_eff)
    fee_a = gross_cost * Decimal(leg_a_market.fee_bps) / Decimal(20000)  # half on each leg
    fee_b = gross_cost * Decimal(leg_b_market.fee_bps) / Decimal(20000)
    fee_cost = fee_a + fee_b

    gas_cost = (
        ctx.polymarket_gas_usd if leg_a_market.platform == "polymarket" else Decimal(0)
    ) + (
        ctx.polymarket_gas_usd if leg_b_market.platform == "polymarket" else Decimal(0)
    )

    capital_at_risk = gross_cost + fee_cost + gas_cost
    payout = size * Decimal(1)
    expected_profit = payout - capital_at_risk
    if expected_profit <= 0:
        return None

    # Use the earlier of the two resolutions to be conservative.
    earlier_resolution = min(leg_a_market.resolution_date, leg_b_market.resolution_date)
    earlier_fetched = max(leg_a_market.fetched_at, leg_b_market.fetched_at)
    delta = earlier_resolution - earlier_fetched
    days_to_resolution = Decimal(delta.total_seconds()) / Decimal(86400)
    if days_to_resolution <= 0:
        return None
    if days_to_resolution < intra_cfg.min_days_to_resolution:
        return None

    profit_pct = expected_profit / capital_at_risk
    exponent = Decimal(365) / days_to_resolution
    annualized = (Decimal(1) + profit_pct) ** exponent - Decimal(1)
    if annualized < threshold:
        return None

    # The opportunity is reported under leg_a's market_id with title combining both.
    title = f"[{pair.pair_id}/{direction_label}] {leg_a_market.title[:35]} ↔ {leg_b_market.title[:35]}"

    return Opportunity(
        opportunity_id=_opp_id(pair.pair_id, direction_label, size, earlier_fetched.isoformat()),
        strategy="cross_market",
        platform=f"{leg_a_market.platform}+{leg_b_market.platform}",
        market_id=f"{leg_a_market.market_id}|{leg_b_market.market_id}",
        event_id=pair.pair_id,
        title=title,
        detected_at=earlier_fetched,
        size_contracts=size,
        yes_fill_price=a_price_eff,  # leg-a price under naming convention
        no_fill_price=b_price_eff,   # leg-b price
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


def _detect_one_direction(
    *,
    leg_a_market: Market,
    leg_a_side: OrderBookSide,
    leg_b_market: Market,
    leg_b_side: OrderBookSide,
    ctx: CrossMarketContext,
    intra_cfg: IntraMarketConfig,
    threshold: Decimal,
    pair: Pair,
    direction_label: str,
) -> Optional[Opportunity]:
    """Find largest size where threshold holds for one direction. Same shape as
    intra_market.compute_opportunity's binary search."""
    if leg_a_side.total_size() < intra_cfg.min_trade_size_contracts:
        return None
    if leg_b_side.total_size() < intra_cfg.min_trade_size_contracts:
        return None

    # Quick attempt at max size.
    best = _compute_cross_for_size(
        leg_a_market=leg_a_market,
        leg_a_side=leg_a_side,
        leg_b_market=leg_b_market,
        leg_b_side=leg_b_side,
        size=intra_cfg.max_trade_size_contracts,
        ctx=ctx,
        intra_cfg=intra_cfg,
        threshold=threshold,
        pair=pair,
        direction_label=direction_label,
    )
    if best is not None:
        return best

    lo = int(intra_cfg.min_trade_size_contracts)
    hi = int(min(
        intra_cfg.max_trade_size_contracts,
        leg_a_side.total_size(),
        leg_b_side.total_size(),
    ))
    if hi < lo:
        return None

    base = _compute_cross_for_size(
        leg_a_market=leg_a_market,
        leg_a_side=leg_a_side,
        leg_b_market=leg_b_market,
        leg_b_side=leg_b_side,
        size=Decimal(lo),
        ctx=ctx,
        intra_cfg=intra_cfg,
        threshold=threshold,
        pair=pair,
        direction_label=direction_label,
    )
    if base is None:
        return None
    best = base
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        cand = _compute_cross_for_size(
            leg_a_market=leg_a_market,
            leg_a_side=leg_a_side,
            leg_b_market=leg_b_market,
            leg_b_side=leg_b_side,
            size=Decimal(mid),
            ctx=ctx,
            intra_cfg=intra_cfg,
            threshold=threshold,
            pair=pair,
            direction_label=direction_label,
        )
        if cand is not None:
            best = cand
            lo = mid
        else:
            hi = mid
    return best


def detect_cross_pair(
    pair: Pair,
    poly_market: Optional[Market],
    kalshi_market: Optional[Market],
    ctx: CrossMarketContext,
    intra_cfg: IntraMarketConfig,
    default_threshold: Decimal,
) -> List[Opportunity]:
    """Return opportunities (0, 1, or 2) for a single pair.

    Skips if either market is missing or pair is disabled.
    """
    if not pair.trading_enabled:
        return []
    if poly_market is None or kalshi_market is None:
        return []
    if poly_market.resolved or kalshi_market.resolved:
        return []
    if not poly_market.active or not kalshi_market.active:
        return []

    threshold = pair.min_annualized_return_override or default_threshold

    out: List[Opportunity] = []
    # Direction 1: buy YES on Polymarket + buy NO on Kalshi
    d1 = _detect_one_direction(
        leg_a_market=poly_market,
        leg_a_side=poly_market.yes_asks,
        leg_b_market=kalshi_market,
        leg_b_side=kalshi_market.no_asks,
        ctx=ctx,
        intra_cfg=intra_cfg,
        threshold=threshold,
        pair=pair,
        direction_label="POLY_YES+KAL_NO",
    )
    if d1 is not None:
        out.append(d1)

    # Direction 2: buy NO on Polymarket + buy YES on Kalshi
    d2 = _detect_one_direction(
        leg_a_market=poly_market,
        leg_a_side=poly_market.no_asks,
        leg_b_market=kalshi_market,
        leg_b_side=kalshi_market.yes_asks,
        ctx=ctx,
        intra_cfg=intra_cfg,
        threshold=threshold,
        pair=pair,
        direction_label="POLY_NO+KAL_YES",
    )
    if d2 is not None:
        out.append(d2)

    return out


def find_cross_opportunities(
    pairs: List[Pair],
    polymarket_by_id: Dict[str, Market],
    kalshi_by_ticker: Dict[str, Market],
    ctx: CrossMarketContext,
    intra_cfg: IntraMarketConfig,
    default_threshold: Decimal,
) -> List[Opportunity]:
    """Scan all enabled pairs for cross-market opportunities.

    Output is deterministic: pair iteration order matches input list.
    """
    results: List[Opportunity] = []
    for pair in pairs:
        poly_m = polymarket_by_id.get(pair.polymarket_market_id)
        kal_m = kalshi_by_ticker.get(pair.kalshi_market_ticker)
        results.extend(
            detect_cross_pair(pair, poly_m, kal_m, ctx, intra_cfg, default_threshold)
        )
    return results
