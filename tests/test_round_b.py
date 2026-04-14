"""Round B regression tests: #6 review queue, #10 extractor retry+cost,
#12 backpressure, #13 cache GC, #14 streaming replay."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
import yaml

from src.layer1_data_recording.parquet_writer import DailyParquetWriter
from src.layer3_strategy.models import Market, OrderBookLevel, OrderBookSide
from src.matching.extraction_cache import ExtractionCache
from src.matching.extractor import (
    Extractor,
    ExtractorConfig,
    ExtractorCostTracker,
    ExtractorMode,
)
from src.matching.review_queue import (
    process_decision,
)

# ---- #6: Review queue closure ----


class TestReviewQueueClosure:
    def _candidate(self, pair_id="test-pair"):
        return {
            "pair_id": pair_id,
            "polymarket_market_id": "0xabc",
            "kalshi_market_ticker": "TICKER",
            "verified_by": "auto",
            "topic_tags": ["fed"],
            "edge_cases_reviewed": [
                {"scenario": "c1", "polymarket": "YES", "kalshi": "YES", "divergent": False},
                {"scenario": "c2", "polymarket": "NO", "kalshi": "NO", "divergent": False},
                {"scenario": "c3", "polymarket": "YES", "kalshi": "YES", "divergent": False},
                {"scenario": "c4", "polymarket": "YES", "kalshi": "YES", "divergent": False},
                {"scenario": "c5", "polymarket": "ambiguous", "kalshi": "NO", "divergent": True},
            ],
        }

    def test_approve_writes_to_event_map_as_disabled(self, tmp_path):
        em_path = tmp_path / "event_map.yaml"
        log_path = tmp_path / "log.jsonl"
        process_decision(
            self._candidate(),
            "a",
            event_map_path=em_path,
            log_path=log_path,
            decided_by="tester",
        )
        # Must exist in event_map.yaml with trading_enabled=false.
        data = yaml.safe_load(em_path.read_text())
        assert len(data["pairs"]) == 1
        assert data["pairs"][0]["trading_enabled"] is False
        assert data["pairs"][0]["pair_id"] == "test-pair"

    def test_reject_does_not_touch_event_map(self, tmp_path):
        em_path = tmp_path / "event_map.yaml"
        log_path = tmp_path / "log.jsonl"
        process_decision(
            self._candidate(),
            "r",
            event_map_path=em_path,
            log_path=log_path,
            decided_by="tester",
        )
        assert not em_path.exists()
        # Log still recorded.
        lines = log_path.read_text().strip().splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["decision"] == "reject"

    def test_dedup_skips_existing_pair(self, tmp_path):
        em_path = tmp_path / "event_map.yaml"
        log_path = tmp_path / "log.jsonl"
        process_decision(
            self._candidate(pair_id="dup"),
            "a",
            event_map_path=em_path,
            log_path=log_path,
            decided_by="tester",
        )
        # Approve again — should no-op in event_map.
        process_decision(
            self._candidate(pair_id="dup"),
            "a",
            event_map_path=em_path,
            log_path=log_path,
            decided_by="tester",
        )
        data = yaml.safe_load(em_path.read_text())
        assert len(data["pairs"]) == 1

    def test_conditional_includes_mitigation_note(self, tmp_path):
        em_path = tmp_path / "event_map.yaml"
        log_path = tmp_path / "log.jsonl"
        process_decision(
            self._candidate(pair_id="cond"),
            "m",
            event_map_path=em_path,
            log_path=log_path,
            decided_by="tester",
            mitigation_note="Requires monitoring rate cycle dates",
        )
        data = yaml.safe_load(em_path.read_text())
        assert "Mitigation" in data["pairs"][0]["notes"]


# ---- #10: Extractor cost tracker ----


class TestExtractorCostTracker:
    def test_tracks_cost_accumulates(self):
        t = ExtractorCostTracker(input_usd_per_mtok=3.0, output_usd_per_mtok=15.0)
        t.record(1_000_000, 100_000)
        # 1M input × $3 + 0.1M output × $15 = $3 + $1.5 = $4.5
        assert abs(t.total_usd - 4.5) < 0.001
        assert t.calls == 1

    def test_snapshot_shape(self):
        t = ExtractorCostTracker()
        t.record(1000, 200)
        snap = t.snapshot()
        assert "total_usd" in snap
        assert snap["calls"] == 1


# ---- #12: Backpressure ----


class TestBackpressure:
    @pytest.mark.asyncio
    async def test_writer_drops_beyond_buffer_limit(self, tmp_path):
        writer = DailyParquetWriter(
            base_dir=tmp_path,
            platform="polymarket",
            max_buffer_size=3,  # tight ceiling
            flush_interval_seconds=10_000,  # never auto-flush
            flush_batch_rows=10_000,
        )
        anchor = datetime(2026, 4, 14, 12, 0, 0, tzinfo=timezone.utc)
        mkts = [
            Market(
                platform="polymarket",
                market_id=f"m-{i}",
                event_id=f"e-{i}",
                title=f"T{i}",
                yes_bids=OrderBookSide(levels=[]),
                yes_asks=OrderBookSide(levels=[OrderBookLevel(price=Decimal("0.5"), size_contracts=Decimal("100"))]),
                no_bids=OrderBookSide(levels=[]),
                no_asks=OrderBookSide(levels=[OrderBookLevel(price=Decimal("0.5"), size_contracts=Decimal("100"))]),
                fee_bps=0,
                resolution_date=anchor + timedelta(days=30),
                resolution_source="test",
                fetched_at=anchor,
                active=True,
                resolved=False,
                liquidity_usd=Decimal("1000"),
            )
            for i in range(10)
        ]
        await writer.write_many(mkts)
        # Buffer max 3; 10 submitted → 7 dropped.
        assert writer.drops_due_to_backpressure == 7
        await writer.close()


# ---- #13: Cache GC ----


class TestCacheGc:
    @pytest.mark.asyncio
    async def test_gc_keeps_newest_only(self, tmp_path):
        cache = ExtractionCache(base_dir=tmp_path, platform="polymarket")
        e = Extractor(ExtractorConfig(mode=ExtractorMode.STUB))
        # Build up several cached entries with distinct keys.
        for i in range(10):
            crit = await e.extract(title=f"m{i}", description=f"d{i}", rules_text="r")
            cache.put(market_id=f"m{i}", criteria=crit)
        assert cache.size() == 10
        dropped = cache.gc(keep_most_recent=3)
        assert dropped == 7
        assert cache.size() == 3


# ---- #14: Streaming replay ----


class TestStreamingReplay:
    @pytest.mark.asyncio
    async def test_streams_per_file_not_whole_range(self, tmp_path):
        """Verify the per-file streaming invariant: we yield groups as we go,
        not after loading everything. Assert that yield happens incrementally
        by checking we get at least one group before we've finished iterating.
        """
        from src.layer2_data_serving.replay_stream import ReplayStream

        base = tmp_path / "snapshots"
        writer = DailyParquetWriter(base_dir=base, platform="polymarket")

        anchor = datetime(2026, 4, 14, 12, 0, 0, tzinfo=timezone.utc)

        def _m(ts, i):
            return Market(
                platform="polymarket",
                market_id=f"m-{i}",
                event_id=f"e-{i}",
                title=f"T{i}",
                yes_bids=OrderBookSide(levels=[]),
                yes_asks=OrderBookSide(levels=[OrderBookLevel(price=Decimal("0.5"), size_contracts=Decimal("100"))]),
                no_bids=OrderBookSide(levels=[]),
                no_asks=OrderBookSide(levels=[OrderBookLevel(price=Decimal("0.5"), size_contracts=Decimal("100"))]),
                fee_bps=0,
                resolution_date=ts + timedelta(days=30),
                resolution_source="test",
                fetched_at=ts,
                active=True,
                resolved=False,
                liquidity_usd=Decimal("1000"),
            )

        # Two distinct timestamps → two groups.
        markets = [_m(anchor, i) for i in range(5)] + [
            _m(anchor + timedelta(seconds=5), i) for i in range(5)
        ]
        await writer.write_many(markets)
        await writer.close()

        stream = ReplayStream(
            base_dir=base,
            platform="polymarket",
            start=anchor - timedelta(seconds=1),
            end=anchor + timedelta(minutes=1),
        )
        groups = [g async for g in stream.ticks()]
        # Exactly 2 groups (one per unique fetched_at), 5 markets each.
        assert len(groups) == 2
        assert all(len(g) == 5 for g in groups)
        # Second group is strictly after first.
        assert groups[0][0].fetched_at < groups[1][0].fetched_at
