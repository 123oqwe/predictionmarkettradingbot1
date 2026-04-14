# Phase 3 Decision — YYYY-MM-DD

**Duration:** N live days at Gate 3
**Trades:** N
**Capital deployed:** $XXX

## Headline numbers

| Metric | Value |
|---|---|
| Gross PnL | $X |
| After-tax PnL | $X |
| Annualized net | X% |
| Risk-free + premium benchmark | X% |
| **Excess over benchmark** | X% |
| Calibration statistic (target 85%) | X/Y = Z% |
| Unexplained divergence count | N |

## Divergence sources identified

- [ ] Slippage (higher than model): N trades, explanation
- [ ] Fee tier surprise: N trades, explanation
- [ ] Resolution timing: N trades, explanation
- [ ] Unexplained: N trades ← **must be 0 before advancing**

## Fixes applied to paper model

- [ ] Slippage distribution refit on live data (retain_samples=200)
- [ ] Fee overrun distribution refit
- [ ] Fill-rate distribution refit
- [ ] Gate-3 re-verification after fixes: coverage X%

## Decision

Pick ONE, do not fudge:

- [ ] **SCALE → Phase 4** — calibration ≥ 85%, after-tax excess > 0, zero unexplained
- [ ] **HOLD** — calibration 75–85% OR excess marginal OR a few unexplained.
      Run another 2 weeks at current gate with paper-model updates.
- [ ] **REDESIGN** — calibration < 75% OR after-tax net negative OR systemic
      unexplained issues. Back to Phase 2.5: find what the backtest missed,
      re-run Phase 3 from scratch.
- [ ] **STOP** — can't explain divergence after 4+ weeks, OR after-tax excess
      is structurally negative. Edge isn't real. Infrastructure stays valuable.

## Reasoning

(write 3-5 sentences tying the numbers above to the decision. No vibes.)

## Commitment

Reread this document in 30 days. If the decision was SCALE but 30-day
performance regressed, REDESIGN retrospectively — do not advance "because
we're already committed".

## Provenance

- Git commit at decision time: `<hash>`
- Config hash: `<hash>`
- Event map hash: `<hash>`
- Decision made by: `<name>`
- Reviewed by: `<name or "none, self-reviewed">`
