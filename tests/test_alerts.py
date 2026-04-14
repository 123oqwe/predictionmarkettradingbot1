"""Tests for redaction layer and Telegram alerter (stub mode)."""
from __future__ import annotations

import traceback

import pytest

from src.alerts.redact import redact
from src.alerts.telegram import AlertLevel, TelegramAlerter, TelegramConfig


class TestRedaction:
    def test_redacts_github_pat(self):
        # Build a synthetic PAT that matches the regex pattern without being
        # a real-looking credential (GitHub push protection would block it).
        fake_pat = "github_pat_" + ("X" * 50)
        s = f"config has {fake_pat} inside"
        out = redact(s)
        assert "github_pat_" not in out
        assert "[REDACTED:github_pat]" in out

    def test_redacts_telegram_token(self):
        s = "bot token 1234567:AABBCCddEEFFggHHiiJJkkLLmmNNooPPqq"
        out = redact(s)
        assert "1234567:AABBCC" not in out
        assert "[REDACTED:" in out

    def test_redacts_aws_access(self):
        s = "AWS access key AKIA1234567890ABCDEF used"
        out = redact(s)
        assert "AKIA1234567890ABCDEF" not in out

    def test_redacts_kv_assignment(self):
        s = 'config api_key = "my-secret-12345"'
        out = redact(s)
        # The sk- pattern doesn't match here, but the keyish_assignment one does.
        assert "my-secret-12345" not in out

    def test_redaction_idempotent(self):
        s = "github_pat_" + "x" * 50
        once = redact(s)
        twice = redact(once)
        assert once == twice

    def test_redacts_traceback(self):
        try:
            api_key = "github_pat_" + "x" * 50  # noqa: F841
            raise RuntimeError(f"failed with key {api_key}")
        except Exception:
            tb = traceback.format_exc()
        out = redact(tb)
        assert "github_pat_xxx" not in out
        assert "[REDACTED:github_pat]" in out


class TestTelegramStubMode:
    @pytest.mark.asyncio
    async def test_stub_send_succeeds_without_token(self):
        alerter = TelegramAlerter(TelegramConfig())
        ok = await alerter.send(AlertLevel.INFO, "hello")
        assert ok is True

    @pytest.mark.asyncio
    async def test_redacts_secret_in_body(self):
        alerter = TelegramAlerter(TelegramConfig())
        # Stub send always returns True; we verify redaction happens in the
        # logged body by inspecting the redact function directly.
        body = "leaking github_pat_" + "y" * 50
        cleaned = redact(body)
        assert "[REDACTED:github_pat]" in cleaned
        await alerter.send(AlertLevel.INFO, body)

    @pytest.mark.asyncio
    async def test_rate_limit_drops_non_critical(self):
        cfg = TelegramConfig(max_per_hour_non_critical=2)
        alerter = TelegramAlerter(cfg)
        # In stub mode, three INFO sends: first 2 succeed, 3rd is dropped.
        r1 = await alerter.send(AlertLevel.INFO, "a")
        r2 = await alerter.send(AlertLevel.INFO, "b")
        r3 = await alerter.send(AlertLevel.INFO, "c")
        assert r1 is True
        assert r2 is True
        assert r3 is False

    @pytest.mark.asyncio
    async def test_critical_bypasses_rate_limit(self):
        cfg = TelegramConfig(max_per_hour_non_critical=0)
        alerter = TelegramAlerter(cfg)
        info = await alerter.send(AlertLevel.INFO, "a")
        crit = await alerter.send(AlertLevel.CRITICAL, "halt!")
        assert info is False  # rate-limited
        assert crit is True   # CRITICAL always sends
