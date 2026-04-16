"""Replay determinism test — the architectural heartbeat of Phase 0.

Recipe:
  1. Generate a synthetic set of Market snapshots, spread across a few timestamps.
  2. Write them to a Parquet file via DailyParquetWriter.
  3. Read them back twice via ReplayStream + find_opportunities.
  4. Assert the two runs produce byte-identical opportunity output.

If this fails, Layer 3 has hidden state (datetime.now(), unseeded randomness,
dict iteration order, env variables). Finding the offender is the point.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import List

import pytest

from src.layer1_data_recording.parquet_writer import DailyParquetWriter
from src.layer2_data_serving.replay_stream import ReplayStream
from src.layer3_strategy.intra_market import find_opportunities
from src.layer3_strategy.models import Market, OrderBookLevel, OrderBookSide


def _synthetic_markets(anchor: datetime, count: int, tick_offset_s: int) -> List[Market]:
    """Generate `count` deterministic markets at a single timestamp.

    Prices are seeded by index so output is reproducible.
    """
    markets: List[Market] = []
    for i in range(count):
        yes_price = Decimal("0.40") + (Decimal(i) / Decimal(100))  # 0.40, 0.41, 0.42...
        no_price = Decimal("0.50") + (Decimal(i) / Decimal(200))
        ts = anchor + timedelta(seconds=tick_offset_s)
        markets.append(
            Market(
                platform="polymarket",
                market_id=f"mkt-{i:04d}",
                event_id=f"evt-{i:04d}",
                title=f"Synthetic market {i}",
                yes_bids=OrderBookSide(levels=[]),
                yes_asks=OrderBookSide(
                    levels=[OrderBookLevel(price=yes_price, size_contracts=Decimal("500"))]
                ),
                no_bids=OrderBookSide(levels=[]),
                no_asks=OrderBookSide(
                    levels=[OrderBookLevel(price=no_price, size_contracts=Decimal("500"))]
                ),
                fee_bps=0,
                resolution_date=ts + timedelta(days=30),
                resolution_source="test",
                fetched_at=ts,
                active=True,
                resolved=False,
                liquidity_usd=Decimal("1000"),
            )
        )
    return markets


@pytest.mark.asyncio
async def test_replay_is_deterministic(tmp_path: Path, strategy_ctx):
    base_dir = tmp_path / "snapshots"

    # NB: anchor = now() so the writer's today-based file path matches the
    # synthetic markets' fetched_at dates.
    anchor = datetime.now(timezone.utc).replace(microsecond=0)
    # 3 ticks of 20 markets each → 60 rows.
    tick0 = _synthetic_markets(anchor, count=20, tick_offset_s=0)
    tick1 = _synthetic_markets(anchor, count=20, tick_offset_s=5)
    tick2 = _synthetic_markets(anchor, count=20, tick_offset_s=10)

    writer = DailyParquetWriter(base_dir=base_dir, platform="polymarket")
    await writer.write_many(tick0 + tick1 + tick2)
    await writer.close()

    start = anchor - timedelta(seconds=1)
    end = anchor + timedelta(minutes=1)

    async def replay_once() -> str:
        hasher = hashlib.sha256()
        stream = ReplayStream(
            base_dir=base_dir, platform="polymarket", start=start, end=end
        )
        async for t in stream.ticks():
            opps = find_opportunities(t, strategy_ctx)
            for o in opps:
                hasher.update(
                    json.dumps(o.model_dump(mode="json"), sort_keys=True, default=str).encode()
                )
        return hasher.hexdigest()

    h1 = await replay_once()
    h2 = await replay_once()
    assert h1 == h2, f"replay determinism broken: {h1} != {h2}"


@pytest.mark.asyncio
async def test_replay_emits_per_tick_groups(tmp_path: Path, strategy_ctx):
    """Replay should group snapshots by fetched_at; each tick yields a batch.

    NB: synthetic markets use `datetime.now()` as the anchor so the writer's
    today-based file path matches (writer chooses filename from wall-clock,
    not from the markets' fetched_at).
    """
    base_dir = tmp_path / "snapshots"
    anchor = datetime.now(timezone.utc).replace(microsecond=0)
    t0 = _synthetic_markets(anchor, count=5, tick_offset_s=0)
    t1 = _synthetic_markets(anchor, count=5, tick_offset_s=5)

    writer = DailyParquetWriter(base_dir=base_dir, platform="polymarket")
    await writer.write_many(t0 + t1)
    await writer.close()

    stream = ReplayStream(
        base_dir=base_dir,
        platform="polymarket",
        start=anchor - timedelta(seconds=1),
        end=anchor + timedelta(minutes=1),
    )
    groups = [g async for g in stream.ticks()]
    assert len(groups) == 2
    assert all(len(g) == 5 for g in groups)
    # Second tick must strictly follow the first.
    assert groups[0][0].fetched_at < groups[1][0].fetched_at
