"""Monitoring layer.

In-process metric collectors. Each cycle the orchestrator calls `snapshot()` to
push current values to SQLite. The Prometheus endpoint reads the same in-memory
state for low-latency scrapes.

Design rule from phase-2: monitoring NEVER decides anything. It records.
The policy layer reads these metrics and decides; enforcement halts.
"""
from __future__ import annotations

import json
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Deque, Dict, List, Optional, Tuple


@dataclass
class _Counter:
    value: float = 0.0

    def inc(self, n: float = 1.0) -> None:
        self.value += n


@dataclass
class _Gauge:
    value: float = 0.0

    def set(self, v: float) -> None:
        self.value = float(v)


@dataclass
class _RollingWindow:
    """Keeps timestamped events for the last `window_seconds`."""

    window_seconds: int
    events: Deque[Tuple[float, float]] = field(default_factory=deque)

    def add(self, value: float, ts: Optional[float] = None) -> None:
        if ts is None:
            ts = time.time()
        self.events.append((ts, value))
        self._trim(ts)

    def _trim(self, now: float) -> None:
        cutoff = now - self.window_seconds
        while self.events and self.events[0][0] < cutoff:
            self.events.popleft()

    def sum(self, now: Optional[float] = None) -> float:
        if now is None:
            now = time.time()
        self._trim(now)
        return sum(v for _, v in self.events)

    def count(self, now: Optional[float] = None) -> int:
        if now is None:
            now = time.time()
        self._trim(now)
        return len(self.events)


@dataclass
class _Latency:
    """Crude in-memory percentile tracker. Last N samples kept."""

    capacity: int = 1000
    samples: Deque[float] = field(default_factory=deque)

    def observe(self, ms: float) -> None:
        self.samples.append(float(ms))
        while len(self.samples) > self.capacity:
            self.samples.popleft()

    def percentile(self, p: float) -> float:
        if not self.samples:
            return 0.0
        srt = sorted(self.samples)
        idx = max(0, min(len(srt) - 1, int(p * (len(srt) - 1))))
        return srt[idx]


class MetricsRegistry:
    """All metrics in one place. Orchestrator is the only writer; snapshot() reads."""

    def __init__(self):
        # Counters
        self.opportunities_detected_total = _Counter()
        self.opportunities_passed_total = _Counter()
        self.trades_executed_total = _Counter()
        self.api_errors_total: Dict[str, _Counter] = {}
        self.exceptions_total = _Counter()

        # Gauges
        self.capital_utilization_pct = _Gauge()
        self.clock_drift_seconds = _Gauge()
        self.event_map_content_hash = _Gauge()  # treat as a checksum for change detection
        self.layer_heartbeat_age: Dict[str, _Gauge] = {}

        # Rolling windows for derived rates
        self.opportunities_per_minute = _RollingWindow(60)
        self.exceptions_per_5min = _RollingWindow(300)
        self.rolling_pnl_24h_usd = _Gauge()  # set from DB query each cycle

        # Latency
        self.api_latency_ms: Dict[str, _Latency] = {}

        # Heartbeats — last time each layer was seen alive.
        self._last_heartbeat: Dict[str, datetime] = {}

    def heartbeat(self, layer: str, now: Optional[datetime] = None) -> None:
        if now is None:
            now = datetime.now(timezone.utc)
        self._last_heartbeat[layer] = now

    def heartbeat_age_seconds(self, layer: str, now: Optional[datetime] = None) -> Optional[float]:
        if layer not in self._last_heartbeat:
            return None
        if now is None:
            now = datetime.now(timezone.utc)
        return (now - self._last_heartbeat[layer]).total_seconds()

    def record_api_error(self, platform: str) -> None:
        self.api_errors_total.setdefault(platform, _Counter()).inc()

    def record_api_latency(self, platform: str, ms: float) -> None:
        self.api_latency_ms.setdefault(platform, _Latency()).observe(ms)

    def snapshot(self, now: Optional[datetime] = None) -> List[Tuple[str, float, Optional[str]]]:
        """Return a list of (name, value, labels_json) tuples for persistence."""
        if now is None:
            now = datetime.now(timezone.utc)
        out: List[Tuple[str, float, Optional[str]]] = [
            ("opportunities_detected_total", self.opportunities_detected_total.value, None),
            ("opportunities_passed_total", self.opportunities_passed_total.value, None),
            ("trades_executed_total", self.trades_executed_total.value, None),
            ("exceptions_total", self.exceptions_total.value, None),
            ("capital_utilization_pct", self.capital_utilization_pct.value, None),
            ("clock_drift_seconds", self.clock_drift_seconds.value, None),
            ("rolling_pnl_24h_usd", self.rolling_pnl_24h_usd.value, None),
            (
                "exceptions_per_5min",
                float(self.exceptions_per_5min.count(now=now.timestamp())),
                None,
            ),
            (
                "opportunities_per_minute",
                float(self.opportunities_per_minute.count(now=now.timestamp())),
                None,
            ),
        ]
        for platform, counter in self.api_errors_total.items():
            out.append(("api_errors_total", counter.value, json.dumps({"platform": platform})))
        for platform, lat in self.api_latency_ms.items():
            out.append(
                ("api_latency_ms_p50", lat.percentile(0.5), json.dumps({"platform": platform}))
            )
            out.append(
                ("api_latency_ms_p95", lat.percentile(0.95), json.dumps({"platform": platform}))
            )
            out.append(
                ("api_latency_ms_p99", lat.percentile(0.99), json.dumps({"platform": platform}))
            )
        for layer in list(self._last_heartbeat.keys()):
            age = self.heartbeat_age_seconds(layer, now=now) or 0.0
            out.append(
                ("layer_heartbeat_age_seconds", float(age), json.dumps({"layer": layer}))
            )
        return out

    def to_prometheus(self, now: Optional[datetime] = None) -> str:
        """Render in Prometheus exposition format. Trivial — flat counters/gauges."""
        lines: List[str] = []
        for name, value, labels_json in self.snapshot(now=now):
            label_str = ""
            if labels_json:
                labels = json.loads(labels_json)
                label_str = "{" + ",".join(f'{k}="{v}"' for k, v in labels.items()) + "}"
            lines.append(f"{name}{label_str} {value}")
        return "\n".join(lines) + "\n"


def persist_snapshot(conn, registry: MetricsRegistry, now: Optional[datetime] = None) -> None:
    """Write the current snapshot to the metrics SQLite table."""
    from src.storage import state_db

    if now is None:
        now = datetime.now(timezone.utc)
    iso = now.isoformat()
    for name, value, labels_json in registry.snapshot(now=now):
        state_db.write_metric(conn, name, float(value), iso, labels_json)
