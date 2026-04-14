"""Phase 3 uncertainty-bounded paper model.

The doc's key shift: paper stops being a point estimator, becomes a probabilistic
model. Each opportunity gets three profit numbers — expected, p05, p95 — and
calibration is measured by "did live actually land within [p05, p95] ≥ 85% of
the time?"

We model four sources of uncertainty per doc:
  1. Slippage: historical distribution of (live_fill - paper_expected) in bps.
  2. Partial fills: binomial on per-trade fill probability.
  3. Fees: mostly deterministic but volume-tier crossings add tails.
  4. Time-to-resolution: earlier or later resolution changes annualized, but
     not realized profit in absolute terms. We don't propagate this into
     profit p05/p95 directly — it shows up in annualized CI separately.

Initial distributions are bootstrapped from Phase 2.5 backtest data (honest
pessimistic fill model). After Gate 1 we update from paired live observations.

This module is PURE. It reads an Opportunity (already detected) and
configurable distributions, returns an OpportunityUncertainty — does not
modify the Opportunity itself (keeps Phase 0-2 schemas stable).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from statistics import quantiles
from typing import List

from src.layer3_strategy.models import Opportunity


@dataclass(frozen=True)
class UncertaintyInputs:
    """Observed distributions backing the uncertainty model."""

    slippage_bps_samples: List[float]  # (live_fill - paper) per trade in bps
    fill_rate_samples: List[float]     # fraction of requested size actually filled
    fee_overrun_bps_samples: List[float]  # realized_fee - quoted_fee, bps

    @classmethod
    def default_pre_live(cls) -> "UncertaintyInputs":
        """Reasonable priors before any live data exists.

        Numbers come from Phase 2.5 pessimistic fill model assumptions:
          - slippage ~ N(mean=20, sd=30) bps (lower-bounded at 0)
          - fill rate ~ 0.95 typical, 0.75 worst case
          - fee overrun ~ 0 bps typical, up to 10 bps tail
        """
        return cls(
            slippage_bps_samples=[0, 5, 10, 15, 20, 25, 30, 40, 50, 70, 100],
            fill_rate_samples=[1.0, 1.0, 1.0, 0.95, 0.90, 0.85, 0.75],
            fee_overrun_bps_samples=[0, 0, 0, 2, 5, 10],
        )


@dataclass(frozen=True)
class OpportunityUncertainty:
    """Probability-aware view of an opportunity's expected PnL.

    All fields are Decimal. The three profit numbers are:
      expected: point estimate (matches Opportunity.expected_profit_usd)
      p05: 5th percentile of realized PnL under the uncertainty model
      p95: 95th percentile
    """

    opportunity_id: str
    expected_profit_usd: Decimal
    profit_p05_usd: Decimal
    profit_p95_usd: Decimal


def _percentile(samples: List[float], p: float) -> float:
    if not samples:
        return 0.0
    if len(samples) == 1:
        return samples[0]
    # quantiles() gives n-1 cut points; we want the one nearest p.
    cuts = quantiles(samples, n=100, method="inclusive")
    # cuts has 99 elements indexed 1..99. Grab floor index.
    idx = int(p * 100) - 1
    idx = max(0, min(len(cuts) - 1, idx))
    return cuts[idx]


def model_uncertainty(
    opp: Opportunity, inputs: UncertaintyInputs
) -> OpportunityUncertainty:
    """Combine the opportunity's point estimate with input distributions."""
    capital = opp.capital_at_risk_usd
    size = opp.size_contracts

    # Per-contract: realized = (1 - yes_px - no_px - per_contract_fee) - per_contract_gas
    # Slippage adds to yes_px + no_px effectively (you pay more).
    # Fill rate scales profit linearly (filled 80% → 80% of expected profit).
    # Fee overrun subtracts from profit.

    slip_p95 = _percentile(inputs.slippage_bps_samples, 0.95)
    slip_p05 = _percentile(inputs.slippage_bps_samples, 0.05)
    fill_p05 = _percentile(inputs.fill_rate_samples, 0.05)
    fill_p95 = _percentile(inputs.fill_rate_samples, 0.95)
    fee_over_p95 = _percentile(inputs.fee_overrun_bps_samples, 0.95)
    fee_over_p05 = _percentile(inputs.fee_overrun_bps_samples, 0.05)

    # Worst case (for profit): high slippage, low fill rate, high fee overrun.
    # Best case: low slippage, high fill rate, low fee overrun.

    def _pnl_at(slip_bps: float, fill_rate: float, fee_over_bps: float) -> Decimal:
        # Slippage increases the cost proportionally to gross.
        slip_factor = Decimal(1) + Decimal(slip_bps) / Decimal(10000)
        fee_over = opp.gross_cost_usd * Decimal(fee_over_bps) / Decimal(10000)
        adjusted_cost = opp.gross_cost_usd * slip_factor + opp.fee_cost_usd + fee_over + opp.gas_cost_usd
        payout = size * Decimal(1) * Decimal(str(fill_rate))
        # Capital adjusts with actual fill size too.
        actual_cost = adjusted_cost * Decimal(str(fill_rate))
        return payout - actual_cost

    worst = _pnl_at(slip_p95, fill_p05, fee_over_p95)
    best = _pnl_at(slip_p05, fill_p95, fee_over_p05)

    # Ensure ordering — best >= expected >= worst in healthy inputs.
    p05 = min(worst, best)
    p95 = max(worst, best)

    return OpportunityUncertainty(
        opportunity_id=opp.opportunity_id,
        expected_profit_usd=opp.expected_profit_usd,
        profit_p05_usd=p05,
        profit_p95_usd=p95,
    )


def in_confidence_interval(
    uncertainty: OpportunityUncertainty, realized_pnl_usd: Decimal
) -> bool:
    """True if live PnL fell within the 90% CI (p05..p95)."""
    return (
        uncertainty.profit_p05_usd <= realized_pnl_usd <= uncertainty.profit_p95_usd
    )


def update_inputs_from_paired(
    current: UncertaintyInputs,
    new_slippage_bps: List[float],
    new_fill_rates: List[float],
    new_fee_overrun_bps: List[float],
    retain_samples: int = 200,
) -> UncertaintyInputs:
    """Produce new UncertaintyInputs by appending observed samples and trimming.

    Bootstrap window: we keep the most recent `retain_samples` observations per
    dimension so the model tracks regime changes without being overwhelmed by
    stale early data.
    """
    def _append_trim(old: List[float], new: List[float]) -> List[float]:
        combined = old + new
        return combined[-retain_samples:]

    return UncertaintyInputs(
        slippage_bps_samples=_append_trim(current.slippage_bps_samples, new_slippage_bps),
        fill_rate_samples=_append_trim(current.fill_rate_samples, new_fill_rates),
        fee_overrun_bps_samples=_append_trim(
            current.fee_overrun_bps_samples, new_fee_overrun_bps
        ),
    )
