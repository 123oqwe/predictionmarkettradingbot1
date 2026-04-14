"""Feature flag system for Phase 5 expansions.

Why file-based: the doc demands a "hot kill" that doesn't require a restart
or redeploy. `touch /tmp/disable_opt_e.flag` is the universal interface any
operator can use even mid-incident. The orchestrator checks flags each cycle.

All Phase 5 strategies are default-OFF. Even after you decide to try one,
enabling it is an explicit act — either via config.yaml or via a "enable
file" (the inverse of the disable file) that expires after N hours.

A separate "global_phase5_enabled" master flag gates the whole phase. While
we're still in Phase 3, keep this False. Every Phase 5 strategy ALSO checks
its individual flag, so there are two layers of defense.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Optional

DEFAULT_FLAG_DIR = Path("/tmp/arb_agent_flags")


@dataclass(frozen=True)
class FeatureFlag:
    name: str
    default_enabled: bool = False
    description: str = ""


# All Phase 5 expansions register here. Default = off.
REGISTERED_FLAGS: Dict[str, FeatureFlag] = {
    "global_phase5_enabled": FeatureFlag(
        name="global_phase5_enabled",
        default_enabled=False,
        description="Master switch for all Phase 5 expansions. Off until Phase 3 completes.",
    ),
    "option_e_convergence": FeatureFlag(
        name="option_e_convergence",
        default_enabled=False,
        description="Resolution convergence trading (Phase 5 Option E).",
    ),
    "manifold_fetcher": FeatureFlag(
        name="manifold_fetcher",
        default_enabled=False,
        description="Manifold Markets Layer 1 fetcher (Phase 5 Option B).",
    ),
    "calendar_spreads": FeatureFlag(
        name="calendar_spreads",
        default_enabled=False,
        description="Cross-timescale calendar spread detection (Phase 5 Option G).",
    ),
}


def _disable_path(name: str, flag_dir: Path = DEFAULT_FLAG_DIR) -> Path:
    return flag_dir / f"disable_{name}.flag"


def _enable_path(name: str, flag_dir: Path = DEFAULT_FLAG_DIR) -> Path:
    return flag_dir / f"enable_{name}.flag"


def is_enabled(
    name: str,
    *,
    config_override: Optional[bool] = None,
    flag_dir: Path = DEFAULT_FLAG_DIR,
    now: Optional[datetime] = None,
) -> bool:
    """Resolve enabled state with precedence:

      1. Disable file present → False (always wins; hot kill)
      2. config_override not None → config_override
      3. Enable file present and not expired → True
      4. Flag default_enabled

    Enable file can contain an ISO timestamp on its first line; if the file
    is older than 24h and contains no expiry, we treat it as expired.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    disable_file = _disable_path(name, flag_dir)
    if disable_file.exists():
        return False

    if config_override is not None:
        return bool(config_override)

    enable_file = _enable_path(name, flag_dir)
    if enable_file.exists():
        try:
            text = enable_file.read_text().strip()
        except OSError:
            text = ""
        if text:
            try:
                expiry = datetime.fromisoformat(text.split("\n")[0])
                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=timezone.utc)
                return now < expiry
            except ValueError:
                pass
        # Auto-expire after 24h if no explicit expiry present.
        try:
            mtime = datetime.fromtimestamp(
                enable_file.stat().st_mtime, tz=timezone.utc
            )
            if now - mtime < timedelta(hours=24):
                return True
        except OSError:
            pass
        return False

    flag = REGISTERED_FLAGS.get(name)
    return bool(flag.default_enabled) if flag else False


def disable(name: str, *, flag_dir: Path = DEFAULT_FLAG_DIR) -> Path:
    """Create the disable flag for this feature. Idempotent."""
    flag_dir.mkdir(parents=True, exist_ok=True)
    p = _disable_path(name, flag_dir)
    p.touch()
    return p


def enable(
    name: str,
    *,
    expires_in_hours: Optional[int] = 24,
    flag_dir: Path = DEFAULT_FLAG_DIR,
) -> Path:
    """Create the enable flag, optionally with an expiry. Removes any
    conflicting disable flag."""
    flag_dir.mkdir(parents=True, exist_ok=True)
    disable_file = _disable_path(name, flag_dir)
    if disable_file.exists():
        disable_file.unlink()
    p = _enable_path(name, flag_dir)
    if expires_in_hours is not None:
        expiry = datetime.now(timezone.utc) + timedelta(hours=expires_in_hours)
        p.write_text(expiry.isoformat())
    else:
        p.touch()
    return p


def clear_all(flag_dir: Path = DEFAULT_FLAG_DIR) -> None:
    """Remove every flag file. Useful in tests."""
    if not flag_dir.exists():
        return
    for f in flag_dir.glob("*.flag"):
        try:
            f.unlink()
        except OSError:
            pass
