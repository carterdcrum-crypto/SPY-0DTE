# $0 causal breakout failure/confirmation router

**Research-only experiment — predeclared before Railway historical run.**

## Hypothesis

Is a market-structure/price-action choice after **three genuinely observed
completed SPY minutes following a breakout** more consistent than automatically
continuing the breakout at the **same delayed entry time**?

Unlike the earlier post-entry snapback diagnostics, this router explicitly
**waits for the confirming/failing three 1-minute closes before deciding**.
It never enters at the original breakout and then pretends a future snapback
was already known. No broker integration or trading account is modified.

The existing 128-session licensed data stores SPY 1-minute **close** and
volume_ratio, but not genuine high/low opening-range bars, transaction VWAP,
event timestamped options chain or bid/ask liquidity. This tests only signed
SPY *underlying* price direction, not executable SPY 0DTE option profit.

## Timing, as-of constraints, and identical opportunity set

Start with exactly the established, nonoverlapping
`close_breakout_baseline` 15-prior-**close** breakout events from
`engine.free_signal_screen.screen_session`. For event minute `i`:

- Initial signal known at **end of bar i**.
- Observe completed bars **i+1, i+2, i+3** to determine clean continuation
  versus snapback/rejection. Confirmation is known at end of **i+3**.
- Accepted hypothetical entry is *close of next completed bar i+4*.
  This is an observed-next-bar forecast proxy, not an actual fill.
- Exit proxy is close of **i+14** (10 minutes after entry).
- Require 30 prior complete 1m bars through exit, strict 60-second
  continuity across the entire interval, positive observed prices.
- Original baseline was already locked against overlapping 10-minute
  events, and the router keeps that original clock. All four policies
  share **the same possible events and i+4 entry time**.
- Never use outcomes at i+4 through i+14 to decide eligibility.
  Those are **only** for later scoring and signed-return diagnostics.

All thresholds and four alternatives below are **frozen**, no grid tuning:

| Fixed policy | Decision at the end of third confirmation minute |
| --- | --- |
| `delayed_baseline_all` | Buy original breakout direction for **every** eligible event, but at the *same delayed* i+4 proxy |
| `confirmed_trend_continuation` | Continue only if all of: initial volume_ratio >=1.25; original-direction 30-min trend >=8 bps; 15-close efficiency >=0.35; current confirmed close still outside original 15-close boundary by 0–8 bps; 3-min change in breakout direction nonnegative; **no snapback within any of the 3 observed closes** |
| `confirmed_failed_break_fade` | Reverse only if by confirmation SPY has closed >=2 bps **back inside** the original close boundary, changed >=2 bps **against** the breakout in the three-minute observation period, and prior breakout-direction 30-min trend is <8 bps |
| `conditional_router` | Continue on continuation, otherwise reverse on failed-break fade, otherwise **abstain**; mutually exclusive conditions |

All formulas use observed signed price bps; "trend" and "efficiency"
here are **close-based proxies**, not true VWAP, market depth or order flow.
Any entry into CALL/PUT is **directional labeling only**. A real
options quote can easily turn a correct SPY forecast into a losing
trade due to volatility, bid-ask spread, theta, fees and fills.

## Predeclared evaluation and no-cherry-picking rules

Chronological periods come from `free_signal_screen.PERIODS`:

- Apr 1–Jul 31, 2026: research development.
- Aug 3–Sep 4, 2026: validation **previously touched by prior
  experiments**, not truly sealed.
- Sep 8–Oct 6, 2026: **previously inspected diagnostic**, not untouched.

Do NOT promote any of these dates as a newly untouched holdout.
Do NOT pick the best regime and ignore the losses in the other two.

Report for each period/policy: original candidate count, accepted and
abstained events, directional accuracy, mean signed SPY bps per accepted
event, and mean/total signed SPY bps **after subtracting a hypothetical
2-bps underlying friction proxy per accepted event**. Report mean signed
SPY bps per session **including no-trade days** (abstentions earn zero),
worst eligible ISO week, fraction of positive weeks, and *additive*
cumulative bps peak-to-trough drawdown **NOT account drawdown**.

Bootstrap full market-day net bps with 1,000 seeded, paired 5-session
circular blocks across baseline and each policy; intervals are
descriptive and cannot cure prior inspection/selection.

Critical controls:
- `delayed_baseline_all` is the proper **same-clock** comparison, not
  the original immediate-entry baseline.
- `confirmed_failed_break_fade` direction is explicitly opposite the
  initial breakout; evaluate its results on **the same timestamps**.
- `conditional_router` can abstain all day; zero is the fair outside
  alternative when every costly-entry strategy loses.
- A filter improving return *relative to a negative baseline* does
  NOT demonstrate positive returns. A strategy with a 1–2 week lucky
  period is not a repeatable edge.

## Run with existing research history (no paid data)

```bash
python -m pip install -e '.[dev]'
python -m pytest -q tests/test_causal_breakout_router.py
python -m engine.causal_breakout_router \
  --data-dir /data/research \
  --output-dir /data/research/causal_breakout_router
```

On isolated Railway service `spy-0dte-research`, set
`RESEARCH_MODE=causalrouter`.
Outputs saved to the existing persistent research volume:

- `report.json` — every policy and period, bootstrap and all losses.
- `baseline_delayed_confirmation_opportunities.csv` — all original
  same-clock opportunities and **causal** confirmation features plus
  future-only labeled result.
- `accepted_SPY_direction_proxy_NOT_option_trades.csv` —
  one row for each accepted forecast/action, clearly **NOT** option
  execution or P&L.

No new vendor/API purchase, no Webull order or live change. A claim of
+100% account return a week still requires **future unseen**, realistic
historical option chain execution replay and extended paper trading.


## Actual Railway execution — October 8, 2026

Research-only Railway deployment `386f8331-0192-4284-a754-c1f564a64fe1`
ran successfully against **128 sessions / 49,792 archived SPY
one-minute snapshots**. It evaluated **2,473 nonoverlapping original
breakout opportunities**, created the JSON and CSV reports under
`/data/research/causal_breakout_router/`, and made **zero new
market-data purchases or broker orders**. The Python test workflow
passed.

### Results: identical 3-minute-delayed entry clock, next-bar spot proxy

Below each accepted forecast is scored as the signed **SPY
underlying** basis-point change over the ten minute proxy horizon,
**minus a hypothetical flat 2-bp hurdle**; NOT option premium P&L,
account returns or real execution costs. An abstention earns zero.
This is especially important when filtering away a losing strategy.

| Period | Fixed strategy | Accepted | Direction correct | Mean *after 2bp* per event | Mean *after 2bp* per session incl. abstentions |
| --- | --- | ---: | ---: | ---: | ---: |
| Apr–Jul development, 82 days | Delayed all-breakouts baseline | 1,582 | 51.1% | −1.9617 bps | −37.8467 bps |
| Development | Confirmed continuation | 31 | 61.3% | −0.9534 | −0.3604 |
| Development | Observed failed-break fade | 158 | 48.1% | −1.5666 | −3.0186 |
| Development | Conditional router | 189 | 50.3% | −1.4660 | −3.3791 |
| Aug–Sep validation, 25 days | Delayed baseline | 497 | 53.9% | −1.7298 | −34.3881 |
| Validation | **Confirmed continuation** | **13** | **76.9%** | **+1.0317** | **+0.5365** |
| Validation | Observed failed-break fade | 39 | 46.2% | −1.4146 | −2.2068 |
| Validation | Conditional router | 52 | 53.8% | −0.8030 | −1.6703 |
| Sep–Oct previously studied diagnostic, 21 days | Delayed baseline | 394 | 47.2% | −2.1314 | −39.9898 |
| Previously studied | Confirmed continuation | **7** | **42.9%** | **−3.8674** | **−1.2891** |
| Previously studied | Observed failed-break fade | 47 | 55.3% | −0.5629 | −1.2598 |
| Previously studied | Conditional router | 54 | 53.7% | −0.9912 | −2.5489 |

### Falsifiable readout

**Confirmed continuation** looked promising in Aug–Sep, with
**10 correct directions out of 13**, but was **3 out of 7** later.
That is far too few events and a clear cross-period reversal; it
**does not survive the later regime**.

Its paired five-session block 95% interval for **daily bps after
the hypothetical 2bp hurdle** was
**−0.5287 to +1.6641** in validation (includes zero), then
**−2.7453 to −0.1319** in the later diagnostic (entirely negative).

**Failed-break fade** was negative during development, validation,
and the later diagnostic, despite its confirmation using only events
observable **before** delayed entry.

**Conditional router** was negative in all three periods. No combined
ruleset reliably turns these 15-prior-close-breakout forecasts into
positive signed SPY bps after the hypothetical hurdle.

The paired improvement over the delayed all-breakouts baseline is
large and positive in each period, but that baseline was deeply
negative; **taking fewer costly signals often improves results without
generating any absolute edge**.

**Decision: none of these four is eligible for live 0DTE trading.**
Do not tune dozens of thresholds after seeing the above metrics
and call the same days untouched validation. An independent future
SPY **and timestamped option event-quote** study is needed to
measure plausible profit/drawdown or the 100%-weekly hypothesis.
