"""Async token-bucket rate limiter for live exchange clients.

Round C #9: the operator's real PolymarketLiveClient / KalshiLiveClient MUST
throttle API calls. Polymarket CLOB bans IPs after sustained rate limit
violations; Kalshi has per-endpoint limits that escalate to account suspension.
This module provides a ready-to-use async token bucket they can wrap around
any HTTP call.

Usage:

    bucket = TokenBucket(rate_per_sec=10, burst=20)
    async with bucket:
        await do_http_call()

Or for non-context use:

    await bucket.acquire()
    await do_http_call()
"""
from __future__ import annotations

import asyncio
import time


class TokenBucket:
    """Async token bucket. Refills continuously at `rate_per_sec`.

    `burst` is the bucket capacity — short-lived spikes up to this count
    pass through without delay; beyond that, callers await until refill.

    Thread-safe within a single event loop (uses asyncio.Lock). NOT safe
    across processes — if you run multiple worker processes hitting the
    same exchange, use a distributed limiter (Redis) instead.
    """

    def __init__(self, rate_per_sec: float, burst: int):
        if rate_per_sec <= 0:
            raise ValueError("rate_per_sec must be positive")
        if burst < 1:
            raise ValueError("burst must be >= 1")
        self.rate_per_sec = float(rate_per_sec)
        self.capacity = int(burst)
        self._tokens: float = float(burst)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    def _refill(self, now: float) -> None:
        elapsed = now - self._last_refill
        if elapsed <= 0:
            return
        self._tokens = min(self.capacity, self._tokens + elapsed * self.rate_per_sec)
        self._last_refill = now

    async def acquire(self, tokens: int = 1) -> None:
        """Wait until `tokens` are available, then consume them."""
        if tokens < 1:
            raise ValueError("tokens must be >= 1")
        if tokens > self.capacity:
            raise ValueError(
                f"requested {tokens} tokens exceeds bucket capacity {self.capacity}"
            )
        while True:
            async with self._lock:
                now = time.monotonic()
                self._refill(now)
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                # How long until enough tokens exist?
                deficit = tokens - self._tokens
                wait = deficit / self.rate_per_sec
            # Release the lock before sleeping so other consumers can try.
            await asyncio.sleep(wait)

    async def __aenter__(self):
        await self.acquire()
        return self

    async def __aexit__(self, *_args):
        return None

    @property
    def available_tokens(self) -> float:
        self._refill(time.monotonic())
        return self._tokens
