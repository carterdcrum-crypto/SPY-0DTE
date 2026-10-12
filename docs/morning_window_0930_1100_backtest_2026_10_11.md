# SPY 0DTE — move the morning strategy to 09:30–11:00 ET
Historical experiment executed October 11–12, 2026

## The user-requested change

**New time window: 09:30 inclusive to 11:00 exclusive America/New_York.**
The previous high-volume morning research entry rule used 10:00–12:00 ET.
The research branch's `engine/cost_aware_entry_edge.py` now applies
**09:30–11:00 ET** to its `volume_150_early` variant.
Relative-volume >=1.50 remains fixed. The **30% whole-position
trailing stop** remains fixed, with independent 35% hard-loss stop and
ten-minute maximum hold, no profit target, scale-out, or runner.

### Critical signal-history limitation

The **existing four-factor signal classifier needs 75 completed
1-minute SPY closes**. Assuming the first available bar begins at
09:30 ET, its first eligible signal is **10:46 ET**. Consequently
the simple clock change only moved the strategy to an effective
**10:46–11:00 ET** subset, not a functioning 09:30 entry system.

To evaluate the requested early window honestly, a separate,
research-only **opening close-breakout** variant was included.
It can begin at **09:46 ET** after 15 previous completed
one-minute closes, requires relative volume >=1.50, a first new
15-minute CLOSE breakout and direction-aligned five-minute close
momentum >=3 basis points. It is **not a genuine OHLC opening
range breakout, VWAP, full tick/volume tape, or real execution
backtest**.

A combined variant tries opening close-breakouts and late existing
four-factor signals together with one deterministic ten-minute
nonoverlap lock.

## Actual results (same 128 archived SPY sessions)

| Arm | One-contract sampled cases | Win rate | Avg option return | Summed modeled P&L | Extra $2/trade friction P&L |
|---|---:|---:|---:|---:|---:|
| **Original 10:00–12:00, four-factor + RVOL>=1.5** | 81 | 41.98% | +5.438% | **+$290.70** | +$128.70 |
| **Shifted 09:30–11:00, same existing four-factor** | **14** | **57.14%** | **+5.443%** | **-$6.20** | **-$34.20** |
| New opening-close-breakout proxy, 09:30–11:00 | 163 | 39.26% | -0.943% | -$558.90 | -$884.90 |
| Opening + existing four-factor 09:30–11:00 | 165 | 40.00% | -0.724% | -$511.50 | -$841.50 |

**Important:** The 57.14% from eight wins out of 14 is a very
small sample and **not repeatable 57% evidence**. Total modeled
dollars remained negative despite a positive average percentage
return. Different premium sizes per contract explain why those
two aggregates do not have to share the same sign.

Of the shifted existing four-factor's 14 qualifying quotes:
- **0** were at 09:30–09:59;
- **0** were at 10:00–10:44;
- **14** were at 10:45–10:59 (all signals near the latter end,
  given indicator warmup).

Of the separate 163 opening close-breakout proxy cases:
- 27 signals before 10:00;
- 108 signals from 10:00–10:44;
- 28 signals from 10:45–10:59.

Historical group sums, **shifted four-factor**:
- development 82 sessions: **11 cases, -$135.30**;
- designated validation 25 sessions: **1 case, +$81.70**;
- previously inspected diagnostic 21 sessions: **2 cases, +$47.40**.

Historical group sums, **opening close-breakout**:
- development: **110 cases, -$575.00**;
- validation: **25 cases, -$66.50**;
- previously inspected diagnostics: **28 cases, +$82.60**.

The shifted version's largest single winning session accounted
for **+$81.70**. Removing that day changes the summed result
to **-$87.90**. Its extra-execution-stress 5-session block
bootstrap uncertainty interval in USD per archived calendar
day was **[-$3.688, +$2.520]**, which spans zero. The
previous 10:00–12:00 result is even more concentrated
in a few large winners.

## Engineering interpretation

The requested **09:30–11:00 time configuration has been
implemented for the research morning-volume rule**. With
the same existing four-factor model, this timing change
raises sampled historical win rate from ~42% to ~57%,
but cuts scenarios from 81 to **14**, and the
net sum **becomes negative**. This is NOT proof of a better
strategy. Starting genuinely at 09:30 requires a new
signal design using completed premarket or opening bars
and a separate holdout, not silently filling missing
indicator history with invented prices.

The optional opening-close-breakout research mechanism
generated more early signals but was **unprofitable**. It
should **not** be promoted to paper-auto/live on the
current evidence.

## Data and safety notes

The 128 trading sessions had been inspected in previous
experiments. The options quotes are archived **CBBO-1m
minute buckets**, not event-observed executable as-of NBBO;
quoted sizes and fills are unverified. Full-chain point-in-time
SPY put/call volume is missing. These are hypothetical
one-contract returns, with $0.65/side fees and 1 cent/share
adverse option slippage per side, plus an optional additional
$2/trade execution-cost stress; they are NOT a funded real
cash-account equity curve or reliable live profit projections.

**No new historical data purchases, broker orders, Android UI,
production strategy, live toggle or account records were changed.**
Research code only; production not promoted.

### Files

- `engine/cost_aware_entry_edge.py`: morning window now 09:30–11:00 ET.
- `engine/morning_0930_1100_study.py`: old/new side-by-side and
  opening close-breakout proxy.
- `tests/test_cost_aware_entry_edge.py`,
  `tests/test_morning_0930_1100_study.py`: boundary and causality tests.
- `research/results/morning_window_0930_1100_2026_10_11.json`:
  full executed Railway study, 128 archival sessions.
