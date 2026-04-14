# Phase 6 — Productization (Optional)

**Duration:** Months to years, or never.
**Goal:** Decide whether this becomes a product other people use, stays yours alone, or ends.
**Prerequisite:** Phase 5 running consistently profitable. You have the itch, or the money, or the boredom, to change something.

---

## The core tension

Phase 6 is not a technical phase. It's a life phase.

Up through Phase 5, every decision has been technical-with-financial-consequences: does the math work, is the architecture right, is the edge real. Phase 6 is the opposite: financial-and-personal-with-technical-consequences. The question isn't "can I build this" — by now you obviously can. The question is "should I, given what it will cost me in time, relationships, legal exposure, and opportunity cost."

Most people skip this framing and jump straight to "let me productize it" because they watched a YouTube video about someone making $20k/month from a trading bot. Then six months later they've spent $15k on legal fees, broken up with a partner who got tired of them working weekends, and made $8k in revenue. **This is not a hypothetical outcome.** It's the median outcome for people who productize side projects without thinking first.

So Phase 6 starts with five honest questions, and you're not allowed to skip them. Only after answering those do you pick a direction.

The second tension is that Phase 6 options vary enormously in how much they resemble Phase 0-5. Running a small SaaS is a very different life from running a small fund, which is a very different life from open-sourcing the code and moving on. Don't pick by default or by momentum.

---

## Five questions to answer before choosing a direction

Write your answers down. Date them. Re-read them in 3 months.

### 1. What is your actual motivation?

Pick one:

- **Money.** You want the strategy to generate more total dollars than it currently does.
- **Autonomy.** You want this to replace your day job, not supplement it.
- **Learning.** You want to learn fund management, or SaaS ops, or open source community management, as a career move.
- **Legacy.** You want the work to exist beyond your personal use.
- **Curiosity.** You're bored and this is the next interesting thing.

These lead to very different right answers. "Money" and "autonomy" both point toward income, but "money" wants profit maximization (fund structure) while "autonomy" wants reliable, recurring income with lower upside (SaaS or signal service). "Learning" wants whichever direction teaches the most relevant thing. "Legacy" and "curiosity" point toward open source.

Most people's real motivation is some mix, but there's always a dominant one. Be honest about yours.

### 2. How much time can you actually give this?

Your Phase 5 system takes ~30 min/day. All Phase 6 directions take significantly more:

- **SaaS:** 15-25 hr/week minimum. Customer support alone eats 5-10 hours if you have any users at all.
- **Signal service:** 10-20 hr/week. Less support than SaaS, but more content creation and marketing.
- **Fund:** 20+ hr/week once operational. 10+ hr/week during setup that produces zero revenue.
- **Open source:** 5-15 hr/week if you're the lead maintainer. 1-2 hr/week if you can recruit co-maintainers.

If you have a demanding day job or young kids, the honest answer is "I don't have 15 hours a week for something new." That's not a failure; it's a constraint that rules out most of these options. A good answer here is either "I can commit X hours" or "I can't, so I'm staying in Phase 5 (or doing open source with low ambition)."

### 3. What's your appetite for legal and regulatory exposure?

Prediction markets are in a weird regulatory space. Running your own strategy privately is low-risk. The moment you take money from others or offer signals/advice, you're potentially in territory covered by CFTC, SEC, state money-transmitter laws, or various gray zones.

- **SaaS where customers run their own bots:** lowest exposure. You're selling software, not advice. But if the software is explicitly a trading bot, regulators may still argue it constitutes providing investment advice. Jurisdiction-dependent.
- **Signal service:** medium. You're effectively providing investment recommendations. May fall under investment adviser registration in some states (federal: $110M AUM threshold; state: much lower, varies).
- **Fund structure:** high. Pooled investor money always gets regulatory attention. Even a small "friends and family" fund typically requires a proper fund formation, operating agreement, possibly adviser registration, and ongoing compliance. Budget $20k+ just to start legally.
- **Open source:** low for the code itself, but **be careful about anything that reads like advice** — even a README that says "this strategy earns 20% annualized" can be problematic if read as a promise.

The question is not "could I get in trouble" — it's "what's my tolerance for the ambiguity and the possibility of a cease-and-desist letter." If that tolerance is zero, fund and signal service are off the table.

### 4. Are you prepared to be a support organization?

When you had one user (yourself), bugs were annoying. With real users:
- Every subtle exchange-API change becomes a P0 that you fix on weekends
- Customers will use the system in ways you didn't expect and blame you when it breaks
- Edge cases you shrugged at in Phase 2 (a trade rejected by the exchange for reasons you don't understand) become 2am phone calls
- You'll spend more time on billing, onboarding, and documentation than on strategy

Running software for other people is a service business. Every successful SaaS founder underestimates this, and so will you. Be honest: do you actually want to be a support organization? Or do you just want the revenue?

### 5. What is your personal ceiling for this strategy?

Prediction market arbitrage has real capacity limits. Realistically, a well-run personal system on current Polymarket + Kalshi volumes might generate $2k-$10k/month in edge, capacity-capped. Scaling to $50k/month of edge requires either a very different strategy (market making, institutional relationships) or entering a much more competitive regime where your individual advantages go away.

Ask yourself: is the ceiling already close? If your Phase 5 system is capturing most of the personally-reachable edge, productization only works if you can **productize the system itself** (SaaS) or **aggregate capital** (fund). The strategy you have isn't going to 10x just because more people use it; the opportunities don't scale.

This is the question most people get wrong. They imagine productization will multiply their edge, when in reality, it changes the business model — the underlying math stays the same.

---

## The four directions

Only after answering the questions above.

### Direction A — SaaS (platform for others to run their own bots)

**What it is:** You package the system into software other people can run. They bring their own API keys, their own capital, their own risk. You sell the software — probably as a hosted service with a monthly fee, possibly as self-hosted with a license.

**Who it's right for:** Technical-leaning users who answered "autonomy" or "learning" to Q1, can commit 15+ hr/week, and genuinely enjoy running software for other people.

**Honest overhead:**
- Multi-tenancy rewrite (user accounts, isolation, per-user state)
- Billing infrastructure (Stripe, invoicing, refunds)
- Onboarding flow that non-developers can follow
- Support infrastructure (ticketing, docs, FAQ, status page)
- Legal: ToS, privacy policy, disclaimers, possibly per-state registration
- Ongoing customer support load
- Security hardening — multi-tenant means a bug can leak one customer's keys to another

**Realistic first-year economics:**
- Revenue: $0-5k/month in year one, growing slowly
- Cost: $5-15k in legal + infra + marketing
- Your time: 20+ hr/week
- Break-even: typically year 2 if ever

**Why this path is harder than it looks:** the users who can productively use a prediction market trading bot overlap heavily with users who can just build their own. Your target market is narrow.

**Why it still makes sense for some people:** if you genuinely love the business side and have a reason to believe you can reach an audience (existing newsletter, community, credibility), the tooling already exists and the marginal cost of scale is low.

### Direction B — Signal service (sell alpha, not software)

**What it is:** You keep running the strategy yourself. You publish signals — "buy this market at this price, here's why" — to paying subscribers.

**Who it's right for:** People who enjoy writing and explaining. Motivation is typically "money" or "legacy."

**Honest overhead:**
- Content creation rhythm (daily? weekly?). Stop writing, users churn.
- Subscription management (Substack, Whop, Gumroad, custom)
- Community management (Discord, Telegram)
- Possible investment adviser registration depending on how you frame things
- Reputation risk — one bad call becomes a public memory

**Key tension:** the signals you publish become public information the moment they're sent. Other subscribers or third parties can front-run you. Your own edge degrades because you're broadcasting it. Successful signal services manage this by having signals that are time-insensitive (longer-horizon calls) or by having enough subscribers that the subscription revenue exceeds the lost edge.

**Realistic economics:**
- $1k-5k/month in year one if you're a decent writer and have any audience
- Can scale to $10-50k/month with a strong reputation, but reaching that takes years
- Your trading profits probably decrease due to broadcast

**The honest downside:** you're trading a quiet $3k/month of arb edge for a noisy $4k/month of subscription revenue plus $2k/month of remaining arb edge. Net positive, but now you have subscribers to manage instead of a peaceful dashboard.

### Direction C — Small fund (manage others' capital)

**What it is:** You run the same strategy, but with pooled investor money. You charge a management fee (typically 1-2%) and/or performance fee (typically 20% of profits).

**Who it's right for:** People who answered "money" or "learning" to Q1, have professional network access to investors, and are willing to take on substantial regulatory and operational overhead.

**Honest overhead:**
- Legal formation: $10-25k minimum ($25-50k for a real setup)
- Ongoing compliance: accountant, auditor, administrator
- Investor relations: quarterly letters, LP calls, occasional in-person meetings
- Increased regulatory exposure — you're a fiduciary now
- Your trading decisions carry legal weight, not just financial
- Lockup periods and redemption terms you need to honor
- The fund structure itself has ongoing costs (fund admin ~$10k/year minimum)

**The capacity problem:** most small prediction-market funds run into the ceiling around $500k-$2M AUM. Beyond that, opportunity capacity can't absorb the capital. So even a "successful" personal fund might plateau at $5-20k/month in net fees to you — not life-changing, and now you have LP obligations.

**When this makes sense:** you've reached the personal-capital ceiling in Phase 5, you have network access to investors who want exposure to this strategy, and you genuinely want to learn fund operations. If any of those three is missing, skip.

**When it especially doesn't make sense:** if the appeal is just "managing other people's money sounds prestigious." It's a service business with legal liability. Prestige fades quickly when an LP emails demanding an explanation for a $10k drawdown.

### Direction D — Open source

**What it is:** You publish the code. Either you remain primary maintainer, or you hand it off.

**Who it's right for:** Answered "legacy" or "curiosity" to Q1. Don't need the money. Actively want other people to build on the work.

**Overhead:**
- Code cleanup for public consumption (removing hardcoded credentials, secrets, personal configs)
- Documentation for the four-layer architecture, extraction schemas, etc.
- Issue/PR management if the project gets traction
- Maintenance expectations from users who may not contribute
- The psychological weirdness of watching others use, adapt, and sometimes monetize your work

**Economics:** Direct revenue: usually zero. Indirect benefits: reputation, portfolio piece, potentially career opportunities (hiring managers at quant firms notice open source trading infrastructure).

**Considerations:**
- **License choice matters.** MIT/Apache lets anyone use it commercially. GPL forces downstream modifications to remain open. Choose deliberately.
- **Disclaimers matter even more.** "This is educational; not investment advice; not financial advice" in large letters. Any reasonable reading of the README as "use this to make money" creates exposure.
- **Edge erosion.** Once the code is public, any edge it contained is now contested by every reader. This is fine if the code is a framework and the edge was in the event map / configurations / calibration (which you keep private), bad if the code itself was the edge.

**A reasonable hybrid:** open-source the framework (the four-layer architecture, the backtest engine, the matcher tooling) while keeping your private event maps, calibration data, and current configurations. You get reputation benefits without completely giving away your working edge.

---

## The tax reality, per direction

Taxes change dramatically once you're doing this for money-that-matters. The numbers below are US-specific and rough; consult a professional.

**Personal trading (Phase 5, no productization):**
- As discussed in Phase 3: Kalshi probably Section 1256 (60/40 long/short blend), Polymarket probably ordinary income, combined federal+state+city marginal ~35-50% in NYC.
- Simple: file on your personal return.

**SaaS revenue:**
- Treated as ordinary business income.
- You can deduct expenses (infra, tools, portion of home office, etc.).
- Self-employment tax (15.3%) unless you form an S-Corp, which has its own setup costs.
- Typically lands at effective ~35-45% in NYC after deductions.

**Signal service revenue:**
- Same as SaaS: ordinary business income, self-employment tax applies.

**Fund management fees and performance fees:**
- Management fees: ordinary income.
- Performance fees (carried interest): historically long-term capital gains treatment if held > 3 years, ordinary income otherwise. Rules have changed and may change again.
- The fund entity itself has pass-through treatment typically; you pay based on allocations.
- **Budget $5-10k/year for tax prep alone** once the fund is real.

**Open source:**
- Usually no tax implications unless you accept donations or sponsorship, in which case treat as business income.

**The cross-jurisdiction problem:** if you're in NYC selling to people in Texas, paying an infra provider in Ireland, running exchanges based in St. Kitts, etc., the tax and compliance complexity increases faster than revenue. Once you have real revenue, hire an actual accountant who specializes in small software or investment businesses. Don't DIY this.

---

## The "stay in Phase 5" option

Listed explicitly because it's a legitimate choice that people skip over due to momentum bias.

You built something that works. It runs hands-off. It makes some money. You have a life.

**You can just keep doing that.**

Staying in Phase 5 means:
- No legal entity formation
- No customer support
- No LP letters
- No release commitments
- No "community management"
- ~30 min/day of maintenance
- All profits are yours
- You still have energy for other things

The implicit message of "Phase 6" in most roadmaps is "you must grow the thing." This is false. A quiet, profitable, hands-off personal trading system is rare and valuable. Growing it turns it into a business that may or may not work and will definitely consume more of your life.

The right answer for many people — maybe most — is:
1. Stay in Phase 5
2. Periodically refresh the strategy (re-verify event maps, check for new edge, update calibration)
3. Use the profits to fund other things you care about
4. Accept the strategy's natural ceiling and don't chase beyond it

If reading this section feels disappointing, check Question 1 from earlier. You may be more motivated by momentum or identity ("I'm the kind of person who builds businesses") than by the underlying thing. Momentum is a bad reason to start a business.

---

## Decision framework

After answering the five questions, your direction usually becomes obvious. If it doesn't, here's a rough decision tree:

```
Q1: motivation?
├── Money → Q5 (ceiling)
│   ├── Ceiling is close → Fund or SaaS (capacity expansion), or stay
│   └── Ceiling is far → Stay in Phase 5, harvest
│
├── Autonomy → Q2 (time)
│   ├── 15+ hr/week → SaaS
│   └── Less → Can't replace day job; stay in Phase 5
│
├── Learning → what do you want to learn?
│   ├── Running software → SaaS
│   ├── Fund ops → Fund
│   └── OSS community → Open source
│
├── Legacy → Open source (hybrid framework-public, edge-private)
│
└── Curiosity → Stay in Phase 5, pick an adjacent project
```

This tree is approximate. Your actual decision will depend on local factors (network, financial runway, partner, kids, job security). The tree is a starting point, not a verdict.

---

## If you're going to do something, here's a safe sequencing

Assuming you've decided to productize, the order of operations that minimizes regret:

### Month 1: Commitment check
- Write your Phase 6 decision document answering all five questions
- Commit to a direction in writing
- Give the document to a trusted friend with permission to push back
- Wait 2 weeks before acting — most impulsive Phase 6 launches die in this window

### Months 2-3: Legal and financial foundation
- Talk to an accountant who specializes in small software or investment businesses
- If needed, talk to a lawyer (fund formation, adviser registration, etc.)
- Set up business entity (LLC minimum, possibly more)
- Get a business bank account separate from personal
- Understand your tax situation before revenue exists

### Months 4-6: Minimum viable version
- Build the smallest possible version of the product
- SaaS: one tier, simple onboarding, single-region hosting
- Signal: one email per week, one price tier
- Fund: friends-and-family soft launch with $25-100k
- Open source: clean repo, readme, disclaimers, nothing else

### Months 6-12: Iterate on real feedback
- Only after you have real users, real subscribers, or real LPs
- Pre-launch assumptions about what matters will be wrong
- Budget 40% of your effort to be "things I didn't know I'd need to do"

### Month 12+: Decide to continue or wind down
- If you're not break-even by month 12, something is off. Don't romanticize losses.
- If you are, the next year is scaling or stabilizing.
- Either way, review against your original Phase 6 decision document. Does the thing you built still match the reason you started?

---

## Failure modes

- **Productizing because Phase 5 got boring.** Boredom is not a business reason. If you're bored, take a break or pick a non-business adjacent project.
- **Underestimating the non-technical work.** Legal, accounting, customer support, marketing — all take real time and real money. Whatever you estimate, multiply by 2.
- **Sticking with the wrong direction due to sunk cost.** Six months in, you realize SaaS is wrong for you. You can switch (or stop). The money and time spent aren't coming back either way; the question is only whether you want to spend more.
- **Letting the product eat the strategy.** Customer support takes all your time. Your Phase 5 system rots. You're making less money from trading than before AND running a business you don't love.
- **Confusing revenue with profit.** $10k/month revenue with $11k/month costs (infra + legal + time) is worse than $3k/month of clean arb profit.
- **Forgetting why you started.** Every 3 months, re-read your decision document and ask if the reasons still hold.

---

## What "done" looks like

Phase 6 doesn't really end in a clean way. Some rough pictures of "done":

- **SaaS that found product-market fit:** 100+ paying customers, ~$10k+ MRR, you've hired at least part-time help. The project has outgrown being a side project.

- **Signal service that's sustainable:** steady subscriber base, writing rhythm is comfortable, revenue + residual trading profit exceeds previous income.

- **Fund that's operating:** LPs are satisfied, returns are meeting expectations, AUM is at your capacity ceiling. You're now running a fund, not building one.

- **Open source project that outlived you:** other contributors, issues being resolved without you, used by people you don't know.

- **You stayed in Phase 5:** system still runs, still profitable, you've moved on to other things while it quietly generates income. This is underrated.

- **You stopped entirely:** you shut it down, took the lessons, did something else. Also underrated. Not every project needs to continue forever.

---

## Final note

The through-line of this entire roadmap has been: **build carefully, measure honestly, decide with data.** Phase 6 is where that discipline has to extend to decisions about your own life, not just your trading system.

Most roadmaps in this category end with "and here's how to make it big." Real advice is different: the thing you built is valuable; the decision about what to do with it should be made with the same care you used building it; and the answer "I'm happy with what I have" is allowed.

Whatever you choose, you already did something most people never do: you built a trading system from first principles, with correct math and real engineering discipline, from scratch. That capability travels. Whether this particular project continues or not, you have it now.

Good luck.
