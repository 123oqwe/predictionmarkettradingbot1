"""Provenance bundle. Attached to every trade/error so post-mortem is possible.

Round A fixes:
  #11: bundle now includes a `deps_hash` + `python_version`. A transparent
       `pip install -U pydantic` that silently changes model behavior will
       produce a different deps_hash — so trade records remain traceable
       even across environment drift.
  #15: config_hash uses a canonical JSON encoder with Decimal-aware
       serialization so dict key order and Python-version float repr
       don't produce different hashes for semantically identical configs.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict

SCHEMA_VERSION = 2  # bumped due to added fields; backward-compat via defaults


def _canonicalize(obj):
    """Recursive canonical form for config/prov hashing. Stable across Python
    versions. Decimals become strings; datetimes become isoformat; unknown
    types become repr() — which can drift, but this function is only used on
    JSON-safe config dicts, so this is a defensive last resort.
    """
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _canonicalize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_canonicalize(v) for v in obj]
    if isinstance(obj, (str, int, bool)) or obj is None:
        return obj
    if isinstance(obj, float):
        # Route floats through Decimal so 0.1 and similar round-trip stably.
        return str(Decimal(str(obj)))
    return repr(obj)


@dataclass(frozen=True)
class ProvenanceBundle:
    git_commit: str
    git_dirty: bool
    config_hash: str
    schema_version: int
    started_at: str  # ISO-8601 UTC
    # Round A #11: new fields. Defaulted so legacy code paths still construct.
    deps_hash: str = ""
    python_version: str = ""
    raw_config: Dict[str, Any] = field(default_factory=dict, compare=False, hash=False)

    def serialize(self) -> str:
        return json.dumps(
            {
                "git_commit": self.git_commit,
                "git_dirty": self.git_dirty,
                "config_hash": self.config_hash,
                "schema_version": self.schema_version,
                "started_at": self.started_at,
                "deps_hash": self.deps_hash,
                "python_version": self.python_version,
            },
            sort_keys=True,
        )


def _run(cmd: list) -> str:
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

    Uses _canonicalize for Python-version-stable Decimal/datetime handling.
    Semantically equivalent reorderings produce the same hash.
    """
    canonical = json.dumps(_canonicalize(raw_config), sort_keys=True).encode()
    return hashlib.sha256(canonical).hexdigest()[:16]


def deps_hash() -> str:
    """Hash over installed package versions.

    Uses `pip freeze` output, which is stable per environment. Any dep upgrade
    or downgrade produces a different hash. Falls back to "unknown" if pip
    isn't available (e.g., baked-into-container scenarios).
    """
    output = _run([sys.executable, "-m", "pip", "freeze", "--disable-pip-version-check"])
    if not output:
        return "unknown"
    # Sort to stabilize order across pip versions.
    lines = sorted(line.strip() for line in output.splitlines() if line.strip())
    canonical = "\n".join(lines)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def build_bundle(raw_config: Dict[str, Any]) -> ProvenanceBundle:
    return ProvenanceBundle(
        git_commit=git_commit_short(),
        git_dirty=git_is_dirty(),
        config_hash=config_hash_of(raw_config),
        schema_version=SCHEMA_VERSION,
        started_at=datetime.now(timezone.utc).isoformat(),
        deps_hash=deps_hash(),
        python_version=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        raw_config=raw_config,
    )
