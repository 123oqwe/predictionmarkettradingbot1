# Expansion Proposal: [name]

Before writing any code for an expansion, fill this out. All fields required.

## Binding constraint

What is currently limiting your system? (Pick ONE — diagnosis must precede action.)

- [ ] Capital (edge exists, capital underused)
- [ ] Edge (opportunities saturating daily)
- [ ] Latency (opportunities vanish before fill)
- [ ] Attention (too much manual intervention)

Evidence from `scripts/capacity_report.py --window-days 30`:

```
(paste the diagnosis section)
```

## Hypothesis

This expansion will generate $X/month of additional edge, addressing the
[constraint] by [mechanism].

## Backtest evidence

Over the last 30 days of historical Parquet data:

- Opportunities detected under this expansion: N
- Estimated PnL under realistic fill: $X
- Estimated annualized on capital deployed: Y%
- Sharpe (per-trade, 365-annualized): Z

Backtest command:
```
(paste command used)
```

## Cost

- Dev time: N weeks
- New infrastructure: $X/month
- LLM/API fees: $Y/month

## Risk to existing system

- [ ] None (new module, no shared state)
- [ ] Isolated (shares DB/config only)
- [ ] Shared code paths (list them)

If shared code paths: explain why the refactor is safe, and note which
regression tests cover it.

## Success metric

(Specific and measurable. Vague hypotheses produce vague results.)

e.g., "Average weekly risk-adjusted return increases from X% to Y% with
statistical significance at p < 0.1 over 4 weeks of live running."

## Failure signals

(Observable warning signs during the 2-week live trial.)

- [ ] Existing strategy PnL drops by > N%
- [ ] Shared DB writes start failing
- [ ] Live calibration divergence grows
- [ ] Kill switch trips against the new strategy
- [ ] (custom)

## Exit plan

If after 4 weeks of live running the hypothesis hasn't been met:

- [ ] Disable via `touch /tmp/arb_agent_flags/disable_<name>.flag`
- [ ] Remove module (git revert + regression test run)
- [ ] Iterate once on parameters, then decide
- [ ] (other)

## Feature flag

This expansion ships off-by-default. Flag name: `<strategy_name>`.
Enable via: `python scripts/enable_flag.py <strategy_name> --hours 48` or
by editing `config.yaml`.

## Commitment

Rereading this document in 4 weeks, does the evidence support continuing?

- YES, promote to default-on
- NO, disable and post-mortem

Do NOT change this answer retroactively.

## Provenance

- Author: ____
- Date: YYYY-MM-DD
- Git commit: `<hash>`
- Config hash: `<hash>`
- Backtest window: YYYY-MM-DD to YYYY-MM-DD
