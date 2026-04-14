"""Event map loader tests — strict validation is the whole point."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.matching.event_map import (
    EventMapValidationError,
    load_event_map,
)


def _write(tmp_path: Path, contents: str) -> Path:
    p = tmp_path / "event_map.yaml"
    p.write_text(contents)
    return p


_VALID_PAIR = """
schema_version: 1
pairs:
  - pair_id: ok-pair
    polymarket_market_id: "0xabc"
    kalshi_market_ticker: "TICKER-1"
    verified_by: "tester"
    verified_date: "2026-04-01"
    trading_enabled: true
    topic_tags: [test]
    edge_cases_reviewed:
      - { scenario: "case 1", polymarket: "YES", kalshi: "YES", divergent: false }
      - { scenario: "case 2", polymarket: "NO",  kalshi: "NO",  divergent: false }
      - { scenario: "case 3", polymarket: "YES", kalshi: "YES", divergent: false }
      - { scenario: "case 4", polymarket: "YES", kalshi: "YES", divergent: false }
      - { scenario: "case 5 (divergent)", polymarket: "ambiguous", kalshi: "NO", divergent: true, mitigation: "doc" }
"""


def test_loads_valid_event_map(tmp_path):
    p = _write(tmp_path, _VALID_PAIR)
    em = load_event_map(p)
    assert len(em.pairs) == 1
    assert em.pairs[0].pair_id == "ok-pair"
    assert em.pairs[0].trading_enabled is True
    assert len(em.enabled()) == 1
    assert em.content_hash != "empty"


def test_empty_file_returns_empty_map(tmp_path):
    em = load_event_map(tmp_path / "missing.yaml")
    assert em.pairs == []
    assert em.content_hash == "empty"


def test_rejects_pair_below_min_edge_cases(tmp_path):
    yml = """
schema_version: 1
pairs:
  - pair_id: too-few
    polymarket_market_id: "0xabc"
    kalshi_market_ticker: "T1"
    verified_by: "tester"
    verified_date: "2026-04-01"
    trading_enabled: false
    edge_cases_reviewed:
      - { scenario: "only one", polymarket: "YES", kalshi: "YES", divergent: true }
"""
    with pytest.raises(EventMapValidationError, match="at least 5 edge_cases_reviewed"):
        load_event_map(_write(tmp_path, yml))


def test_rejects_pair_with_no_divergent_case(tmp_path):
    yml = """
schema_version: 1
pairs:
  - pair_id: no-divergent
    polymarket_market_id: "0xabc"
    kalshi_market_ticker: "T1"
    verified_by: "tester"
    verified_date: "2026-04-01"
    trading_enabled: true
    edge_cases_reviewed:
      - { scenario: "1", polymarket: "YES", kalshi: "YES", divergent: false }
      - { scenario: "2", polymarket: "YES", kalshi: "YES", divergent: false }
      - { scenario: "3", polymarket: "YES", kalshi: "YES", divergent: false }
      - { scenario: "4", polymarket: "YES", kalshi: "YES", divergent: false }
      - { scenario: "5", polymarket: "YES", kalshi: "YES", divergent: false }
"""
    with pytest.raises(EventMapValidationError, match="divergent=true"):
        load_event_map(_write(tmp_path, yml))


def test_rejects_missing_required_pair_fields(tmp_path):
    yml = """
schema_version: 1
pairs:
  - pair_id: missing-fields
    polymarket_market_id: "0xabc"
    edge_cases_reviewed: []
"""
    with pytest.raises(EventMapValidationError, match="missing required fields"):
        load_event_map(_write(tmp_path, yml))


def test_rejects_duplicate_pair_id(tmp_path):
    yml = (
        _VALID_PAIR
        + """
  - pair_id: ok-pair
    polymarket_market_id: "0xdef"
    kalshi_market_ticker: "T2"
    verified_by: "tester"
    verified_date: "2026-04-01"
    trading_enabled: false
    edge_cases_reviewed:
      - { scenario: "1", polymarket: "YES", kalshi: "YES", divergent: false }
      - { scenario: "2", polymarket: "YES", kalshi: "YES", divergent: false }
      - { scenario: "3", polymarket: "YES", kalshi: "YES", divergent: false }
      - { scenario: "4", polymarket: "YES", kalshi: "YES", divergent: false }
      - { scenario: "5", polymarket: "YES", kalshi: "YES", divergent: true }
"""
    )
    with pytest.raises(EventMapValidationError, match="duplicate pair_id"):
        load_event_map(_write(tmp_path, yml))


def test_rejects_unknown_schema_version(tmp_path):
    yml = """
schema_version: 999
pairs: []
"""
    with pytest.raises(EventMapValidationError, match="schema_version mismatch"):
        load_event_map(_write(tmp_path, yml))


def test_shipped_example_loads(tmp_path):
    """The example file we ship as a template must itself pass validation."""
    example = Path(__file__).resolve().parent.parent / "event_map.example.yaml"
    em = load_event_map(example)
    assert len(em.pairs) == 1
    # Trading must default to disabled in the shipped example — safety.
    assert em.pairs[0].trading_enabled is False
