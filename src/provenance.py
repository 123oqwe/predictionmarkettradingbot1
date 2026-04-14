"""Provenance bundle. Attached to every trade/error so post-mortem is possible.

Phase 2 formalizes this into a hash-based audit trail; we put the foundations in
Phase 0 so no legacy records lack provenance.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ProvenanceBundle:
    git_commit: str
    git_dirty: bool
    config_hash: str
    schema_version: int
    started_at: str  # ISO-8601 UTC
    raw_config: Dict[str, Any] = field(default_factory=dict, compare=False, hash=False)

    def serialize(self) -> str:
        # Exclude raw_config from serialized form; it's large and already hashed.
        return json.dumps(
            {
                "git_commit": self.git_commit,
                "git_dirty": self.git_dirty,
                "config_hash": self.config_hash,
                "schema_version": self.schema_version,
                "started_at": self.started_at,
            },
            sort_keys=True,
        )


def _run(cmd: list[str]) -> str:
    try:
        out = subprocess.check_output(
            cmd, stderr=subprocess.DEVNULL, cwd=Path(__file__).resolve().parent.parent
        )
        return out.decode().strip()
    except Exception:
        return ""


def git_commit_short() -> str:
    return _run(["git", "rev-parse", "--short=12", "HEAD"]) or "unknown"


def git_is_dirty() -> bool:
    status = _run(["git", "status", "--porcelain"])
    return bool(status)


def config_hash_of(raw_config: Dict[str, Any]) -> str:
    """Stable SHA-256 over the raw config dict.

    Uses sort_keys so semantically-equivalent reorderings produce the same hash.
    """
    canonical = json.dumps(raw_config, sort_keys=True, default=str).encode()
    return hashlib.sha256(canonical).hexdigest()[:16]


def build_bundle(raw_config: Dict[str, Any]) -> ProvenanceBundle:
    return ProvenanceBundle(
        git_commit=git_commit_short(),
        git_dirty=git_is_dirty(),
        config_hash=config_hash_of(raw_config),
        schema_version=SCHEMA_VERSION,
        started_at=datetime.now(timezone.utc).isoformat(),
        raw_config=raw_config,
    )
