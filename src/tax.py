"""After-tax PnL calculator.

ILLUSTRATIVE — NOT TAX ADVICE. The numbers are doc-suggested defaults. An
accountant familiar with small trading businesses + Section 1256 treatment
must confirm before filing.

Defaults implemented per doc (phase-3-paper-to-live.md):
  - Kalshi: Section 1256 treatment → 60% long-term cap gains + 40% short-term
    cap gains, regardless of actual holding period.
  - Polymarket: ordinary income (most likely treatment, offshore exchange
    settling in crypto).

Output includes the benchmark comparison: after-tax net vs.
(risk_free_rate + risk_premium_required). Negative excess = you're losing to
T-bills after tax — critical signal.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Dict


@dataclass(frozen=True)
class TaxConfig:
    federal_marginal: Decimal
    state: Decimal
    city: Decimal
    kalshi_longterm_rate: Decimal  # usually ~15-20% federal for long-term
    kalshi_shortterm_weight: Decimal  # 0.40 for 1256
    kalshi_longterm_weight: Decimal   # 0.60 for 1256
    risk_free_rate: Decimal
    risk_premium_required: Decimal


def default_us_nyc() -> TaxConfig:
    """NYC resident default. Adjust via load from config.yaml if needed."""
    return TaxConfig(
        federal_marginal=Decimal("0.35"),
        state=Decimal("0.065"),
        city=Decimal("0.04"),
        kalshi_longterm_rate=Decimal("0.15"),
        kalshi_shortterm_weight=Decimal("0.40"),
        kalshi_longterm_weight=Decimal("0.60"),
        risk_free_rate=Decimal("0.04"),
        risk_premium_required=Decimal("0.05"),
    )


@dataclass
class AfterTaxReport:
    gross_pnl: Decimal
    polymarket_pnl: Decimal
    kalshi_pnl: Decimal
    estimated_tax_polymarket: Decimal
    estimated_tax_kalshi: Decimal
    net_pnl: Decimal
    effective_tax_rate: Decimal
    annualized_net: Decimal
    benchmark_rate: Decimal
    excess_over_benchmark: Decimal

    def to_dict(self) -> Dict[str, str]:
        return {k: str(v) for k, v in self.__dict__.items()}


def _ordinary_marginal(cfg: TaxConfig) -> Decimal:
    return cfg.federal_marginal + cfg.state + cfg.city


def compute_after_tax(
    *,
    polymarket_pnl_usd: Decimal,
    kalshi_pnl_usd: Decimal,
    period_days: int,
    cfg: TaxConfig,
    capital_deployed_usd: Decimal,
) -> AfterTaxReport:
    """Compute after-tax PnL + benchmark comparison."""
    ordinary = _ordinary_marginal(cfg)
    # Polymarket: ordinary income. Taxed on positive PnL only (losses carry
    # over for separate accounting in real tax prep; we keep it simple here).
    poly_tax = max(Decimal(0), polymarket_pnl_usd) * ordinary

    # Kalshi: 60/40. Long-term portion taxed at longterm rate (federal only,
    # not NY state+city — state may also give lower rate but varies).
    kalshi_lt = max(Decimal(0), kalshi_pnl_usd) * cfg.kalshi_longterm_weight * cfg.kalshi_longterm_rate
    kalshi_st = max(Decimal(0), kalshi_pnl_usd) * cfg.kalshi_shortterm_weight * ordinary
    kalshi_tax = kalshi_lt + kalshi_st

    gross = polymarket_pnl_usd + kalshi_pnl_usd
    total_tax = poly_tax + kalshi_tax
    net = gross - total_tax
    effective = (total_tax / gross) if gross > 0 else Decimal(0)

    # Annualize the net return.
    if capital_deployed_usd > 0 and period_days > 0:
        net_pct = net / capital_deployed_usd
        ann = (Decimal(1) + net_pct) ** (Decimal(365) / Decimal(period_days)) - Decimal(1)
    else:
        ann = Decimal(0)

    benchmark = cfg.risk_free_rate + cfg.risk_premium_required
    excess = ann - benchmark

    return AfterTaxReport(
        gross_pnl=gross,
        polymarket_pnl=polymarket_pnl_usd,
        kalshi_pnl=kalshi_pnl_usd,
        estimated_tax_polymarket=poly_tax,
        estimated_tax_kalshi=kalshi_tax,
        net_pnl=net,
        effective_tax_rate=effective,
        annualized_net=ann,
        benchmark_rate=benchmark,
        excess_over_benchmark=excess,
    )
