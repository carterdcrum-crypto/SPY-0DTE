# Free SPY close/volume signal screening — exploratory only

**Date:** October 8, 2026. This is a $0 market-data-increment study
over the **already stored** 128 SPY trading-session archives.

## Do not confuse this with executable SPY 0DTE options backtesting

The archive holds Databento SPY minute **close** and calculated minute
volume ratios, plus *sampled* option top-of-book bids and asks. It
**does not preserve** the complete SPY OHLCV bars necessary for a
true 15-minute opening **high/low** breakout, nor transaction-weighted
VWAP, nor event-time observed option quotes and market size.

**The new screening study deliberately NEVER computes any options fill,
account return, premium return, or profit/drawdown.** It tests the
much narrower and still valuable question: does a contemporaneous
SPY close-based entry signal correctly anticipate the **SPY price
direction over the next ten minutes**, more consistently than matched
always-call/always-put benchmarks?

All signal/return metrics are *underlying price basis points*,
not account percentage returns. An observed positive result
**does not imply net-profitable option execution.**

## Four predeclared causal signal families (no large parameter search)

All models require at least 30 completed minutes of current-session
SPY data, entry before 15:25 ET, an uninterrupted 1-minute stream,
and at most one overlapping 10-minute observation per family.

- `close_breakout_baseline`: current **completed close** breaks
  the maximum/minimum of the **preceding 15 SPY closes**, and prior
  close was not already beyond its preceding 15-close range.
  CALL on upside, PUT on downside. **Not** an OHLC opening range.
- `close_breakout_volume`: same event AND prior-five-minute
  directional change >=3 basis points AND observed minute
  volume ratio >=1.25, using only information from the signal
  timestamp.
- `rolling_mean_reversal`: current close first moves >=7 basis
  points above/below the prior 20 **closes**' simple mean.
  Fade the excess with PUT above, CALL below. **Not VWAP.**
- `trend_pullback_reclaim`: 30-minute directional trend >=8
  basis points and completed bar pullback across its causal
  five-close mean, then reclaim with a confirming move.
  CALL/PUT selected from contemporaneous direction.

**Signal timestamp** = close of bar at frame T (`T + 60s`).
**Proxy entry** = close of next *completed* SPY bar T+1
(`T + 120s`); **proxy exit** = close of bar T+11, 10
minutes after proxy entry. Neither SPY price represents an actual
option fill, and even the SPY next-minute close is merely a
conservative observation-aligned **forecast proxy**, not
guaranteed executable.

Report matched always-CALL and always-PUT **SPY directional**
comparisons on the same timestamps. Show mean signed SPY basis points
after subtracting a *hypothetical 2/5 bps underlying friction*
for sensitivity; these **do not model 0DTE option spreads**.
A 1000-rep whole-market-day seeded bootstrap produces a CI for
**aggregate signed bps per session including days with no signals**.
The bootstrap is descriptive, assumes exchangeability of days and
does not establish statistical out-of-sample predictive certainty.

## Chronological date labels (the data has been inspected before!)

- Apr 1–Jul 31, 2026: development; no parameter fitting in this run.
- Aug 3–Sep 4: validation; compare the four frozen fixed rules.
- Sep 8–Oct 6: **previously studied diagnostic**, absolutely
  **NOT an untouched holdout** because other project experiments
  have previously viewed this time interval.

If observed metrics are not positive and statistically convincing,
none is declared a discovered trading edge. Even if a model appears
positive, an independent future options-data and replay study
must validate bid/ask entries, adverse option slippage, spread
crossing, exchange/broker cutoff and cash-account T+1.

## Run commands

No API or vendor download required:

```bash
python -m pip install -e '.[dev]'
python -m engine.free_signal_screen \
  --data-dir /data/research \
  --output-dir /data/research/free_signal_screen
python -m pytest -q tests/test_free_signal_screen.py
```

On **research-only** Railway service `spy-0dte-research`,
set `RESEARCH_MODE=freesignals`, restart research worker.
It prints every period/family result and writes
`research_summary.json` and `all_price_signals_NOT_OPTIONS_PNL.csv`
to the persistent research volume.

**Zero purchase and no live trading:** live order executor, Android,
broker authentication, paper balance and production trading
controls are unchanged. This branch is research-only.
