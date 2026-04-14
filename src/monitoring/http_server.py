"""Tiny health + metrics HTTP server. localhost-only by design.

GET /health  → 200 + {mode, uptime_s, kill_switch_state, last_reconcile}
              503 + reason when not healthy
GET /metrics → text/plain Prometheus format

Bind to 127.0.0.1 only. Never 0.0.0.0. No auth (no external exposure).
"""
from __future__ import annotations

import time
from typing import Optional

from aiohttp import web

from src.monitoring.metrics import MetricsRegistry
from src.storage import state_db


class HealthServer:
    def __init__(
        self,
        registry: MetricsRegistry,
        conn,
        *,
        mode: str = "paper",
        port: int = 9100,
        bind: str = "127.0.0.1",
    ):
        self.registry = registry
        self.conn = conn
        self.mode = mode
        self.port = port
        self.bind = bind
        self._started_at = time.time()
        self._app = web.Application()
        self._app.router.add_get("/health", self._handle_health)
        self._app.router.add_get("/metrics", self._handle_metrics)
        self._runner: Optional[web.AppRunner] = None

    async def start(self) -> None:
        # Hard-enforced bind to localhost; if a caller passes 0.0.0.0 we refuse.
        if self.bind not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError(
                f"HealthServer.bind={self.bind!r} is non-loopback. "
                f"Refusing to expose health/metrics externally."
            )
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.bind, self.port)
        await site.start()

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    async def _handle_health(self, request: web.Request) -> web.Response:
        tripped = state_db.any_kill_switch_tripped(self.conn)
        body = {
            "mode": self.mode,
            "uptime_s": int(time.time() - self._started_at),
            "kill_switch_tripped": tripped,
            "schema_version": state_db.current_schema_version(self.conn),
        }
        status = 503 if tripped else 200
        return web.json_response(body, status=status)

    async def _handle_metrics(self, request: web.Request) -> web.Response:
        text = self.registry.to_prometheus()
        return web.Response(text=text, content_type="text/plain")
