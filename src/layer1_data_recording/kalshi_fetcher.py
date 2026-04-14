"""Kalshi market fetcher.

Kalshi exposes a REST API at https://api.elections.kalshi.com/trade-api/v2/.
Read endpoints (markets, orderbook) are public; trading requires API key auth.
We only need read access for Phase 1.

Unlike Polymarket's YES/NO-as-separate-tokens model, a Kalshi market has a single
ticker with a YES/NO price structure directly in the orderbook. Kalshi orderbook:
  { "orderbook": { "yes": [[price_cents, size], ...], "no": [[price_cents, size], ...] } }
Prices are reported in cents (0-99, integer) — we convert to Decimal dollars.

Critical gotcha: Kalshi ticks at 1 cent. Cross-market detection must round our
target fill prices to integer cents before computing the math, otherwise a
"0.505" detection becomes a "0.51" actual, killing thin arbitrage.
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

KALSHI_TICK_SIZE = Decimal("0.01")  # 1 cent


def quantize_to_kalshi_tick(price: Decimal) -> Decimal:
    """Round a price to Kalshi's nearest tick (1 cent, rounded half-up to worst)."""
    # Round conservatively: asks round UP (we'll pay more), bids round DOWN.
    return price.quantize(KALSHI_TICK_SIZE)


def _parse_book_side(
    levels_payload: list, sort_ascending: bool
) -> OrderBookSide:
    """Kalshi returns levels as [[price_cents, size], ...]. Convert to Decimal $."""
    parsed: list[OrderBookLevel] = []
    for lv in levels_payload or []:
        try:
            if isinstance(lv, (list, tuple)) and len(lv) >= 2:
                price_cents = int(lv[0])
                size = Decimal(str(lv[1]))
            elif isinstance(lv, dict):
                price_cents = int(lv["price"])
                size = Decimal(str(lv.get("size") or lv.get("size_contracts") or 0))
            else:
                continue
            price = Decimal(price_cents) / Decimal(100)
            if size > 0 and 0 < price < 1:
                parsed.append(OrderBookLevel(price=price, size_contracts=size))
        except (KeyError, ValueError, TypeError):
            continue
    parsed.sort(key=lambda lv: lv.price, reverse=not sort_ascending)
    return OrderBookSide(levels=parsed)


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


class KalshiFetcher:
    """Async Kalshi fetcher. Same shape as PolymarketFetcher → Layer 1 abstraction holds."""

    def __init__(
        self,
        base_url: str = "https://api.elections.kalshi.com/trade-api/v2",
        fee_bps: int = 0,
        api_key: Optional[str] = None,
        timeout_seconds: int = 10,
        max_concurrency: int = 8,
        markets_limit: int = 100,
    ):
        self.base_url = base_url.rstrip("/")
        self.fee_bps = fee_bps
        self.api_key = api_key  # Only needed for trading endpoints
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._sem = asyncio.Semaphore(max_concurrency)
        self.markets_limit = markets_limit

    async def _get_json(
        self, session: aiohttp.ClientSession, path: str, params: Optional[dict] = None
    ):
        url = f"{self.base_url}{path}"
        async with self._sem:
            async with session.get(url, params=params, timeout=self._timeout) as resp:
                resp.raise_for_status()
                return await resp.json()

    async def list_active_markets(self, session: aiohttp.ClientSession) -> List[dict]:
        """Active, open Kalshi markets."""
        params = {
            "status": "open",
            "limit": str(self.markets_limit),
        }
        data = await self._get_json(session, "/markets", params)
        if isinstance(data, dict) and "markets" in data:
            return data["markets"]
        return []

    async def fetch_orderbook(
        self, session: aiohttp.ClientSession, ticker: str
    ) -> dict:
        try:
            return await self._get_json(session, f"/markets/{ticker}/orderbook")
        except Exception as e:
            logger.warning("kalshi_orderbook_failed", ticker=ticker, error=str(e))
            return {"orderbook": {"yes": [], "no": []}}

    async def _build_market(
        self, session: aiohttp.ClientSession, raw: dict
    ) -> Optional[Market]:
        try:
            ticker = raw.get("ticker")
            event_ticker = raw.get("event_ticker") or ticker
            if not ticker:
                return None

            status = (raw.get("status") or "").lower()
            if status not in ("open", "active"):
                return None

            resolution = (
                _parse_iso(raw.get("close_time"))
                or _parse_iso(raw.get("expected_expiration_time"))
                or _parse_iso(raw.get("expiration_time"))
            )
            if resolution is None:
                return None
            now = datetime.now(timezone.utc)
            if now >= resolution:
                return None

            book_resp = await self.fetch_orderbook(session, ticker)
            book = book_resp.get("orderbook") or {}

            # Kalshi 'yes' is buying YES (price in cents you pay). 'no' similarly.
            # Bids would be the ask side from the other side's perspective. For arb
            # detection we only need asks — buy YES + buy NO to lock in arbitrage.
            yes_asks = _parse_book_side(book.get("yes"), sort_ascending=True)
            no_asks = _parse_book_side(book.get("no"), sort_ascending=True)

            title = str(raw.get("title") or raw.get("subtitle") or ticker)

            return Market(
                platform="kalshi",
                market_id=str(ticker),
                event_id=str(event_ticker),
                title=title,
                yes_bids=OrderBookSide(levels=[]),  # not used for arb detection
                yes_asks=yes_asks,
                no_bids=OrderBookSide(levels=[]),
                no_asks=no_asks,
                fee_bps=self.fee_bps,
                resolution_date=resolution,
                resolution_source=str(raw.get("settlement_source") or "kalshi"),
                fetched_at=now,
                active=True,
                resolved=False,
                liquidity_usd=Decimal(str(raw.get("liquidity") or 0)),
            )
        except Exception as e:
            logger.warning("kalshi_build_market_failed", ticker=raw.get("ticker"), error=str(e))
            return None

    async def fetch_snapshot(self) -> List[Market]:
        async with aiohttp.ClientSession() as session:
            try:
                raw = await self.list_active_markets(session)
            except Exception as e:
                logger.error("kalshi_list_markets_failed", error=str(e))
                return []
            if not raw:
                return []
            built = await asyncio.gather(
                *[self._build_market(session, m) for m in raw],
                return_exceptions=True,
            )
            return [m for m in built if isinstance(m, Market)]
