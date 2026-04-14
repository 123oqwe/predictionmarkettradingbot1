"""Provenance module tests."""
from __future__ import annotations

from src.provenance import (
    ProvenanceBundle,
    build_bundle,
    config_hash_of,
    git_commit_short,
)


def test_config_hash_stable_under_key_order():
    a = {"foo": 1, "bar": {"x": 2, "y": [1, 2, 3]}}
    b = {"bar": {"y": [1, 2, 3], "x": 2}, "foo": 1}
    assert config_hash_of(a) == config_hash_of(b)


def test_config_hash_changes_on_value_change():
    a = {"foo": 1}
    b = {"foo": 2}
    assert config_hash_of(a) != config_hash_of(b)


def test_build_bundle_fields_present():
    b = build_bundle({"mode": "paper"})
    assert isinstance(b, ProvenanceBundle)
    assert b.git_commit  # either a hash or 'unknown'
    assert b.schema_version == 1
    # Serialize must be JSON-parseable and not contain raw_config.
    import json

    parsed = json.loads(b.serialize())
    assert "raw_config" not in parsed
    assert parsed["config_hash"] == b.config_hash


def test_git_commit_short_returns_string():
    s = git_commit_short()
    assert isinstance(s, str)
    assert len(s) > 0
