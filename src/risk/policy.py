"""Kill switch policy + enforcement layer.

Three modes per rule (config-driven):
  - disabled: skip evaluation entirely
  - observe:  evaluate, log "would have tripped" events, do NOT halt
  - enforce:  evaluate, log AND halt trading on first trip

Cooldown: every rule has a cooldown window (default 5 min). Within the window
a rule can't trip again — prevents flapping when metrics oscillate around the
threshold.

Persistence: trips and resets go through state_db so a restart does not
forget that we're halted. Refusing to start when an enforce-tripped switch
is set is the orchestrator's job.

Pure: Rule evaluation is a function of (Metrics snapshot, config). Side-effects
go through state_db helpers.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional

from src.monitoring.metrics import MetricsRegistry
from src.storage import state_db


class TriggerName(str, enum.Enum):
    DAILY_LOSS_EXCEEDED = "daily_loss_exceeded"
    ABNORMAL_PRICE_JUMP = "abnormal_price_jump"
    API_DISCONNECT = "api_disconnect"
    CLOCK_DRIFT = "clock_drift"
    UNHANDLED_EXCEPTION_RATE = "unhandled_exception_rate"
    POSITION_MISMATCH = "position_mismatch"
    USDC_DEPEG = "usdc_depeg"
    LAYER_STALL = "layer_stall"
    MANUAL = "manual"
    EVENT_MAP_DRIFT = "event_map_drift"


class PolicyMode(str, enum.Enum):
    DISABLED = "disabled"
    OBSERVE = "observe"
    ENFORCE = "enforce"


class Verdict(str, enum.Enum):
    OK = "ok"
    TRIP = "trip"


@dataclass(frozen=True)
class RuleDecision:
    verdict: Verdict
    reason: str = ""

    @classmethod
    def ok(cls) -> "RuleDecision":
        return cls(Verdict.OK)

    @classmethod
    def trip(cls, reason: str) -> "RuleDecision":
        return cls(Verdict.TRIP, reason)


@dataclass
class RuleConfig:
    name: TriggerName
    mode: PolicyMode = PolicyMode.OBSERVE
    cooldown_seconds: int = 300


@dataclass
class PolicyConfig:
    rules: Dict[TriggerName, RuleConfig]
    default_mode: PolicyMode = PolicyMode.OBSERVE
    default_cooldown_seconds: int = 300


# Rule signature: takes (metrics, params) → RuleDecision. Pure.
RuleFn = Callable[[MetricsRegistry, dict], RuleDecision]


class PolicyEngine:
    """Evaluates rules each cycle. Persists observations / enforcements via state_db."""

    def __init__(
        self,
        conn,
        config: PolicyConfig,
        provenance_json: str,
        now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        self.conn = conn
        self.config = config
        self.provenance_json = provenance_json
        self._now_fn = now_fn
        self._rules: Dict[TriggerName, RuleFn] = {}
        self._params: Dict[TriggerName, dict] = {}
        self._last_trip_at: Dict[TriggerName, datetime] = {}

    def register(self, name: TriggerName, fn: RuleFn, params: Optional[dict] = None) -> None:
        self._rules[name] = fn
        self._params[name] = params or {}

    def _rule_config(self, name: TriggerName) -> RuleConfig:
        return self.config.rules.get(name) or RuleConfig(name=name, mode=self.config.default_mode)

    def _in_cooldown(self, name: TriggerName, now: datetime) -> bool:
        last = self._last_trip_at.get(name)
        if last is None:
            return False
        rc = self._rule_config(name)
        return (now - last) < timedelta(seconds=rc.cooldown_seconds)

    def evaluate_all(self, metrics: MetricsRegistry) -> List[tuple]:
        """Run every registered rule once. Returns list of (name, decision)."""
        now = self._now_fn()
        results: List[tuple] = []
        for name, fn in self._rules.items():
            rc = self._rule_config(name)
            if rc.mode == PolicyMode.DISABLED:
                continue
            if self._in_cooldown(name, now):
                continue
            decision = fn(metrics, self._params[name])
            results.append((name, decision))
            if decision.verdict == Verdict.TRIP:
                self._handle_trip(name, decision, rc, now)
        return results

    def _handle_trip(
        self, name: TriggerName, decision: RuleDecision, rc: RuleConfig, now: datetime
    ) -> None:
        iso = now.isoformat()
        if rc.mode == PolicyMode.ENFORCE:
            state_db.kill_switch_enforce(
                self.conn, name.value, decision.reason, iso, self.provenance_json
            )
        else:
            state_db.kill_switch_record_observation(
                self.conn, name.value, decision.reason, iso, self.provenance_json
            )
        self._last_trip_at[name] = now

    def should_halt(self) -> List[str]:
        """Return list of currently-tripped trigger names. Empty list = OK to trade."""
        return state_db.any_kill_switch_tripped(self.conn)

    def force_trip(self, name: TriggerName, reason: str = "test") -> None:
        """Test-only helper: simulate a trip regardless of metrics state."""
        rc = self._rule_config(name)
        self._handle_trip(name, RuleDecision.trip(reason), rc, self._now_fn())
