"""Recovery and health endpoint tests."""
from __future__ import annotations

from datetime import datetime, timezone

import aiohttp
import pytest

from src.monitoring.http_server import HealthServer
from src.monitoring.metrics import MetricsRegistry
from src.risk.recovery import perform_recovery
from src.storage import state_db


def _open_db(tmp_path):
    conn = state_db.connect(tmp_path / "state.db")
    state_db.init_schema(conn)
    return conn


class TestRecovery:
    def test_clean_db_is_safe_to_trade(self, tmp_path):
        conn = _open_db(tmp_path)
        report = perform_recovery(conn)
        assert report.safe_to_trade is True
        assert report.tripped_triggers == []

    def test_tripped_switch_blocks_trading(self, tmp_path):
        conn = _open_db(tmp_path)
        state_db.kill_switch_enforce(
            conn, "manual", "test", datetime.now(timezone.utc).isoformat(), "{}"
        )
        report = perform_recovery(conn)
        assert report.safe_to_trade is False
        assert "manual" in report.tripped_triggers

    def test_reset_clears_tripped(self, tmp_path):
        conn = _open_db(tmp_path)
        state_db.kill_switch_enforce(
            conn, "manual", "test", datetime.now(timezone.utc).isoformat(), "{}"
        )
        ok = state_db.kill_switch_reset(
            conn, "manual", "tester", datetime.now(timezone.utc).isoformat()
        )
        assert ok
        report = perform_recovery(conn)
        assert report.safe_to_trade is True


class TestHealthServer:
    @pytest.mark.asyncio
    async def test_refuses_non_loopback_bind(self, tmp_path):
        conn = _open_db(tmp_path)
        registry = MetricsRegistry()
        server = HealthServer(registry, conn, bind="0.0.0.0", port=9123)
        with pytest.raises(ValueError, match="non-loopback"):
            await server.start()

    @pytest.mark.asyncio
    async def test_serves_metrics_and_health(self, tmp_path, unused_tcp_port):
        conn = _open_db(tmp_path)
        registry = MetricsRegistry()
        registry.opportunities_detected_total.inc(3)
        server = HealthServer(registry, conn, port=unused_tcp_port)
        await server.start()
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"http://127.0.0.1:{unused_tcp_port}/health") as resp:
                    assert resp.status == 200
                    body = await resp.json()
                    assert body["kill_switch_tripped"] == []
                async with session.get(f"http://127.0.0.1:{unused_tcp_port}/metrics") as resp:
                    assert resp.status == 200
                    text = await resp.text()
                    assert "opportunities_detected_total 3.0" in text
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_health_503_when_tripped(self, tmp_path, unused_tcp_port):
        conn = _open_db(tmp_path)
        state_db.kill_switch_enforce(
            conn, "manual", "test", datetime.now(timezone.utc).isoformat(), "{}"
        )
        server = HealthServer(MetricsRegistry(), conn, port=unused_tcp_port)
        await server.start()
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"http://127.0.0.1:{unused_tcp_port}/health") as resp:
                    assert resp.status == 503
        finally:
            await server.stop()


@pytest.fixture
def unused_tcp_port():
    """Find an unused TCP port for the health server tests."""
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port
