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


## Completed Railway archival research results — October 8, 2026

A fresh research-only Railway deployment executed the frozen four
signal models on **128 historical SPY sessions / 49,792 market frames**.
No new purchased feed, no live trading calls. Research worker completed
and stored its full decision ledger and JSON summary under
`/data/research/free_signal_screen/`.

### Directional price-forecast results (NOT options, NOT profits)

Each row is **mean signed SPY underlying 10-minute move in BASIS POINTS
from next completed bar**, with per-event directional hit rate.
Zero-trade dates were retained in the separate day/week denominators.

| Period | Model | Signal events | Direction correct | Mean signed SPY move |
| --- | --- | ---: | ---: | ---: |
| Dev Apr–Jul (82 days) | 15-close breakout baseline | 1,582 | 49.37% | **−0.0065 bps** |
| Dev Apr–Jul | Breakout + volume | 630 | 47.94% | **−0.0221 bps** |
| Dev Apr–Jul | Rolling-mean reversal | 931 | 49.84% | +0.1047 bps |
| Dev Apr–Jul | Trend pullback/reclaim | 920 | 53.91% | +0.6689 bps |
| Validation Aug 3–Sep 4 (25 days) | 15-close breakout baseline | 497 | 53.32% | +0.2995 bps |
| Validation | Breakout + volume | 166 | 50.00% | +0.3694 bps |
| Validation | Rolling-mean reversal | 191 | 42.41% | **−1.2136 bps** |
| Validation | Trend pullback/reclaim | 228 | 51.32% | +0.1095 bps |
| Prior-studied diagnostic Sep 8–Oct 6 (21 days) | 15-close breakout baseline | 394 | 44.67% | **−0.6101 bps** |
| Prior-studied diagnostic | Breakout + volume | 148 | 40.54% | **−0.7360 bps** |
| Prior-studied diagnostic | Rolling-mean reversal | 172 | 55.81% | +0.6631 bps |
| Prior-studied diagnostic | Trend pullback/reclaim | 195 | 46.67% | **−0.0802 bps** |

**Uncertainty:** the whole-day 1,000-resample bootstrap 95% interval
for **signed-bps-per-day including no-signal days** includes zero for
EVERY strategy in the Aug–Sep validation sample **except reversal,
whose interval is entirely negative**: −16.9789 to −2.0636 bps/day.
Example: breakout + volume validation interval −4.245 to +9.3962;
pullback/reclaim validation interval −7.1152 to +8.8501 bps/day.

**Conservative friction sensitivity:** a hypothetical *underlying*
2-basis-point round-trip hurdle (NOT options execution costs) already
takes all **twelve period × strategy mean event returns below zero**.
Even the top validation row (+0.3694 bps) becomes **−1.6306 bps**.
Actual 0DTE options spreads, time decay, fills, fees, and gamma
are a distinct expense; **no executable options P&L is estimated**.

### Falsifiable conclusion

The **volume-confirmed breakout** leads the limited Aug–Sep validation
sample on raw directional bps, but fails on the Sep–Oct prior-studied
diagnostic set: +0.3694 to **−0.7360 bps/event**. The
trend pullback shows a similar deterioration, and mean reversion
flips from negative validation to positive later diagnostic.

**No stable repeatable entry-direction edge survives the elementary
2-bps proxy stress or the chronological regime change. There is no
evidence for +100% account return per week.** These experiments
also reused historical days inspected in earlier project research,
so neither sample is a truly untouched options validation.

**Recommended response:** do NOT deploy any of these models as
autonomous live 0DTE strategy, do NOT search a huge parameter grid
until a winner appears, preserve money and data-purchase budget,
and collect additional untouched data in the future.
