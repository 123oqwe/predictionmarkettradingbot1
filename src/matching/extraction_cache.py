"""Parquet-backed extraction cache.

Key: (market_id, description_hash, rules_hash, llm_model_version).
Any change to description, rules, or model version busts the cache entry.
This closes the "stale description" cross-contamination hole the doc warned
about (Polymarket often tweaks description without changing formal rules).

Storage: one Parquet file per platform. Flat columnar layout. Entries are
append-only; on cache hit we use the most recent entry for the key.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

import pyarrow as pa
import pyarrow.parquet as pq
import structlog

from src.matching.schema import ResolutionCriteria

logger = structlog.get_logger(__name__)


CACHE_SCHEMA = pa.schema(
    [
        ("market_id", pa.string()),
        ("description_hash", pa.string()),
        ("rules_hash", pa.string()),
        ("llm_model_version", pa.string()),
        ("cached_at", pa.timestamp("us", tz="UTC")),
        ("criteria_json", pa.string()),
    ]
)


def _cache_path(base_dir: str | Path, platform: str) -> Path:
    p = Path(base_dir) / "extraction_cache" / f"{platform}.parquet"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _criteria_to_json(criteria: ResolutionCriteria) -> str:
    """Serialize to stable JSON (Decimals → strings, datetimes → ISO)."""
    d = criteria.model_dump(mode="json")
    return json.dumps(d, sort_keys=True, default=str)


def _json_to_criteria(s: str) -> ResolutionCriteria:
    return ResolutionCriteria(**json.loads(s))


class ExtractionCache:
    def __init__(self, base_dir: str | Path, platform: str):
        self.path = _cache_path(base_dir, platform)
        self.platform = platform
        # Keep latest entries in memory for fast lookup. Rebuilt from file on init.
        self._by_key: Dict[tuple, ResolutionCriteria] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            table = pq.read_table(self.path)
        except Exception as e:
            logger.warning("extraction_cache_read_failed", error=str(e), path=str(self.path))
            return
        for row in table.to_pylist():
            key = (
                row["market_id"],
                row["description_hash"],
                row["rules_hash"],
                row["llm_model_version"],
            )
            try:
                self._by_key[key] = _json_to_criteria(row["criteria_json"])
            except Exception as e:
                logger.warning("extraction_cache_parse_failed", error=str(e))

    def get(
        self,
        *,
        market_id: str,
        description_hash: str,
        rules_hash: str,
        llm_model_version: str,
    ) -> Optional[ResolutionCriteria]:
        return self._by_key.get(
            (market_id, description_hash, rules_hash, llm_model_version)
        )

    def put(self, *, market_id: str, criteria: ResolutionCriteria) -> None:
        key = (
            market_id,
            criteria.description_hash,
            criteria.raw_rules_hash,
            criteria.llm_model_version,
        )
        self._by_key[key] = criteria
        self._append_row(
            market_id=market_id,
            description_hash=criteria.description_hash,
            rules_hash=criteria.raw_rules_hash,
            llm_model_version=criteria.llm_model_version,
            criteria_json=_criteria_to_json(criteria),
        )

    def _append_row(
        self,
        *,
        market_id: str,
        description_hash: str,
        rules_hash: str,
        llm_model_version: str,
        criteria_json: str,
    ) -> None:
        row = {
            "market_id": market_id,
            "description_hash": description_hash,
            "rules_hash": rules_hash,
            "llm_model_version": llm_model_version,
            "cached_at": datetime.now(timezone.utc),
            "criteria_json": criteria_json,
        }
        new_table = pa.Table.from_pylist([row], schema=CACHE_SCHEMA)
        if self.path.exists():
            # Append by reading + concatenating. Cache size stays small enough
            # that this is fine; if it grows we can switch to one file per day.
            existing = pq.read_table(self.path)
            merged = pa.concat_tables([existing, new_table])
            pq.write_table(merged, self.path, compression="snappy")
        else:
            pq.write_table(new_table, self.path, compression="snappy")

    def size(self) -> int:
        return len(self._by_key)

    def gc(self, *, keep_most_recent: int = 5000, before_date: Optional[datetime] = None) -> int:
        """Round B #13: cache GC. Returns rows dropped.

        Two modes:
          - `keep_most_recent`: keep only the N newest entries per key.
          - `before_date`: drop entries with cached_at older than this.

        Rewrites the Parquet file once. Cheap enough to run daily.
        """
        if not self.path.exists():
            return 0
        table = pq.read_table(self.path)
        rows = table.to_pylist()
        rows.sort(key=lambda r: r.get("cached_at") or datetime.min.replace(tzinfo=timezone.utc))

        if before_date is not None:
            rows = [r for r in rows if (r.get("cached_at") or datetime.min.replace(tzinfo=timezone.utc)) >= before_date]
        if len(rows) > keep_most_recent:
            rows = rows[-keep_most_recent:]

        dropped = table.num_rows - len(rows)
        if dropped <= 0:
            return 0

        new_table = pa.Table.from_pylist(rows, schema=CACHE_SCHEMA)
        pq.write_table(new_table, self.path, compression="snappy")
        # Reload in-memory map.
        self._by_key = {}
        self._load()
        return dropped
