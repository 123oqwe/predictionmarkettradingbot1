"""Historical replay stream.

Reads recorded Parquet files and yields Market snapshots in chronological order.
Same async-iterator interface as `live_stream.LiveStream` so Layer 3 / Layer 4
don't know which mode they're running in.

Output is strictly ordered by (fetched_at, market_id) to ensure determinism:
two replays of the same date range produce byte-identical outputs.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import pyarrow.parquet as pq

from src.layer1_data_recording.parquet_writer import row_to_market
from src.layer3_strategy.models import Market


class ReplayStream:
    """Iterate over historical snapshots in chronological order.

    Usage:
        rs = ReplayStream(base_dir="data/snapshots", platform="polymarket",
                          start=datetime(2026, 4, 14), end=datetime(2026, 4, 15))
        async for tick in rs.ticks():
            for market in tick:
                ...
    """

    def __init__(
        self,
        base_dir: str | Path,
        platform: str,
        start: datetime,
        end: datetime,
    ):
        self.base_dir = Path(base_dir)
        self.platform = platform
        self.start = start
        self.end = end

    def _files_in_range(self) -> List[Path]:
        d = self.base_dir / self.platform
        if not d.exists():
            return []
        out: List[Path] = []
        for p in sorted(d.glob("*.parquet")):
            try:
                date_str = p.stem
                day = datetime.strptime(date_str, "%Y-%m-%d")
            except ValueError:
                continue
            # Include any file whose date could contain data in [start, end].
            if day.date() >= self.start.date() and day.date() <= self.end.date():
                out.append(p)
        return out

    async def ticks(self) -> AsyncIterator[List[Market]]:
        """Yield groups of Market snapshots sharing the same `fetched_at`."""
        rows: List[dict] = []
        for f in self._files_in_range():
            table = pq.read_table(f)
            # Convert to Python rows in chunks to avoid loading everything at once
            # for very large files.
            df = table.to_pandas(ignore_metadata=True) if False else None  # noqa
            for batch in table.to_batches(max_chunksize=10_000):
                batch_rows = batch.to_pylist()
                for r in batch_rows:
                    if r["fetched_at"] is None:
                        continue
                    ts = r["fetched_at"]
                    # Pyarrow returns tz-aware datetimes for timestamp[us, UTC].
                    if isinstance(ts, datetime) and self.start <= ts <= self.end:
                        rows.append(r)

        # Sort by fetched_at then market_id for determinism.
        rows.sort(key=lambda r: (r["fetched_at"], r["market_id"]))

        # Group by fetched_at (same-tick batches).
        current_ts: Optional[datetime] = None
        group: List[Market] = []
        for r in rows:
            ts = r["fetched_at"]
            if current_ts is None:
                current_ts = ts
            if ts != current_ts:
                if group:
                    yield group
                group = []
                current_ts = ts
            group.append(row_to_market(r))
        if group:
            yield group
