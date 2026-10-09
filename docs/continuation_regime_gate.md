# Why 10 of 13 correct predictions did not repeat — $0 research

**This is a historical SPY underlying directional proxy audit, NOT an
executable SPY 0DTE options backtest.** No market-data purchases, no broker
execution changes, no implied profit and no account return metrics.

## Falsifiable question

The fixed `confirmed_trend_continuation` rule, which waits three minutes
after a **15-prior-close breakout**, showed **10 of 13 correct direction**
forecasts in Aug 3–Sep 4, 2026 and **3 of 7** in the later Sep 8–Oct 6,
2026 diagnostic. Both periods were previously inspected in this project.
Is the apparent drop meaningful, is a small outlier driving the results,
and does a strict rule using **only preceding completed sessions** improve
out-of-period behavior?

We have locked the checks below **before** inspecting this run's feature
strata and day-by-day sequence. Feature differences are diagnostic; they
**do not establish a new winning trading rule**.

### Descriptive cohort audit

For the static continuation rule, report **all individual events**
(including losing events) in both historical periods, with
timestamp, call/put direction, contemporaneous signal volume ratio,
historical 30-minute signed trend, historical close-path efficiency,
confirmed close's signed distance from previous 15-close boundary,
its signed three-minute follow-through and next-observed-minute
10-minute signed SPY bps.

For each development, validation, and later already-studied diagnostic
period, report: n, successes, exact direction-hit fraction, Wilson 95%
binomial descriptive hit interval, mean and median signed SPY bps
after a flat hypothetical **2bp** friction adjustment, **5bp**
sensitivity, best/worst trade, and minimum/maximum leave-one-event-out
mean net underlying bps.

Compare 10/13 and 3/7 with a two-sided Fisher exact contingency table.
**This small-sample calculation assumes independent Bernoulli
observations (not strictly true for serial market sessions), and all
datasets have already been inspected.** Do not claim Fisher p proves
the strategies are different, equivalent or profitable.

Six *predeclared exploratory* buckets for **post hoc** explanation,
NOT extra strategy families: original direction (CALL vs PUT),
signal clock (10–11, 11–13:30, 13:30–15:25 ET), source-minute
volume ratio (1.25–1.75 vs >=1.75), original aligned 30-minute
trend (8–15 vs >=15 bps), 15-close price-path efficiency
(.35–.6 vs >=.6), confirmed distance past the old 15-close boundary
(0–4 vs 4–8 bps). Flag all cells with fewer than 10 events.

### One mechanical **prior-session-only** dynamic abstention gate

Predeclare ONE unoptimized, fail-closed safety gate applied **on top
of** the existing frozen confirmed continuation signals. For each new
historical session D before the market opens, use the immediately
previous **30 fully completed trading sessions**, and only their
*already-completed* confirmed continuation 10-minute proxy outcomes.

Enable the *underlying directional research signal* on session D only
when **ALL** hold:
- a full 30 preceding sessions exist,
- at least 8 confirmed continuation events were observed among them,
- at least 60% correctly forecasted the 10-minute SPY direction,
- the trailing mean 10-minute signed SPY price move minus the 2bp
  hypothetical hurdle is **strictly positive**.

Otherwise skip all continuation signals that day. Regardless of
whether that gate is open, end-of-day event histories are appended,
ensuring later days can learn from observed outcomes. **Never allow
the next day's gate to see current-session labels before close.**

This is an exploratory *walk-forward replay on known historical data*,
**not a sealed holdout**. It must show signed SPY net bps *per period
session including no-trade dates*, trades kept versus skipped, 95%
seeded 5-day circular block bootstrap distribution of daily net
bps, and the always-abstain benchmark at **zero bps**. Taking fewer
negative trades can superficially beat a losing baseline yet fail
against zero.

### No options P&L

Prior repo canonical files do not preserve real observed event-time
SPY 0DTE option quotes, transaction-level SPY VWAP, full OHLCV bars or
bid/ask size. Even if a signal direction hits, the entire 0DTE premium
may lose due to theta/IV/liquidity. Do not convert +bps into account
returns, real-money trades, maximum account drawdown, or claims of a
repeatable +100% per week. Train/test timestamps were previously
inspected, so no tuning on the same history for a promotion.

## Reproducible commands

```bash
python -m pytest -q tests/test_continuation_regime_gate.py
python -m engine.continuation_regime_gate \
  --data-dir /data/research \
  --output-dir /data/research/continuation_regime_gate
```

Use isolated Railway service `spy-0dte-research` with
`RESEARCH_MODE=continuationregimegate`. Outputs on persistent volume:

- `summary.json`: full periods, confidence measures, causal gate and
  zero-option-P&L declaration.
- `static_13_and_7_and_other_signals_NOT_OPTIONS_TRADES.csv`:
  individual signal features and outcomes.
- `FEATURES_POSTHOC_NOT_VALIDATED_STRATEGIES.csv`: labeled tiny
  strata, **descriptive only**.
- `PAST_30_SESSIONS_gate_daily_decisions.csv`: actual day-by-day
  no-peek gate state, sample size, previous accuracy and signal counts.

The app and live order executor remain unchanged.


## Completed historical research results — 2026-10-09

Isolated Railway worker deployment
`d0ee1092-dc39-44f9-bc36-576079abf337`
read **128 SPY sessions / 49,792 archived 1-minute snapshots**,
saved individual signal, prior-day gating and diagnostic-strata CSVs
to its existing research volume, and made **$0 in new historical
market-data purchases**. Historical periods are previously inspected,
NOT a new sealed holdout.

### All winners, including those too small to overcome hypothetical friction

| Period | Confirmed events | Correct SPY direction | Positive after hypothetical 2bps SPY hurdle | Mean signed bps AFTER 2bps | Mean signed bps AFTER 5bps |
| --- | ---: | ---: | ---: | ---: | ---: |
| Development Apr–Jul (82 days) | 31 | 19 | — | **−0.9534** | −3.9534 |
| Validation Aug 3–Sep 4 (25 days) | **13** | **10** | **7** | **+1.0317** | **−1.9683** |
| Prior-studied diagnostic Sep 8–Oct 6 (21 days) | **7** | **3** | **2** | **−3.8674** | **−6.8674** |

The “positive after 2bps” counts are from the full 20-item
per-signal ledger and do not mean option contracts were profitable.
In validation, 3 apparently correct-direction trades moved SPY
**less than 2bps**, so only **7/13** cleared even the very mild
hypothetical hurdle. Later only **2/7** cleared 2bps. Genuine 0DTE
option spreads and time decay can cost far more.

The **Wilson 95% descriptive intervals** for the correct-direction
probability are **49.7%–91.8%** on the 10/13 cohort and
**15.8%–75.0%** on 3/7; both extremely wide and overlapping.
A conventional Fisher two-sided exact count comparison yields
**p=0.17358**, which is inconclusive. Serial market outcomes and
reuse of known dates further weaken that p-value's assumptions.
It does NOT show the difference is explained by market regimes
rather than sampling variance.

**Outlier sensitivity:** the validation mean after a 2bps proxy
remains positive with any one event deleted (+0.4837 to +1.5310
bps), but with a hypothetical 5bps hurdle its mean is negative.
The later cohort stays negative if any one event is deleted
(−4.8893 to −2.8972 bps). No option profit claim follows.

### Fixed 30-prior-complete-session adaptive gate

The gate was prespecified before these results, with a 30-session
lookback, minimum 8 previous confirmations, previous hit rate >=60%,
and previous mean SPY signed bps after 2bps >0. It is computed at the
start of the current session and cannot read that day's outcomes.

| Period | Static confirmations | Days gate OPEN | Gate trades actually allowed | Gate correct directions | Mean signed SPY bps/session AFTER 2bps (no-trade days zero) |
| --- | ---: | ---: | ---: | ---: | ---: |
| Apr–Jul development | 31 | 0 | 0 | 0 | 0.0000 |
| Aug–Sep validation | 13 | 5 | **5** | **4** | **−0.0096** |
| Sep–Oct prior-studied diagnostic | 7 | 8 | **3** | **0** | **−1.2066** |

The gate had a **4/5 apparent directional hit rate** in validation
yet its overall hypothetical signed SPY bps were still negative.
In the later diagnostic, **all three allowed signals were wrong**:
September 11 and two on October 6. The 5-trading-session-block
bootstrap validation CI of daily signed SPY bps after hypothetical
2bps was **−0.1949 to +0.1661**, overlapping zero.
The later CI was **−2.9554 to 0.0000**, with no demonstrated
absolute improvement over never trading.

### Why the 13 looked good (descriptive, not new model selection)

The 13 validation signals included **8 CALL and 5 PUT** forecasts;
10 correctly forecast direction. In the next seven, CALLs were
right on **3 of 4**, PUTs on **0 of 3**. Those subgroups are tiny;
the difference is not enough to establish “disable puts” or any
other new rule. All six feature breakdowns have fewer than ten
observations per subgroup (except none); their attractive conditional
ratios are **post hoc** and must NOT be tuned and then claimed
validated on these same dates.

Even the strict trailing historical evidence gate lagged the regime
change and authorized losing signals. **Reducing signals can create a
high-looking accuracy from a small sample but is not automatically a
repeatable economic edge**. Even in validation only 7 of 13 forecasts
cleared a hypothetical tiny underlying hurdle, without options costs.

**Research decision: reject live promotion, reject +100%-weekly-profit
claims, and do not spend money on additional data without user approval.**
For further reliable testing, log future sessions chronologically,
freeze rules before reviewing the outcome, and eventually obtain
fully timestamped option bid/ask event data to evaluate actual
premium P&L, fees, settled cash and drawdown. Until then zero-trade
is superior to these negative historical proxies.
