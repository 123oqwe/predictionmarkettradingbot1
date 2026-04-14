"""Paper execution layer.

Accepts allocations, converts each to a PaperPosition, writes to SQLite. Since
there's no real exchange, "fill" price is the size-weighted price the strategy
detected — the same number it would have seen live, minus any latency slippage
(which we model pessimistically in later phases; Phase 0 is a flat paper fill).

Idempotency: client_order_id is deterministic per opportunity, so retrying the
same allocation never creates a duplicate position. This is the Phase 2
discipline pulled into Phase 0 — the cost is low and the gain is safety.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Awaitable, Callable, List, Optional

import structlog

from src.layer3_strategy.models import Allocation, PaperPosition
from src.layer4_execution.resolution import (
    Resolution,
    ResolutionOutcome,
    realize_pnl,
)
from src.storage import state_db

logger = structlog.get_logger(__name__)


# Type alias: (platform, market_id) -> Resolution
ResolutionFn = Callable[[str, str], Awaitable[Resolution]]


def client_order_id(alloc: Allocation) -> str:
    """Deterministic ID from opportunity + size.

    Same opp + same allocated size → same id. Retries are safe.
    """
    opp = alloc.opportunity
    payload = (
        f"{opp.opportunity_id}|{opp.detected_at.isoformat()}|{opp.market_id}"
        f"|{alloc.allocated_size_contracts}|paper"
    )
    return "co_" + hashlib.sha256(payload.encode()).hexdigest()[:16]


class PaperExecutor:
    """Fills allocations at the detected prices; tracks positions until resolution."""

    def __init__(self, conn, provenance_json: str):
        self.conn = conn
        self.provenance_json = provenance_json

    def fill(self, alloc: Allocation) -> PaperPosition:
        opp = alloc.opportunity
        coid = client_order_id(alloc)

        pos = PaperPosition(
            client_order_id=coid,
            opportunity_id=opp.opportunity_id,
            platform=opp.platform,
            market_id=opp.market_id,
            event_id=opp.event_id,
            size_contracts=alloc.allocated_size_contracts,
            yes_fill_price=opp.yes_fill_price,
            no_fill_price=opp.no_fill_price,
            capital_locked_usd=alloc.allocated_capital_usd,
            expected_profit_usd=opp.expected_profit_usd,
            opened_at=datetime.now(timezone.utc),
            resolution_date=opp.detected_at
            + (opp.detected_at - opp.detected_at),  # placeholder — we need actual resolution
            resolved=False,
            realized_pnl_usd=None,
            resolved_at=None,
        )
        # The Market snapshot's resolution_date is what we actually want; callers
        # must supply it. In the orchestrator we reconstruct it from the market
        # dict. For now, surface the risk: if the caller passes an Allocation
        # whose opportunity.detected_at >= resolution, the whole trade is wrong.
        # See fill_with_resolution below.
        raise NotImplementedError("Use fill_with_resolution — requires resolution_date from the snapshot.")

    def fill_with_resolution(
        self, alloc: Allocation, resolution_date: datetime
    ) -> PaperPosition:
        """Create and persist a PaperPosition for this allocation."""
        opp = alloc.opportunity
        coid = client_order_id(alloc)

        pos = PaperPosition(
            client_order_id=coid,
            opportunity_id=opp.opportunity_id,
            platform=opp.platform,
            market_id=opp.market_id,
            event_id=opp.event_id,
            size_contracts=alloc.allocated_size_contracts,
            yes_fill_price=opp.yes_fill_price,
            no_fill_price=opp.no_fill_price,
            capital_locked_usd=alloc.allocated_capital_usd,
            expected_profit_usd=opp.expected_profit_usd,
            opened_at=datetime.now(timezone.utc),
            resolution_date=resolution_date,
            resolved=False,
        )
        state_db.write_opportunity(self.conn, opp, self.provenance_json)
        state_db.write_paper_trade(self.conn, pos, self.provenance_json)
        logger.info(
            "paper_fill",
            client_order_id=coid,
            opportunity_id=opp.opportunity_id,
            size=str(alloc.allocated_size_contracts),
            capital=str(alloc.allocated_capital_usd),
            annualized_return=str(opp.annualized_return),
        )
        return pos

    def resolve_due_positions(self, now: Optional[datetime] = None) -> List[PaperPosition]:
        """Mark positions whose resolution_date has passed — synchronous fallback.

        DEPRECATED path: only used when no `ResolutionFn` is available
        (e.g., purely in-process tests). It writes `expected_profit_usd` as
        realized PnL, which is correct for delta-neutral intra_market but
        WRONG for cross_market and resolution_convergence. Prefer
        `resolve_due_positions_with_probes()` in production.
        """
        if now is None:
            now = datetime.now(timezone.utc)
        resolved_now: List[PaperPosition] = []
        for pos in state_db.due_for_resolution(self.conn, now.isoformat()):
            # Still conservative for intra_market (delta-neutral). For other
            # strategies we keep the position UNRESOLVED until async probe
            # paths run. Check the originating opportunity's strategy.
            strategy = self._lookup_strategy(pos.opportunity_id)
            if strategy != "intra_market":
                logger.info(
                    "paper_resolve_deferred",
                    reason="non_intra_strategy_needs_probe",
                    strategy=strategy,
                    client_order_id=pos.client_order_id,
                )
                continue
            realized = pos.expected_profit_usd
            state_db.mark_resolved(
                self.conn, pos.client_order_id, realized, now.isoformat()
            )
            resolved_now.append(pos)
            logger.info(
                "paper_position_resolved",
                client_order_id=pos.client_order_id,
                realized_pnl=str(realized),
                path="sync_delta_neutral",
            )
        return resolved_now

    def _lookup_strategy(self, opportunity_id: str) -> str:
        row = self.conn.execute(
            "SELECT strategy FROM opportunities WHERE opportunity_id = ?",
            (opportunity_id,),
        ).fetchone()
        return str(row["strategy"]) if row else "intra_market"

    async def resolve_due_positions_with_probes(
        self,
        *,
        resolve_fn: ResolutionFn,
        now: Optional[datetime] = None,
    ) -> List[PaperPosition]:
        """Async path: actually query exchange resolution endpoints.

        For each position whose resolution_date has passed, call `resolve_fn`
        with (platform, market_id). If the outcome is known → compute
        realized PnL via strategy-aware `realize_pnl()`. If UNRESOLVED →
        leave the position open; we'll try again next cycle.

        Cross-market positions have composite market_ids of the form
        "{poly_id}|{kalshi_ticker}"; we split and probe both sides.
        """
        if now is None:
            now = datetime.now(timezone.utc)
        resolved_now: List[PaperPosition] = []
        for pos in state_db.due_for_resolution(self.conn, now.isoformat()):
            strategy = self._lookup_strategy(pos.opportunity_id)
            primary, secondary = await self._fetch_outcomes(
                pos, strategy, resolve_fn
            )
            pnl = realize_pnl(pos, strategy, primary, secondary)
            if pnl is None:
                logger.info(
                    "paper_probe_unresolved",
                    client_order_id=pos.client_order_id,
                    primary=primary.outcome.value,
                    secondary=secondary.outcome.value if secondary else "n/a",
                )
                continue
            state_db.mark_resolved(
                self.conn, pos.client_order_id, pnl, now.isoformat()
            )
            resolved_now.append(pos)
            logger.info(
                "paper_position_resolved",
                client_order_id=pos.client_order_id,
                realized_pnl=str(pnl),
                outcome_primary=primary.outcome.value,
                outcome_secondary=secondary.outcome.value if secondary else "n/a",
                path="async_probed",
            )
        return resolved_now

    async def _fetch_outcomes(
        self, pos: PaperPosition, strategy: str, resolve_fn: ResolutionFn
    ):
        """Query appropriate resolution endpoint(s) for a position."""
        if strategy == "cross_market":
            # Composite market_id = "{poly}|{kalshi}". Split, probe both.
            parts = pos.market_id.split("|")
            if len(parts) == 2:
                a = await resolve_fn("polymarket", parts[0])
                b = await resolve_fn("kalshi", parts[1])
                return a, b
            # Malformed — skip with unresolved.
            return Resolution(ResolutionOutcome.UNRESOLVED, "malformed_composite_id"), None

        # Single-platform: intra_market, resolution_convergence, etc.
        primary = await resolve_fn(pos.platform, pos.market_id)
        return primary, None
