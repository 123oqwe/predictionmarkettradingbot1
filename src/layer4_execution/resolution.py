"""Market resolution probing + strategy-aware PnL realization.

Paper-mode PnL used to assume every trade wins `expected_profit_usd`. That's
correct for delta-neutral intra-market arb (you buy both YES and NO; either
outcome pays $1 per pair), but wrong for:

  - cross_market pairs where rule divergence can flip sign
  - resolution_convergence (directional, wins only if your chosen side wins)
  - any future strategy that isn't delta-neutral

This module queries each platform's public resolution endpoint and returns a
structured outcome. Strategy-aware `realize_pnl` then computes the correct
realized PnL given the actual winning side.

Offline / probe failure → return `Unresolved`. Caller keeps the position
`resolved=0` until next attempt; no inflated PnL gets written.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

import aiohttp
import structlog

from src.layer3_strategy.models import PaperPosition

logger = structlog.get_logger(__name__)


class ResolutionOutcome(str, enum.Enum):
    YES = "yes"
    NO = "no"
    VOID = "void"            # market canceled, no payout
    UNRESOLVED = "unresolved"  # probe couldn't determine


@dataclass(frozen=True)
class Resolution:
    outcome: ResolutionOutcome
    source: str  # human-readable note for logs


async def probe_polymarket_resolution(
    gamma_base: str, market_id: str, timeout: int = 5
) -> Resolution:
    """Query Polymarket Gamma API for a market's resolution.

    Gamma returns `closed: true` + `outcomePrices` like ["1","0"] (YES won) or
    ["0","1"] (NO won) once resolved. Offline / missing → UNRESOLVED.
    """
    url = f"{gamma_base.rstrip('/')}/markets/{market_id}"
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout)
        ) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return Resolution(ResolutionOutcome.UNRESOLVED, f"gamma_status={resp.status}")
                data = await resp.json()
    except Exception as e:
        logger.warning("polymarket_resolution_probe_failed", error=str(e), market_id=market_id)
        return Resolution(ResolutionOutcome.UNRESOLVED, f"probe_error: {e}")

    closed = bool(data.get("closed", False))
    if not closed:
        return Resolution(ResolutionOutcome.UNRESOLVED, "not_closed")

    # Gamma returns outcomePrices as a JSON-string of list or a direct list.
    raw = data.get("outcomePrices")
    if isinstance(raw, str):
        import json as _json

        try:
            raw = _json.loads(raw)
        except Exception:
            raw = None
    if not isinstance(raw, list) or len(raw) < 2:
        return Resolution(ResolutionOutcome.UNRESOLVED, "no_outcome_prices")

    try:
        yes_val = float(raw[0])
        no_val = float(raw[1])
    except (ValueError, TypeError):
        return Resolution(ResolutionOutcome.UNRESOLVED, "unparseable_prices")

    if yes_val >= 0.99 and no_val <= 0.01:
        return Resolution(ResolutionOutcome.YES, "gamma_outcome_yes")
    if no_val >= 0.99 and yes_val <= 0.01:
        return Resolution(ResolutionOutcome.NO, "gamma_outcome_no")
    # Both middling → void / canceled.
    return Resolution(ResolutionOutcome.VOID, f"ambiguous_outcome yes={yes_val} no={no_val}")


async def probe_kalshi_resolution(
    base_url: str, ticker: str, timeout: int = 5
) -> Resolution:
    """Kalshi `/markets/{ticker}` exposes `result` once settled.

    Values: "yes", "no", or the market is still open (result key missing/null).
    """
    url = f"{base_url.rstrip('/')}/markets/{ticker}"
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout)
        ) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return Resolution(ResolutionOutcome.UNRESOLVED, f"kalshi_status={resp.status}")
                data = await resp.json()
    except Exception as e:
        logger.warning("kalshi_resolution_probe_failed", error=str(e), ticker=ticker)
        return Resolution(ResolutionOutcome.UNRESOLVED, f"probe_error: {e}")

    market = data.get("market") if isinstance(data, dict) else None
    if not market:
        return Resolution(ResolutionOutcome.UNRESOLVED, "no_market_field")

    status = str(market.get("status") or "").lower()
    result = str(market.get("result") or "").lower()
    if status == "settled" or status == "closed":
        if result == "yes":
            return Resolution(ResolutionOutcome.YES, "kalshi_result_yes")
        if result == "no":
            return Resolution(ResolutionOutcome.NO, "kalshi_result_no")
        return Resolution(ResolutionOutcome.VOID, f"settled_result={result}")
    return Resolution(ResolutionOutcome.UNRESOLVED, f"status={status}")


def realize_pnl(
    position: PaperPosition,
    strategy: str,
    outcome_primary: Resolution,
    outcome_secondary: Optional[Resolution] = None,
) -> Optional[Decimal]:
    """Return realized PnL given the outcome(s). None = can't resolve yet.

    Strategy semantics:

      intra_market — we bought both YES and NO on one platform. Delta-neutral.
        Either outcome pays $1 per pair → realized = expected_profit_usd.
        Exception: VOID → lose capital_locked_usd.

      cross_market — we bought YES on platform A and NO on platform B (or
        inverse). If both platforms agree on the outcome, realized = expected.
        If they disagree (rule divergence), realized = -capital_locked_usd.

      resolution_convergence — directional; we only bought YES (or only NO).
        If the side we bought won: size*$1 - capital = expected. If the other
        side won: -capital_locked_usd. VOID: -capital_locked_usd.

    Unknown strategy → fall back to intra behavior but log a warning.
    """
    if outcome_primary.outcome == ResolutionOutcome.UNRESOLVED:
        return None

    capital = position.capital_locked_usd
    expected = position.expected_profit_usd

    if strategy == "intra_market":
        if outcome_primary.outcome == ResolutionOutcome.VOID:
            return -capital
        return expected

    if strategy == "cross_market":
        if outcome_secondary is None or outcome_secondary.outcome == ResolutionOutcome.UNRESOLVED:
            return None  # wait for both legs to settle
        if outcome_primary.outcome == ResolutionOutcome.VOID or outcome_secondary.outcome == ResolutionOutcome.VOID:
            return -capital
        if outcome_primary.outcome == outcome_secondary.outcome:
            return expected
        # Rule divergence — catastrophic loss.
        logger.warning(
            "cross_market_rule_divergence",
            primary=outcome_primary.outcome.value,
            secondary=outcome_secondary.outcome.value,
            position=position.client_order_id,
        )
        return -capital

    if strategy == "resolution_convergence":
        # yes_fill_price > 0 means we bought YES; else we bought NO.
        bought_yes = position.yes_fill_price > 0
        if outcome_primary.outcome == ResolutionOutcome.VOID:
            return -capital
        winner_matches = (bought_yes and outcome_primary.outcome == ResolutionOutcome.YES) or (
            not bought_yes and outcome_primary.outcome == ResolutionOutcome.NO
        )
        return expected if winner_matches else -capital

    logger.warning("realize_pnl_unknown_strategy", strategy=strategy)
    # Safe fallback: treat as intra behavior.
    if outcome_primary.outcome == ResolutionOutcome.VOID:
        return -capital
    return expected
