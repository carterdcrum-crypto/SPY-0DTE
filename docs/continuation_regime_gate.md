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
