"""Telegram alerter with rate limiting and secret redaction.

Levels (per phase-2 doc):
  INFO     — daily summary, notable opportunity
  WARN     — retry succeeded, slow API, observe-mode trip
  ERROR    — unhandled exception, reconciliation mismatch
  CRITICAL — kill switch ENFORCED

Rate limit: at most `max_per_hour` non-CRITICAL alerts per hour; excess are
batched into the next summary (we just drop with log here in Phase 2; batching
arrives in Phase 5 productization). CRITICAL is never rate limited.

Stub mode: when no token is configured the alerter logs to stdout and is a
no-op against Telegram. Tests always run in stub mode.
"""
from __future__ import annotations

import enum
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Optional

import aiohttp
import structlog

from src.alerts.redact import redact

logger = structlog.get_logger(__name__)


class AlertLevel(str, enum.Enum):
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


@dataclass
class TelegramConfig:
    bot_token: Optional[str] = None
    chat_id: Optional[str] = None
    max_per_hour_non_critical: int = 5
    api_timeout_seconds: int = 5

    @property
    def enabled(self) -> bool:
        return bool(self.bot_token and self.chat_id)


class TelegramAlerter:
    """Telegram alerter with DB-backed rate limiting.

    Round A fix #7: rate-limit counter persists in `telegram_alert_log`.
    Process restarts don't reset the window — an alert loop in a crash-restart
    scenario won't flood operators with duplicate warnings.

    Pass `conn=None` to use the old in-memory deque (tests, ephemeral runs).
    """

    def __init__(self, cfg: TelegramConfig, conn=None):
        self.cfg = cfg
        self.conn = conn
        self._recent_non_critical: Deque[float] = deque()
        self._timeout = aiohttp.ClientTimeout(total=cfg.api_timeout_seconds)

    def _allow_non_critical(self, now: float) -> bool:
        """DB-backed if self.conn is set, else in-memory fallback."""
        if self.conn is not None:
            from datetime import datetime, timedelta, timezone

            from src.storage import state_db

            cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
            sent = state_db.count_non_critical_alerts_since(self.conn, cutoff.isoformat())
            return sent < self.cfg.max_per_hour_non_critical

        # In-memory fallback.
        cutoff = now - 3600
        while self._recent_non_critical and self._recent_non_critical[0] < cutoff:
            self._recent_non_critical.popleft()
        if len(self._recent_non_critical) >= self.cfg.max_per_hour_non_critical:
            return False
        self._recent_non_critical.append(now)
        return True

    def _record_sent(self, level: "AlertLevel") -> None:
        if self.conn is None:
            return
        from datetime import datetime, timezone

        from src.storage import state_db

        try:
            state_db.record_telegram_alert(
                self.conn,
                datetime.now(timezone.utc).isoformat(),
                level.value,
            )
        except Exception as e:
            logger.warning("telegram_log_failed", error=str(e))

    async def send(self, level: AlertLevel, message: str, *, force: bool = False) -> bool:
        """Returns True if the message was actually sent (or stub-logged)."""
        # Always redact before any further processing.
        clean = redact(message)
        prefix = f"[{level.value}] "
        body = prefix + clean

        if level != AlertLevel.CRITICAL and not force:
            now = time.time()
            if not self._allow_non_critical(now):
                logger.info("telegram_alert_rate_limited", level=level.value, body=body[:100])
                return False

        self._record_sent(level)

        if not self.cfg.enabled:
            # Stub mode: log and return.
            logger.info("telegram_alert_stub", level=level.value, body=body)
            return True

        url = f"https://api.telegram.org/bot{self.cfg.bot_token}/sendMessage"
        payload = {"chat_id": self.cfg.chat_id, "text": body}
        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as session:
                async with session.post(url, json=payload) as resp:
                    ok = resp.status == 200
                    if not ok:
                        logger.warning(
                            "telegram_send_failed",
                            status=resp.status,
                            body=body[:200],
                        )
                    return ok
        except Exception as e:
            # Never let a failed alert crash the orchestrator.
            logger.warning("telegram_send_exception", error=redact(str(e)))
            return False
