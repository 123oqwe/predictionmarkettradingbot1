"""Storage backend abstraction.

Round C #8: this module is the seam for migrating from SQLite to Postgres
without touching every caller. Callers import `get_backend(cfg)` rather than
importing `src.storage.state_db` directly. The backend exposes the same
function surface so swapping implementations is a one-line config change.

Current state:
  - "sqlite" (default): wraps state_db — production-ready.
  - "postgres": stub that raises NotImplementedError at construction. The
    operator fills in the connection setup and schema migration once they
    reach Phase 3 live deployment (doc requires Postgres for real
    concurrency).

The stub deliberately preserves the import surface so that switching
requires only `cfg.storage.backend: postgres` + implementing ~200 lines in
`src/storage/state_db_postgres.py`.
"""
from __future__ import annotations

_VALID_BACKENDS = {"sqlite", "postgres"}


def get_backend(name: str = "sqlite"):
    """Return a module-like object that exposes the state_db API."""
    name = (name or "sqlite").lower()
    if name == "sqlite":
        from src.storage import state_db

        return state_db
    if name == "postgres":
        raise NotImplementedError(
            "Postgres backend is a Phase 3+ deployment task. See "
            "docs/HANDOFF.md §'Postgres migration' for the template. The "
            "SQLite backend continues to work for development and paper mode."
        )
    raise ValueError(f"unknown storage backend: {name}. Valid: {_VALID_BACKENDS}")
