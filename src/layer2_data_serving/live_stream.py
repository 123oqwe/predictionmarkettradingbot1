"""Live stream. Pulls snapshots directly from a Layer 1 fetcher on a fixed cadence.

Same async-iterator interface as `ReplayStream.ticks()`. Every yield represents one
poll cycle's worth of market snapshots.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Callable, List

from src.layer3_strategy.models import Market


class LiveStream:
    """Call `fetch_fn` on a cadence and yield each snapshot batch."""

    def __init__(
        self,
        fetch_fn: Callable[[], asyncio.Future[List[Market]]],
        poll_interval_seconds: int,
    ):
        self.fetch_fn = fetch_fn
        self.poll_interval_seconds = poll_interval_seconds
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def ticks(self) -> AsyncIterator[List[Market]]:
        while not self._stop.is_set():
            markets = await self.fetch_fn()
            yield markets
            if self._stop.is_set():
                break
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self.poll_interval_seconds
                )
            except asyncio.TimeoutError:
                pass
