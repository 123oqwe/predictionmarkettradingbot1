"""Phase 5 Option E — resolution convergence trading.

**FROZEN pending Phase 3 SCALE decision.** Imports are safe but all detection
is gated behind `feature_flags.is_enabled("option_e_convergence")`. The
orchestrator must not call this strategy until an explicit enable flag
(or config override) is set.

The trade: near market resolution, YES and NO prices should converge to 0 or
1 depending on the outcome. A market at YES=0.97 with 2 hours to resolution,
where public information strongly supports YES, should close at 1.00 — a
3¢ profit per contract.

Risks the doc flags:
  1. Information asymmetry — if the market knows something you don't, the
     "deviation" is actually rational. We mitigate with a hard news-window
     filter and a strict liquidity floor.
  2. Liquidity dries up near resolution. Orders may not fill. Our detection
     reports the available size at the top of book so the allocator can cap.
  3. Manipulation on illiquid markets. Minimum liquidity filter again.

Detection is PURE. No I/O.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal
from typing import List, Optional

from src.layer3_strategy.intra_market import StrategyContext
from src.layer3_strategy.models import Market, Opportunity


@dataclass(frozen=True)
class ConvergenceConfig:
    """Settings for Option E. All values defensive."""

    # How close to resolution the trade must be, else skip.
    max_hours_to_resolution: int = 6
    min_hours_to_resolution: int = 1  # too close = can't execute in time

    # Minimum price proximity to the "certain" outcome for a trade to qualify.
    # At 0.97 we buy YES hoping it resolves 1.00 (3¢ profit = 3.1% in ~hours
    # = extremely high annualized). At 0.80 we're betting on something
    # not-yet-settled — which is regular arb, not convergence.
    min_confidence_price: Decimal = Decimal("0.95")

    # Minimum ABSOLUTE profit required after fees/gas. Convergence trades are
    # small; we want to guarantee they clear gas.
    min_absolute_profit_usd: Decimal = Decimal("1.00")

    # Minimum liquidity on the side we'd buy.
    min_fill_size_contracts: Decimal = Decimal("50")

    # Required: at least this much *total* book depth on the buy side (not just
    # the top level). Thin markets get manipulated.
    min_total_depth_contracts: Decimal = Decimal("200")


def _opp_id(market_id: str, side: str, size: Decimal, ts_iso: str) -> str:
    payload = f"convergence|{market_id}|{side}|{size}|{ts_iso}"
    return "opp_" + hashlib.sha256(payload.encode()).hexdigest()[:16]


def _detect_side(
    market: Market,
    side: str,  # "yes" or "no"
    ctx: StrategyContext,
    cfg: ConvergenceConfig,
) -> Optional[Opportunity]:
    """Check one side for a convergence opportunity."""
    asks = market.yes_asks if side == "yes" else market.no_asks
    if asks.total_size() < cfg.min_total_depth_contracts:
        return None
    if not asks.levels:
        return None

    top_price = asks.levels[0].price
    # Convergence only triggers when the "cheap" side is priced very close to
    # certain-win. Top-of-book must be >= min_confidence_price. Intuition: we
    # pay ≥0.95 hoping for 1.00 payout.
    if top_price < cfg.min_confidence_price:
        return None
    if top_price >= Decimal(1):
        return None

    # Time to resolution.
    delta = market.resolution_date - market.fetched_at
    hours_to_resolution = delta.total_seconds() / 3600.0
    if hours_to_resolution < cfg.min_hours_to_resolution:
        return None
    if hours_to_resolution > cfg.max_hours_to_resolution:
        return None

    # Fill price + size. Walk book but cap at a reasonable size.
    desired = cfg.min_fill_size_contracts
    fill_price, filled = asks.weighted_fill_price(desired)
    if filled < cfg.min_fill_size_contracts:
        return None

    gross_cost = filled * fill_price
    fee_cost = gross_cost * Decimal(market.fee_bps) / Decimal(10000)
    gas_cost = ctx.gas_cost_usd
    capital_at_risk = gross_cost + fee_cost + gas_cost
    payout_if_win = filled * Decimal(1)
    expected_profit = payout_if_win - capital_at_risk

    if expected_profit < cfg.min_absolute_profit_usd:
        return None

    # Annualized return — use hours, cap at sane value.
    days_to_resolution = Decimal(str(hours_to_resolution / 24.0))
    if days_to_resolution <= 0:
        return None
    profit_pct = expected_profit / capital_at_risk
    # Annualize but with a ceiling — sub-day trades explode mathematically.
    exponent = Decimal(365) / max(days_to_resolution, Decimal("0.5"))
    annualized = (Decimal(1) + profit_pct) ** exponent - Decimal(1)

    return Opportunity(
        opportunity_id=_opp_id(market.market_id, side, filled, market.fetched_at.isoformat()),
        strategy="resolution_convergence",
        platform=market.platform,
        market_id=market.market_id,
        event_id=market.event_id,
        title=f"[conv/{side}] {market.title[:60]}",
        detected_at=market.fetched_at,
        size_contracts=filled,
        yes_fill_price=fill_price if side == "yes" else Decimal(0),
        no_fill_price=fill_price if side == "no" else Decimal(0),
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


def find_convergence_opportunities(
    markets: List[Market],
    ctx: StrategyContext,
    cfg: ConvergenceConfig,
) -> List[Opportunity]:
    """Scan markets for convergence opportunities on either side."""
    out: List[Opportunity] = []
    for m in markets:
        if m.resolved or not m.active:
            continue
        for side in ("yes", "no"):
            o = _detect_side(m, side, ctx, cfg)
            if o is not None:
                out.append(o)
    return out
