"""Round A regression tests: #7, #11, #15, #16, #17, #18."""
from __future__ import annotations

import pytest

from src.alerts.redact import redact
from src.alerts.telegram import AlertLevel, TelegramAlerter, TelegramConfig
from src.monitoring.metrics import MetricsRegistry
from src.monitoring.probes import probe_disk_free_pct
from src.provenance import build_bundle, config_hash_of, deps_hash
from src.risk import rules
from src.risk.policy import Verdict
from src.storage import state_db


def _open(tmp_path):
    conn = state_db.connect(tmp_path / "state.db")
    state_db.init_schema(conn)
    return conn


# ---- #7: Telegram rate-limit persists ----


class TestTelegramRateLimitPersists:
    @pytest.mark.asyncio
    async def test_rate_limit_survives_restart(self, tmp_path):
        conn = _open(tmp_path)
        cfg = TelegramConfig(max_per_hour_non_critical=2)
        alerter = TelegramAlerter(cfg, conn=conn)
        # Fill up the quota.
        assert await alerter.send(AlertLevel.INFO, "one")
        assert await alerter.send(AlertLevel.INFO, "two")
        # Third is blocked.
        assert not await alerter.send(AlertLevel.INFO, "three")

        # Simulate restart: new alerter, same DB.
        alerter2 = TelegramAlerter(cfg, conn=conn)
        # Still rate-limited — DB remembers.
        assert not await alerter2.send(AlertLevel.INFO, "four")

    @pytest.mark.asyncio
    async def test_critical_bypasses_even_with_persist(self, tmp_path):
        conn = _open(tmp_path)
        cfg = TelegramConfig(max_per_hour_non_critical=0)
        alerter = TelegramAlerter(cfg, conn=conn)
        assert not await alerter.send(AlertLevel.INFO, "blocked")
        assert await alerter.send(AlertLevel.CRITICAL, "emergency")


# ---- #11: deps hash + python_version in provenance ----


class TestProvenanceDepsHash:
    def test_bundle_includes_deps_hash(self):
        bundle = build_bundle({"mode": "paper"})
        assert bundle.deps_hash != ""
        assert len(bundle.deps_hash) >= 10
        assert bundle.python_version != ""
        assert "." in bundle.python_version

    def test_deps_hash_stable(self):
        a = deps_hash()
        b = deps_hash()
        assert a == b

    def test_serialize_includes_new_fields(self):
        import json as _json

        bundle = build_bundle({"mode": "paper"})
        parsed = _json.loads(bundle.serialize())
        assert "deps_hash" in parsed
        assert "python_version" in parsed


# ---- #15: config hash canonical ----


class TestConfigHashCanonical:
    def test_dict_order_insensitive(self):
        a = {"mode": "paper", "allocation": {"total": "1000", "per_trade": "100"}}
        b = {"allocation": {"per_trade": "100", "total": "1000"}, "mode": "paper"}
        assert config_hash_of(a) == config_hash_of(b)

    def test_decimal_vs_string_same(self):
        from decimal import Decimal

        a = {"x": Decimal("0.20")}
        b = {"x": "0.20"}
        # Canonicalizer converts Decimal → str, so they match.
        assert config_hash_of(a) == config_hash_of(b)

    def test_nested_lists_order_matters(self):
        """Lists are ordered — a reorder is a real change."""
        a = {"tags": ["fed", "rates"]}
        b = {"tags": ["rates", "fed"]}
        assert config_hash_of(a) != config_hash_of(b)


# ---- #16: JWT / Bearer / UUID redaction ----


class TestExpandedRedaction:
    def test_redacts_jwt(self):
        s = "Authorization: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        out = redact(s)
        assert "eyJhbGc" not in out
        assert "[REDACTED:" in out

    def test_redacts_bearer_header(self):
        s = "curl -H 'Authorization: Bearer abcdefghij123456789012345'"
        out = redact(s)
        assert "Bearer abcdefghij" not in out
        assert "[REDACTED:bearer]" in out

    def test_redacts_uuid_in_token_context(self):
        s = "session token=550e8400-e29b-41d4-a716-446655440000 end"
        out = redact(s)
        assert "550e8400" not in out


# ---- #17: disk probe + rule ----


class TestDiskProbeAndRule:
    def test_disk_probe_returns_float(self):
        pct = probe_disk_free_pct("/")
        assert pct is None or 0 <= pct <= 100

    def test_rule_ok_when_plenty_free(self):
        m = MetricsRegistry()
        m.disk_free_pct.set(50.0)
        d = rules.disk_free_low(m, {"min_free_pct": 5.0})
        assert d.verdict == Verdict.OK

    def test_rule_trips_when_low(self):
        m = MetricsRegistry()
        m.disk_free_pct.set(2.0)
        d = rules.disk_free_low(m, {"min_free_pct": 5.0})
        assert d.verdict == Verdict.TRIP

    def test_rule_registered_in_all_rules(self):
        assert "disk_free_low" in rules.ALL_RULES


# ---- #18: anthropic opt-in extras ----


class TestAnthropicExtrasDeclared:
    def test_pyproject_declares_llm_extra(self):
        from pathlib import Path

        content = (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text()
        assert "llm = [" in content or "llm=[" in content
        assert "anthropic" in content

    def test_pyproject_declares_postgres_extra(self):
        from pathlib import Path

        content = (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text()
        assert "postgres = [" in content or "postgres=[" in content
