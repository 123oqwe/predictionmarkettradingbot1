"""Polymarket data fetcher (read-only, no auth).

Fetches active markets from Gamma (metadata) and joins with CLOB order book data.
The Polymarket CLOB treats YES and NO as two independent tokens with independent
order books; we fetch both and assemble a unified `Market` snapshot.

Read path is public:
  - Gamma: https://gamma-api.polymarket.com/markets?active=true&closed=false
  - CLOB:  https://clob.polymarket.com/book?token_id=<yes_token_id>

The fetcher is best-effort and resilient: any single market that fails to fetch
is logged and skipped, not fatal. A 24-hour soak test should not die from a
momentary rate limit on one market.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Optional

import aiohttp
import structlog

from src.layer3_strategy.models import Market, OrderBookLevel, OrderBookSide

logger = structlog.get_logger(__name__)


def _parse_book_side(levels_payload: list, sort_ascending: bool) -> OrderBookSide:
    """Parse a single side of a Polymarket CLOB order book.

    Polymarket CLOB returns `{ "bids": [...], "asks": [...] }` where each element
    is `{"price": "0.45", "size": "30.0"}` (strings — good, they're meant to be
    Decimals). asks are best = lowest price, bids are best = highest price.
    """
    parsed = []
    for lv in levels_payload or []:
        try:
            price = Decimal(str(lv["price"]))
            size = Decimal(str(lv["size"]))
            if size > 0:
                parsed.append(OrderBookLevel(price=price, size_contracts=size))
        except (KeyError, ValueError, TypeError):
            continue
    parsed.sort(key=lambda lv: lv.price, reverse=not sort_ascending)
    return OrderBookSide(levels=parsed)


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    # Polymarket timestamps are typically ISO 8601 with 'Z' suffix.
    try:
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


class PolymarketFetcher:
    """Async Polymarket fetcher. Call `fetch_snapshot()` in a loop."""

    def __init__(
        self,
        gamma_base_url: str,
        clob_base_url: str,
        fee_bps: int,
        timeout_seconds: int = 10,
        max_concurrency: int = 8,
        markets_limit: int = 100,
    ):
        self.gamma_base_url = gamma_base_url.rstrip("/")
        self.clob_base_url = clob_base_url.rstrip("/")
        self.fee_bps = fee_bps
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._sem = asyncio.Semaphore(max_concurrency)
        self.markets_limit = markets_limit

    async def _get_json(self, session: aiohttp.ClientSession, url: str, params: Optional[dict] = None):
        async with self._sem:
            async with session.get(url, params=params, timeout=self._timeout) as resp:
                resp.raise_for_status()
                return await resp.json()

    async def list_active_markets(self, session: aiohttp.ClientSession) -> List[dict]:
        """Return a list of raw Gamma market dicts for currently-active markets."""
        url = f"{self.gamma_base_url}/markets"
        params = {
            "active": "true",
            "closed": "false",
            "limit": str(self.markets_limit),
            "order": "volume24hr",
            "ascending": "false",
        }
        data = await self._get_json(session, url, params)
        # Gamma sometimes returns list directly, sometimes {markets: [...]}
        if isinstance(data, dict) and "markets" in data:
            return data["markets"]
        if isinstance(data, list):
            return data
        return []

    async def fetch_book(self, session: aiohttp.ClientSession, token_id: str) -> dict:
        url = f"{self.clob_base_url}/book"
        try:
            return await self._get_json(session, url, {"token_id": token_id})
        except Exception as e:
            logger.warning("clob_book_fetch_failed", token_id=token_id, error=str(e))
            return {"bids": [], "asks": []}

    async def _build_market(
        self, session: aiohttp.ClientSession, gamma_market: dict
    ) -> Optional[Market]:
        """Join Gamma metadata with CLOB depth into a Market snapshot.

        Returns None if the market is unusable (missing tokens, missing resolution
        date, resolved/closed, etc.).
        """
        try:
            clob_token_ids = gamma_market.get("clobTokenIds") or []
            if isinstance(clob_token_ids, str):
                # Gamma returns this as a JSON-encoded string sometimes.
                import json as _json

                clob_token_ids = _json.loads(clob_token_ids)
            if not clob_token_ids or len(clob_token_ids) < 2:
                return None

            yes_token, no_token = clob_token_ids[0], clob_token_ids[1]

            yes_book, no_book = await asyncio.gather(
                self.fetch_book(session, yes_token),
                self.fetch_book(session, no_token),
            )

            resolution = (
                _parse_iso(gamma_market.get("endDate"))
                or _parse_iso(gamma_market.get("end_date_iso"))
                or _parse_iso(gamma_market.get("resolutionDate"))
            )
            if resolution is None:
                # Without resolution date we can't annualize; skip.
                return None

            now = datetime.now(timezone.utc)
            if now >= resolution:
                return None

            active = bool(gamma_market.get("active", True))
            closed = bool(gamma_market.get("closed", False))
            if not active or closed:
                return None

            liquidity = gamma_market.get("liquidity") or gamma_market.get("liquidityNum") or 0
            try:
                liquidity_usd = Decimal(str(liquidity))
            except Exception:
                liquidity_usd = Decimal(0)

            market_id = str(gamma_market.get("id") or gamma_market.get("conditionId") or "")
            event_id = str(
                gamma_market.get("eventId")
                or gamma_market.get("event_id")
                or gamma_market.get("conditionId")
                or market_id
            )
            title = str(gamma_market.get("question") or gamma_market.get("title") or market_id)

            return Market(
                platform="polymarket",
                market_id=market_id,
                event_id=event_id,
                title=title,
                yes_bids=_parse_book_side(yes_book.get("bids"), sort_ascending=False),
                yes_asks=_parse_book_side(yes_book.get("asks"), sort_ascending=True),
                no_bids=_parse_book_side(no_book.get("bids"), sort_ascending=False),
                no_asks=_parse_book_side(no_book.get("asks"), sort_ascending=True),
                fee_bps=self.fee_bps,
                resolution_date=resolution,
                resolution_source=str(gamma_market.get("resolutionSource") or "polymarket"),
                fetched_at=now,
                active=True,
                resolved=False,
                liquidity_usd=liquidity_usd,
            )
        except Exception as e:
            logger.warning(
                "build_market_failed",
                market_id=gamma_market.get("id"),
                error=str(e),
            )
            return None

    async def fetch_snapshot(self) -> List[Market]:
        """Fetch a single snapshot of all active markets' order books."""
        async with aiohttp.ClientSession() as session:
            try:
                raw = await self.list_active_markets(session)
            except Exception as e:
                logger.error("list_markets_failed", error=str(e))
                return []

            if not raw:
                return []

            coros = [self._build_market(session, m) for m in raw]
            built = await asyncio.gather(*coros, return_exceptions=True)
            markets: List[Market] = []
            for b in built:
                if isinstance(b, Market):
                    markets.append(b)
            return markets
