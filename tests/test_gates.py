"""Three-gate system tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.risk.gates import DEFAULT_GATE_CONFIGS, Gate, GateState


def _now():
    return datetime(2026, 4, 14, 12, 0, 0, tzinfo=timezone.utc)


class TestGate1Graduation:
    def test_needs_min_days(self):
        s = GateState(current_gate=Gate.GATE_1, gate_entered_at=_now())
        for _ in range(5):
            s.record_fill()
        # Only 1 day passed, need 3.
        assert s.evaluate_for_graduation(_now() + timedelta(days=1)) is None

    def test_needs_min_fills(self):
        s = GateState(current_gate=Gate.GATE_1, gate_entered_at=_now())
        for _ in range(2):
            s.record_fill()
        # 4 days passed but only 2 fills, need 5.
        assert s.evaluate_for_graduation(_now() + timedelta(days=4)) is None

    def test_graduates_when_conditions_met(self):
        s = GateState(current_gate=Gate.GATE_1, gate_entered_at=_now())
        for _ in range(5):
            s.record_fill()
        assert s.evaluate_for_graduation(_now() + timedelta(days=4)) == Gate.GATE_2

    def test_advance_resets_state(self):
        s = GateState(current_gate=Gate.GATE_1, gate_entered_at=_now())
        for _ in range(5):
            s.record_fill()
        later = _now() + timedelta(days=4)
        next_gate = s.evaluate_for_graduation(later)
        s.advance_to(next_gate, later)
        assert s.current_gate == Gate.GATE_2
        assert s.successful_fills_at_gate == 0
        assert s.gate_entered_at == later


class TestGate2Graduation:
    def test_requires_calibration_coverage(self):
        s = GateState(current_gate=Gate.GATE_2, gate_entered_at=_now())
        for _ in range(20):
            s.record_fill()
        later = _now() + timedelta(days=5)
        # No calibration data → no graduation.
        assert s.evaluate_for_graduation(later) is None
        # Below target → no graduation.
        s.set_calibration_coverage(0.80)
        assert s.evaluate_for_graduation(later) is None
        # At target → graduates.
        s.set_calibration_coverage(0.86)
        assert s.evaluate_for_graduation(later) == Gate.GATE_3


class TestGate3Terminal:
    def test_gate_3_does_not_graduate_further(self):
        s = GateState(current_gate=Gate.GATE_3, gate_entered_at=_now())
        s.set_calibration_coverage(0.95)
        for _ in range(100):
            s.record_fill()
        assert s.evaluate_for_graduation(_now() + timedelta(days=30)) is None


class TestThresholdsPerDoc:
    def test_gate1_is_60_pct_annualized(self):
        from decimal import Decimal

        cfg = DEFAULT_GATE_CONFIGS[Gate.GATE_1]
        assert cfg.annualized_threshold == Decimal("0.60")
        assert cfg.size_cap_usd == Decimal("10")
        assert cfg.post_fill_pause_seconds == 10

    def test_gate3_is_20_pct_annualized(self):
        from decimal import Decimal

        cfg = DEFAULT_GATE_CONFIGS[Gate.GATE_3]
        assert cfg.annualized_threshold == Decimal("0.20")
        assert cfg.size_cap_usd == Decimal("25")
