# Prediction Market Arbitrage Agent — Roadmap

A phased build plan for an autonomous prediction market arbitrage agent, from first line of code to a system you'd trust with real money — and optionally, to a product other people can use.

This roadmap is opinionated about two things most tutorials get wrong:

1. **This is a quantitative trading system, not a web app that happens to trade.** The core math — annualized return, capital allocation, market impact, adverse selection — is treated as first-class, not as an afterthought.
2. **This is a production engineering problem, not a script.** Data layer, strategy layer, and execution layer are separated from day one, because every system that skips this separation eventually rewrites itself in pain.

---

## What this is, and what it isn't

**This is** a realistic, opinionated sequence of steps based on how systems like this actually get built and how they actually fail. It assumes you're smart, you can code, and you want to do this right rather than fast.

**This isn't** a promise of profit. Prediction market arbitrage is a real strategy with real edge, but it's also heavily contested, capacity-constrained, and punishing to people who skip steps. Plausible outcomes at the end of this roadmap include "consistent small positive edge," "infrastructure is great but edge is gone," and "you learned more about quantitative trading than a master's degree would've taught you." All three are legitimate. Only one of them pays rent.

**You will not get rich from this.** Realistic expectation for a well-executed personal system: a few hundred to a few thousand dollars a month, capacity-capped, with real time investment to maintain. If your mental picture involves a six-figure passive income from a bot, close this file and read something else.

---

## How to use this

Read phases in order. Each phase has:

- **A core tension** — the one thing that makes this phase hard, stated up front
- **Explicit prerequisites** — what must be true before you start
- **Tagged tasks** — `[math]`, `[infra]`, `[research]`, `[slog]` — because they deserve different attention
- **Exit criteria** — what must be true before you move on
- **Failure modes** — when to loop back or stop entirely
- **Cost budget** — a monthly ceiling; exceeding it means something's wrong

When you start a phase, open its file, hand it to Claude Code as context, and work through tasks one at a time. Do not batch. Do not skip ahead.

## The phases

| Phase | File | Time | What you're actually doing |
|---|---|---|---|
| 0 | `phase-0-mvp.md` | 2–3 wk | Building the layered architecture and proving the math on one platform |
| 1 | `phase-1-dual-platform.md` | 4–5 wk | Adding Kalshi and reading a LOT of market rules |
| 2 | `phase-2-risk-reliability.md` | 3 wk | The boring phase that separates toys from real systems |
| 2.5 | `phase-2.5-backtest.md` | 2 wk | Historical recording + replay + backtest engine |
| 3 | `phase-3-paper-to-live.md` | 2+4 wk | Finding out if your edge was real or a paper illusion |
| 4 | `phase-4-auto-matching.md` | 4 wk | Structured extraction + code-driven matching, not LLM classification |
| 5 | `phase-5-expansion.md` | Open | Adding one thing at a time, measuring, repeating |
| 6 | `phase-6-productization.md` | Months | Deciding whether this becomes a product, or stays yours |

Note: Phase 2.5 is new since the previous version. It used to be smeared across other phases, which was a mistake — backtesting deserves its own phase because it's the precondition for every decision in Phase 3 onward.

---

## Principles that apply to every phase

### Quantitative principles

1. **Annualized return, not absolute profit.** A 3% opportunity that resolves in 3 days is worth ~60x a 3% opportunity that resolves in 6 months. All thresholds in this system are on annualized return on capital-at-risk, never on absolute profit. If you see a `profit_pct` variable without an `annualized_return` next to it, it's a bug.

2. **Beat risk-free + premium, after tax.** The benchmark is not zero. It's the risk-free rate (US Treasury, ~4%) plus a risk premium (500 bps minimum for this kind of strategy), after your marginal tax rate. If your backtest says 6% annualized, you're underperforming a T-bill after NYC taxes. Know this number before you start.

3. **Capital-at-risk, not notional.** Profit calculations always account for how much capital is actually locked up until resolution, and for how long. Capital efficiency matters as much as edge.

4. **Adverse selection is the default assumption.** If a trade looks too good, ask why it's still there. The answer is usually "because the other side knows something." Build skepticism into the detection loop, not just the documentation.

5. **Market impact is real.** You do not get top-of-book prices on anything but the first contract. Use order book depth, compute size-weighted fill prices, and size your trades so marginal profit stays above threshold.

### Engineering principles

6. **Layered architecture from day one.** Data recording, data serving, strategy, and execution are separate layers with explicit boundaries. Strategy is a pure function. This is non-negotiable — skipping it in Phase 0 costs you months in Phase 5.

7. **Paper first, always.** Never skip paper validation. Not once. Not "just to test."

8. **Deterministic replay.** Any historical moment must be reconstructible from logs. If you can't replay yesterday's state, you can't debug yesterday's bug.

9. **One change at a time.** Parallel changes make attribution impossible, and attribution is how you learn.

10. **Test the math before the plumbing.** Fee and slippage calculations are where real money actually leaks. A bug in your SQLite writer is embarrassing. A bug in your profit calculation is bankruptcy.

11. **Trust nothing.** Not your code. Not the LLM. Not the exchange's API docs. Not your own confidence after a good week. Verify.

12. **Know when to stop.** Every phase has exit criteria. If you don't meet them, loop back. "Advance on hope" is how most trading projects die.

---

## What to expect emotionally (since nobody else tells you this)

- **Phase 0** is intellectually demanding and fun. You're setting up the architecture and you'll feel like you understand the problem better every day.
- **Phase 1** is a slog. You'll spend more time reading market rules than writing code.
- **Phase 2** is boring. You'll want to skip it. Don't.
- **Phase 2.5** is satisfying. Everything you built comes together into something you can actually query.
- **Phase 3** is where most projects die emotionally. You go live, discover paper PnL was 30–60% optimistic, and have to decide if the edge is real. This is normal. It's the phase working correctly.
- **Phase 4** is intellectually fun again.
- **Phase 5** is where it starts feeling like a real thing. 30 minutes a day of tending.
- **Phase 6** is a life decision more than a technical one.

Most people who try this get stuck between Phase 2 and Phase 3. If you make it to Phase 5 with consistent positive risk-adjusted returns, you've already done something rare.

## Honest timeline

Add 50% to every duration in this roadmap. Not because the estimates are wrong, but because the things you'll learn between phases — reading docs, debugging weird exchange behavior, re-running calibrations — don't fit neatly into task lists.

Realistic time from zero to a stable, hands-off Phase 5 system: **4–7 months of focused evening-and-weekend work**, or 2.5–4 months full-time. Anyone telling you faster is either unusually experienced or selling something.

## Cost budget (total, across phases)

Before you start, know roughly what this will cost in cash before it makes cash:

- Infrastructure (APIs, hosting, storage): $20–500/month depending on phase
- LLM costs (Phase 4 onward): $50–200/month
- Trading capital for Phase 3: $200–500
- Accountant or legal (if you reach Phase 6): $2k+

Budget $1–2k total to reach Phase 5. If the strategy doesn't pay that back in 6 months of live running, you've learned a valuable thing and should stop.

## First step

Close this file. Open `phase-0-mvp.md`. Read the whole thing before touching code. Then start on Task 0.1.
