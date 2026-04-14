"""Tests for cross-market threshold derivation.

Note: the phase-1 doc shows '0.224 / 0.98 ≈ 22.9%' for p_div=0.02 — that
arithmetic is mistyped (0.20 + 0.02 = 0.22, not 0.224). The correct value is
22.45%, which is what the formula produces and what we test for.
"""
from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.threshold_calc import derive_cross_threshold  # noqa: E402


class TestThresholdMath:
    def test_doc_example_p2(self):
        r = derive_cross_threshold(Decimal("0.20"), Decimal("0.02"))
        # (0.20 + 0.02) / 0.98 = 0.22449...
        assert r > Decimal("0.224") and r < Decimal("0.225")

    def test_doc_example_p5(self):
        r = derive_cross_threshold(Decimal("0.20"), Decimal("0.05"))
        # (0.20 + 0.05) / 0.95 = 0.26316...
        assert r > Decimal("0.263") and r < Decimal("0.264")

    def test_zero_divergence_equals_intra(self):
        r = derive_cross_threshold(Decimal("0.20"), Decimal("0"))
        assert r == Decimal("0.20")

    def test_higher_divergence_higher_threshold(self):
        a = derive_cross_threshold(Decimal("0.20"), Decimal("0.01"))
        b = derive_cross_threshold(Decimal("0.20"), Decimal("0.05"))
        c = derive_cross_threshold(Decimal("0.20"), Decimal("0.10"))
        assert a < b < c

    def test_rejects_invalid_divergence(self):
        with pytest.raises(ValueError):
            derive_cross_threshold(Decimal("0.20"), Decimal("-0.1"))
        with pytest.raises(ValueError):
            derive_cross_threshold(Decimal("0.20"), Decimal("1.5"))

    def test_returns_decimal(self):
        r = derive_cross_threshold(Decimal("0.20"), Decimal("0.05"))
        assert isinstance(r, Decimal)
