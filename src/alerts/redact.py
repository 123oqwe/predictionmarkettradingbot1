"""Secret redaction for log messages and exception tracebacks.

The doc warns that Python tracebacks include local variable values, so an
exception near a line holding `api_key = "..."` can serialize the key into
log output. We pre-process every alert message through this redactor.

Strategy: a simple regex catalog. New secret formats can be added by appending.
"""
from __future__ import annotations

import re
from typing import List, Pattern, Tuple

_REDACTORS: List[Tuple[str, Pattern]] = [
    # GitHub PATs
    ("[REDACTED:github_pat]", re.compile(r"github_pat_[A-Za-z0-9_]{40,}")),
    ("[REDACTED:github_token]", re.compile(r"ghp_[A-Za-z0-9]{30,}")),
    # Telegram bot tokens (digits:alphanum, ~46 chars)
    ("[REDACTED:telegram_token]", re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{30,}\b")),
    # AWS keys
    ("[REDACTED:aws_access]", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    # Generic Anthropic / OpenAI keys
    ("[REDACTED:api_key_sk]", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    # Round A #16: JWTs (three base64url segments separated by dots)
    ("[REDACTED:jwt]", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    # Round A #16: Bearer authorization headers
    ("[REDACTED:bearer]", re.compile(r"(?i)Bearer\s+[A-Za-z0-9._~+/=-]{10,}")),
    # Round A #16: UUIDs in token-like contexts
    ("[REDACTED:uuid_token]", re.compile(
        r"(?i)(?:token|key|secret|auth)[\s=:]+[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}"
    )),
    # Polymarket / Kalshi-shape (very loose) — anything that looks like an env-var assignment
    # to a key/secret/token field gets the value redacted.
    (
        "[REDACTED:keyish_assignment]",
        re.compile(r'(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*["\']?[^"\'\s]{6,}["\']?'),
    ),
    # Hex blobs ≥ 32 chars (private keys, hashes that contain auth bytes)
    ("[REDACTED:long_hex]", re.compile(r"\b[0-9a-fA-F]{40,}\b")),
]


def redact(text: str) -> str:
    """Apply all redaction patterns. Idempotent (running on already-redacted text is a no-op)."""
    if not text:
        return text
    out = text
    for replacement, pattern in _REDACTORS:
        out = pattern.sub(replacement, out)
    return out


def add_pattern(replacement_label: str, regex: str) -> None:
    """Register an additional redaction pattern at runtime (e.g., per-customer)."""
    _REDACTORS.append((f"[REDACTED:{replacement_label}]", re.compile(regex)))
