# Phase 5 — Platform and Strategy Expansion

**Duration:** Open-ended. Typically 4–8 weeks per major addition.
**Goal:** Grow the opportunity pool without breaking what works.
**Prerequisite:** Phase 4 complete. System runs hands-off. Consistent positive risk-adjusted returns.

---

## The core tension

You now have a working system. Phase 5 is where many people ruin it.

The temptation is obvious: you've proven the concept, so scale it. Add three platforms, two strategies, rewrite the execution engine, build a dashboard, explore market making. All at once. All now.

**This is how systems that took months to stabilize become broken in a weekend.**

Your system's reliability and your own understanding of it are tightly coupled. When you make one change, you know what you changed. When you make five changes, attribution is impossible — and when something breaks (it will), you don't know which change caused it.

The core tension in Phase 5: breadth versus control. More coverage, more strategies, more edge — versus the rigorous attribution that made Phase 3 calibration work in the first place.

The discipline is: **add one thing at a time, run it for 2 weeks, measure the impact against your Phase 2.5 backtest-derived baseline, then decide what's next.** This phase doesn't have an ending. It has a rhythm. Eventually you'll run out of good ideas, or the marginal edge stops justifying the time, and you'll either move to Phase 6 or call Phase 5 "done" informally.

There's a second tension specific to this phase: **prediction market arbitrage is capacity-constrained.** Unlike equities, where there's always more volume, most opportunities you find cap out at a few hundred to a few thousand dollars of deployable capital before they disappear. Your strategy might earn 40% annualized on $10k but only 8% on $100k because you run out of opportunities to deploy capital against. Before any expansion, ask: is the bottleneck edge quality or capital capacity? The answers are totally different.

---

## Success criteria (per addition)

No single exit criterion for the whole phase. Each addition has its own:

- **Measurable opportunity count increase** in the historical backtest, confirmed in live
- **Net positive impact on Sharpe**, not just on gross PnL
- **No regression in existing strategies** (attribution is how you check)

If an addition can't meet these in 2-4 weeks of live running, roll it back.

---

## The "one at a time" rule, restated

You will be tempted to parallelize. Don't. Here's why:

Imagine you add PredictIt AND a new market-making strategy in the same week. Two weeks later, PnL is up 15%. Which change caused it? Unknown. One might be contributing +25% while the other is -10%, and you can't tell.

Serial additions turn every 2-week window into a clean A/B test against your own previous baseline. The numbers mean something. You learn.

**Hard rule:** no new addition until the previous one has run 2+ weeks in production AND you can state its measured impact in one sentence.

---

## Expansion options

Pick them in order appropriate to your situation. The order below is a rough "what makes sense for most people."

### Option E — Resolution convergence trading (recommended first)

**What:** Near market settlement, prices should converge to 0 or 1. Deviations — "YES at 0.94 with 2 hours to a near-certain resolution" — are exploitable.

**Why first:** Uses existing infrastructure almost entirely. No new platform. No new execution model. New detection rule on data you already have.

**Dev time:** ~2 weeks.

**Risks:**
- The reason for a deviation might be information you don't have
- Liquidity dries up near resolution — orders may not fill
- Manipulation on illiquid markets trying to bait bots

**Verdict:** Easy win, low risk, decent edge. Do it first.

### Option B — Manifold Markets (recommended second)

**What:** Third platform. High market count, uses an AMM model, has real-money markets alongside play-money.

**Why second:** Clean API, lots of markets, good learning environment. Tests your Phase 0 four-layer architecture with a third fetcher — if Layer 3 requires any modification to accommodate Manifold, your Phase 0 work was incomplete, and you'll find out here.

**Dev time:** ~1.5 weeks for integration, more for event matching updates.

**Risks:**
- Play-money markets mixed with real-money — filter aggressively
- AMM pricing model is different from order book — different math for size-weighted fills (no "walk the book" since the price is a continuous function of size)
- Low liquidity on most markets

**Verdict:** Modest profit, significant learning, exercises your architecture.

### Option A — PredictIt

**What:** US political markets with unique coverage.

**Why middle:** Unique markets unavailable elsewhere, persistent mispricings (PredictIt's $850 position cap prevents big traders from arb'ing them), but brutal fees.

**Dev time:** ~2-3 weeks. API is quirky and partly undocumented.

**Risks:**
- $850 per-market cap kills trade sizing
- 10% profit withdrawal fee is devastating for thin arb — many trades that look good are economically dead after this
- 5% deposit withdrawal fee
- API is slow; think minutes not seconds

**Verdict:** Worth it only for intra-PredictIt arb or as a signal source. Cross-platform usually dead after fees.

### Option G — Cross-platform calendar spreads

**What:** Same event at different timescales — "rate cut at next meeting" vs "rate cut by year-end" should have consistent pricing. Deviations are tradable.

**Why:** Builds on existing event matching infrastructure. No new platforms.

**Dev time:** ~3 weeks.

**Risks:**
- Not always truly arbitrage — sometimes the spread reflects real information
- Longer holding periods → capital tied up longer, which lowers annualized return on capital-at-risk (apply Phase 0 math carefully)
- Execution across calendar legs is slower

**Verdict:** Interesting, moderate edge, good exercise in position modeling.

### Option D — Market making (REAL WARNING)

**What:** On low-liquidity markets, post two-sided quotes and earn spread. **Not arbitrage.**

**Real talk:** Market making is a different business entirely, and the previous version of this roadmap was too soft on it. Let me fix that.

Successful market making requires:
1. **Sub-millisecond latency infrastructure** to compete with existing MMs
2. **Adverse selection pricing** — you must price higher when you suspect the counterparty knows more than you, which requires modeling information asymmetry
3. **Inventory risk management** — you're carrying positions, not delta-neutral
4. **Queue position estimation** — where your quote sits in the book determines fill probability
5. **Hedging flow** — unwinding inventory before it goes stale

A solo retail trader running market making **against professional quant firms** is basically donating money. The professionals have faster infra, better models, and dedicated capital. You do not.

**The only scenario where MM makes sense for this project:** markets where professional MMs don't operate at all (typically low-volume political and niche markets), and where you can accept being the only MM in a small market with low volume but high spread. Even then, you're exposed to adverse selection from informed retail.

**Dev time:** 4+ weeks, plus ongoing tuning. But realistically, this is a multi-month commitment to learn the craft.

**Verdict:** **Skip this option unless you already have quant MM experience.** Yes, the profit ceiling is higher than arb. Yes, many successful firms do it. No, that doesn't mean you can. Most people who try lose money. I include this option for completeness, not as a recommendation.

If you still want to pursue it: start by paper-market-making on one illiquid market for 4+ weeks and see if your paper PnL is remotely positive. If yes, proceed cautiously with tiny capital. If no (likely), you've learned what you needed to learn.

### Option C — Drift / Solana-based platforms

**What:** Crypto-native prediction markets on Solana.

**Why later:** Uncorrelated order flow, wider spreads in places — but you're now dealing with wallet security, chain-specific infrastructure, and newer platforms with elevated counterparty risk.

**Dev time:** 3+ weeks, more if you're not in the Solana ecosystem.

**Risks:**
- Wallet/key security becomes a primary concern — a key leak is ruinous in a way that an API key isn't
- Transaction fees and prioritization complexity
- Chain congestion during volatile periods — exactly when you want to execute
- Counterparty risk is higher on newer platforms

**Verdict:** Only if you're already Solana-native. Otherwise, defer indefinitely.

### Option F — Triangular arbitrage

**What:** Three-way mispricings across related but not identical markets.

**Why last:** Rules divergence compounds across three markets, execution across three legs is slower, opportunities are rare.

**Dev time:** 4+ weeks with a graph-based detection engine.

**Verdict:** Academically interesting, practically marginal. Defer unless bored.

---

## Capacity constraint analysis

Before starting any expansion, check which constraint is actually binding:

**Capital constraint:** "I have $10k but my existing strategy only needs $3k because it runs out of opportunities."
- **Diagnosis:** your bottleneck is edge, not capital
- **Right move:** add more edge (new strategy, new platform)
- **Wrong move:** scaling up capital (won't help, will just sit unused)

**Edge constraint:** "I'm saturating available opportunities every day but each one is smaller than I want."
- **Diagnosis:** your bottleneck is trade size, not opportunity count
- **Right move:** either increase per-trade size (if allowed by liquidity) or find higher-capacity platforms
- **Wrong move:** adding more strategies (just dilutes attention)

**Latency constraint:** "I detect opportunities but they're gone before I fill."
- **Diagnosis:** bottleneck is execution speed, not detection
- **Right move:** infrastructure work (colocation, faster API clients, pre-signed transactions on Polygon)
- **Wrong move:** adding platforms (same latency issue will repeat)

**Attention constraint:** "Too much manual intervention, I can't scale my time."
- **Diagnosis:** bottleneck is automation
- **Right move:** Phase 4-style tooling expansion (better matching, better monitoring)
- **Wrong move:** anything that adds operational load

**Diagnose before expanding.** Expansion without diagnosis is how you add complexity without adding edge.

---

## Framework for evaluating any expansion

Before you write any code for an expansion, fill this out:

```markdown
## Expansion proposal: [name]

**Current binding constraint (one of capital/edge/latency/attention):**

**Hypothesis:** This will generate $X/month additional edge, addressing the [constraint] by [mechanism].

**Backtest result:** In Phase 2.5 backtest against the last 30 days of historical data, this would have produced [specific PnL / count / annualized].

**Cost:** Y weeks of dev + $Z in new infrastructure + $W in LLM/API fees.

**Risk to existing system:** [none / isolated / touches shared code paths]

**Exit plan:** If after 4 weeks of live running it hasn't met the hypothesis, I will [disable / iterate once / scrap entirely].

**Success metric:** (specific and measurable) e.g., "average weekly risk-adjusted return increases from X% to Y% with statistical significance at p < 0.1"

**Failure signals:** (observable warning signs) e.g., "shared SQLite writes start failing; live calibration divergence grows; existing strategy PnL drops"
```

If you can't fill every field concretely, you're not ready to start. Vague hypotheses produce vague results you can't learn from.

---

## Infrastructure refactors between expansions

Between expansions, budget time for work you've been deferring:

- **Replace SQLite-based operational state with Postgres** (already done in Phase 3 for transactional data, but possibly incomplete for metrics, kill switch state, event map cache)
- **Performance:** profile the detection loop in production conditions, optimize the hot paths
- **Multi-region:** run closer to exchange infrastructure if latency is the binding constraint
- **Observability:** proper metrics with Grafana or similar, alerting beyond Telegram
- **Disaster recovery:** off-site backups, documented recovery procedures, **actually tested** recovery
- **Upgrade Parquet → DuckDB / ClickHouse** if backtest volume exceeds what plain Parquet can serve quickly
- **Code review by second person:** even just a friend who's technical, to catch bugs and review architectural choices

These aren't sexy and don't increase PnL this week, but they compound over months.

---

## Anti-patterns to actively avoid

**Adding a platform because it exists.** Only add if you have a specific thesis about edge based on the backtest. "More platforms = more opportunities" is not a thesis.

**Adding a strategy because it sounds cool.** Especially market making. If you felt excitement reading Option D, slow down and re-read it. Exciting is not the same as profitable.

**Trusting backtest over live.** Always paper-trade new strategies for 1+ week before live. Always expect live to be 20-40% worse than backtest. Treat backtests as upper bounds, not point estimates.

**Adding before previous addition is profitable.** Proves nothing about your execution; spreads attention thin.

**Neglecting the core system.** Maintenance is where real money gets lost. Every week, touch the Phase 0-4 code briefly to confirm it's still healthy.

**Building for imagined scale.** You're not an HFT firm. Premature optimization for throughput you'll never use wastes time you could spend finding actual edge.

**Scope creep in a single expansion.** "I'm adding PredictIt and while I'm at it let me rewrite the detection loop." No. One at a time.

**Chasing variance.** A single good week doesn't mean a strategy is good. A single bad week doesn't mean it's bad. Make decisions on 2+ week data with statistical awareness.

---

## Capacity-based stopping signal

A hidden trap: you keep adding strategies, your system gets more complex, but **absolute PnL doesn't grow**. This means you're hitting fundamental capacity limits on prediction market arb.

Watch for:
- Diminishing returns from new additions — each one produces less marginal PnL than the last
- Total capital deployed not growing despite new strategies
- Opportunity rejection rate from "already filled another similar trade" growing

When this happens, **stop expanding**. You've found the practical ceiling for personal-scale arb on your chosen platforms. Options:

1. **Accept the ceiling** — harvest what you have, don't add complexity
2. **Scale capital** via Phase 6 Direction C (fund structure) — which has its own capacity limits
3. **Move to a different problem** — market making (dangerous), other crypto-native strategies, quant job

None of these are bad options, but pretending the ceiling isn't there is.

---

## Cost budget

Phase 5 should cost **< $500/month**:
- Cloud hosting if you've moved off your laptop: $50-200/month
- Storage for growing historical data: $20-50/month
- LLM costs for expanded matching: $100-200/month
- Exchange fees: variable with live trading volume
- Potential colocation if latency is critical: $100-300/month

If you're spending > $500/month and the strategy isn't clearing that plus risk-free + premium plus tax, you're running a hobby, not a business.

---

## Exit criteria → Phase 6 (there isn't one, really)

Phase 5 doesn't have a clean exit. Transition to Phase 6 when:

- [ ] System runs hands-off with ~30 min/day maintenance
- [ ] 2+ months of profitable risk-adjusted returns across multiple strategies
- [ ] Further expansion feels marginal
- [ ] You have the itch to either productize or harvest

The last point is the real trigger. Phase 6 is a different kind of work — business decisions, not technical ones. Start it only if you want that work.

**Staying in Phase 5 indefinitely is completely valid.** A quiet, profitable, hands-off personal trading system is already rare and valuable. You don't have to turn it into a product. Don't feel obligated.
