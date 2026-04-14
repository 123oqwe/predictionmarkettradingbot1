"""Append-only Parquet writer with daily rotation at UTC midnight.

Writes one row per market snapshot. Each row stores the full order book (top-N
levels per side) as JSON strings so the schema stays flat. Parquet column types
are pinned to string for Decimal-bearing fields — pyarrow has no native Decimal
type that round-trips cleanly in all consumers.

The writer does NOT read from the log. It only writes.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import List, Optional

import pyarrow as pa
import pyarrow.parquet as pq
import structlog

from src.layer3_strategy.models import Market, OrderBookSide

logger = structlog.get_logger(__name__)


# Schema is intentionally flat + string-biased to survive Decimal transport.
SCHEMA_VERSION = 1

SNAPSHOT_SCHEMA = pa.schema(
    [
        ("schema_version", pa.int32()),
        ("platform", pa.string()),
        ("market_id", pa.string()),
        ("event_id", pa.string()),
        ("title", pa.string()),
        ("fetched_at", pa.timestamp("us", tz="UTC")),
        ("resolution_date", pa.timestamp("us", tz="UTC")),
        ("resolution_source", pa.string()),
        ("fee_bps", pa.int32()),
        ("active", pa.bool_()),
        ("resolved", pa.bool_()),
        ("liquidity_usd", pa.string()),
        ("yes_bids_json", pa.string()),
        ("yes_asks_json", pa.string()),
        ("no_bids_json", pa.string()),
        ("no_asks_json", pa.string()),
    ]
)


def _side_to_json(side: OrderBookSide) -> str:
    return json.dumps(
        [{"price": str(lv.price), "size_contracts": str(lv.size_contracts)} for lv in side.levels]
    )


def _json_to_side(s: str) -> OrderBookSide:
    from src.layer3_strategy.models import OrderBookLevel

    data = json.loads(s) if s else []
    return OrderBookSide(
        levels=[
            OrderBookLevel(price=Decimal(d["price"]), size_contracts=Decimal(d["size_contracts"]))
            for d in data
        ]
    )


def market_to_row(m: Market) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "platform": m.platform,
        "market_id": m.market_id,
        "event_id": m.event_id,
        "title": m.title,
        "fetched_at": m.fetched_at,
        "resolution_date": m.resolution_date,
        "resolution_source": m.resolution_source,
        "fee_bps": m.fee_bps,
        "active": m.active,
        "resolved": m.resolved,
        "liquidity_usd": str(m.liquidity_usd),
        "yes_bids_json": _side_to_json(m.yes_bids),
        "yes_asks_json": _side_to_json(m.yes_asks),
        "no_bids_json": _side_to_json(m.no_bids),
        "no_asks_json": _side_to_json(m.no_asks),
    }


def row_to_market(row: dict) -> Market:
    return Market(
        platform=row["platform"],
        market_id=row["market_id"],
        event_id=row["event_id"],
        title=row["title"],
        fetched_at=row["fetched_at"],
        resolution_date=row["resolution_date"],
        resolution_source=row["resolution_source"],
        fee_bps=int(row["fee_bps"]),
        active=bool(row["active"]),
        resolved=bool(row["resolved"]),
        liquidity_usd=Decimal(row["liquidity_usd"]),
        yes_bids=_json_to_side(row["yes_bids_json"]),
        yes_asks=_json_to_side(row["yes_asks_json"]),
        no_bids=_json_to_side(row["no_bids_json"]),
        no_asks=_json_to_side(row["no_asks_json"]),
    )


class DailyParquetWriter:
    """Writes market snapshots to daily Parquet files, one per (platform, UTC date).

    Buffers rows in memory and flushes every `flush_interval_seconds`, or when
    the buffer reaches `flush_batch_rows`. On UTC day rollover, the current file
    is closed and a new one is opened.

    Safe for concurrent callers (`write_many` is guarded by an asyncio lock).
    """

    def __init__(
        self,
        base_dir: str | Path,
        platform: str,
        flush_interval_seconds: int = 30,
        flush_batch_rows: int = 200,
    ):
        self.base_dir = Path(base_dir)
        self.platform = platform
        self.flush_interval_seconds = flush_interval_seconds
        self.flush_batch_rows = flush_batch_rows

        self._buffer: List[dict] = []
        self._current_date: Optional[str] = None
        self._writer: Optional[pq.ParquetWriter] = None
        self._current_path: Optional[Path] = None
        self._lock = asyncio.Lock()
        self._last_flush = datetime.now(timezone.utc)

    def _path_for_date(self, date_str: str) -> Path:
        d = self.base_dir / self.platform
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{date_str}.parquet"

    def _rollover_if_needed(self, now: datetime) -> None:
        date_str = now.astimezone(timezone.utc).strftime("%Y-%m-%d")
        if date_str == self._current_date and self._writer is not None:
            return
        # Close prior writer if any.
        if self._writer is not None:
            self._writer.close()
            self._writer = None
        self._current_date = date_str
        self._current_path = self._path_for_date(date_str)
        self._writer = pq.ParquetWriter(
            str(self._current_path),
            SNAPSHOT_SCHEMA,
            compression="snappy",
        )
        logger.info(
            "parquet_rollover",
            platform=self.platform,
            path=str(self._current_path),
        )

    async def write_many(self, markets: List[Market]) -> None:
        if not markets:
            return
        async with self._lock:
            now = datetime.now(timezone.utc)
            self._rollover_if_needed(now)
            for m in markets:
                self._buffer.append(market_to_row(m))
            should_flush = (
                len(self._buffer) >= self.flush_batch_rows
                or (now - self._last_flush).total_seconds() >= self.flush_interval_seconds
            )
            if should_flush:
                await self._flush_locked(now)

    async def _flush_locked(self, now: datetime) -> None:
        if not self._buffer or self._writer is None:
            return
        table = pa.Table.from_pylist(self._buffer, schema=SNAPSHOT_SCHEMA)
        self._writer.write_table(table)
        self._buffer.clear()
        self._last_flush = now

    async def flush(self) -> None:
        async with self._lock:
            await self._flush_locked(datetime.now(timezone.utc))

    async def close(self) -> None:
        async with self._lock:
            await self._flush_locked(datetime.now(timezone.utc))
            if self._writer is not None:
                self._writer.close()
                self._writer = None
