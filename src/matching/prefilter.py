"""Cheap pre-filter for candidate pair generation.

Goal: reduce the cartesian product of markets (Polymarket × Kalshi) by ~99%
before any LLM call. Doc says ~1% survive this stage.

Four filters, conjunctive (ALL must pass):
  1. Category/keyword overlap: at least one common tag or keyword
  2. Date compatibility: resolution dates within N days
  3. Liquidity floor: both markets above min_liquidity_usd
  4. Status: both active, neither resolved

Pure function — no I/O, fully testable.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, List, Set, Tuple

from src.layer3_strategy.models import Market


@dataclass(frozen=True)
class PrefilterConfig:
    min_liquidity_usd: Decimal = Decimal("100")
    max_resolution_date_delta_days: int = 7
    min_keyword_overlap: int = 1


# Simple tokenizer: keep lowercase words >=3 chars, drop stopwords.
_STOPWORDS = {
    "the", "and", "for", "will", "would", "does", "this", "that", "with",
    "from", "into", "any", "all", "out", "has", "have", "had", "not", "been",
    "but", "are", "was", "were", "its", "their", "our", "your", "question",
    "resolve", "resolves", "yes", "no", "usd", "com", "www", "http", "https",
}


def _tokenize(s: str) -> Set[str]:
    words = re.findall(r"[A-Za-z][A-Za-z0-9]+", s.lower())
    return {w for w in words if len(w) >= 3 and w not in _STOPWORDS}


def keyword_overlap(a: Market, b: Market) -> int:
    """Number of shared tokens in titles. Crude but effective first filter."""
    return len(_tokenize(a.title) & _tokenize(b.title))


def prefilter_pair(a: Market, b: Market, cfg: PrefilterConfig) -> bool:
    """True if (a, b) is a candidate worth running LLM extraction on."""
    if a.resolved or b.resolved:
        return False
    if not a.active or not b.active:
        return False
    if a.liquidity_usd < cfg.min_liquidity_usd:
        return False
    if b.liquidity_usd < cfg.min_liquidity_usd:
        return False
    delta = abs((a.resolution_date - b.resolution_date).days)
    if delta > cfg.max_resolution_date_delta_days:
        return False
    return keyword_overlap(a, b) >= cfg.min_keyword_overlap


def prefilter_pairs(
    polymarket: Iterable[Market],
    kalshi: Iterable[Market],
    cfg: PrefilterConfig,
) -> List[Tuple[Market, Market]]:
    """Return the surviving (poly, kalshi) candidate pairs.

    Iteration is deterministic: outer loop = Polymarket in given order, inner
    loop = Kalshi in given order. Same inputs → same output list.
    """
    poly_list = list(polymarket)
    kal_list = list(kalshi)
    out: List[Tuple[Market, Market]] = []
    for a in poly_list:
        for b in kal_list:
            if prefilter_pair(a, b, cfg):
                out.append((a, b))
    return out
