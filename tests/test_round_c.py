"""Round C: TokenBucket + storage backend abstraction."""
from __future__ import annotations

import time

import pytest

from src.layer4_execution.rate_limit import TokenBucket
from src.storage.backend import get_backend


class TestTokenBucket:
    def test_invalid_rate_raises(self):
        with pytest.raises(ValueError):
            TokenBucket(rate_per_sec=0, burst=10)

    def test_invalid_burst_raises(self):
        with pytest.raises(ValueError):
            TokenBucket(rate_per_sec=10, burst=0)

    @pytest.mark.asyncio
    async def test_burst_passes_through_quickly(self):
        bucket = TokenBucket(rate_per_sec=10, burst=5)
        start = time.monotonic()
        for _ in range(5):
            await bucket.acquire()
        elapsed = time.monotonic() - start
        # 5 acquires within the burst should take < 100ms.
        assert elapsed < 0.1

    @pytest.mark.asyncio
    async def test_sustained_rate_limited(self):
        bucket = TokenBucket(rate_per_sec=20, burst=2)
        start = time.monotonic()
        # Consume burst then request 3 more → must wait at ~20/sec.
        await bucket.acquire()
        await bucket.acquire()
        # These 3 need fresh tokens.
        await bucket.acquire()
        await bucket.acquire()
        await bucket.acquire()
        elapsed = time.monotonic() - start
        # 3 extra tokens at 20/sec = 150ms minimum. Allow a little overhead.
        assert elapsed >= 0.10
        # But also shouldn't take way more than 300ms.
        assert elapsed < 0.5

    @pytest.mark.asyncio
    async def test_rejects_tokens_over_capacity(self):
        bucket = TokenBucket(rate_per_sec=10, burst=2)
        with pytest.raises(ValueError, match="exceeds bucket capacity"):
            await bucket.acquire(tokens=5)

    @pytest.mark.asyncio
    async def test_context_manager_usage(self):
        bucket = TokenBucket(rate_per_sec=100, burst=5)
        async with bucket:
            pass
        # Burst reduced by 1.
        assert bucket.available_tokens < 5


class TestStorageBackend:
    def test_sqlite_returns_state_db_module(self):
        backend = get_backend("sqlite")
        # Smoke check — has the functions we use.
        assert hasattr(backend, "connect")
        assert hasattr(backend, "init_schema")
        assert hasattr(backend, "write_opportunity")

    def test_postgres_not_yet_implemented(self):
        with pytest.raises(NotImplementedError, match="Postgres backend"):
            get_backend("postgres")

    def test_unknown_backend_raises(self):
        with pytest.raises(ValueError, match="unknown storage backend"):
            get_backend("redis")

    def test_default_is_sqlite(self):
        backend = get_backend()
        assert backend is get_backend("sqlite")
