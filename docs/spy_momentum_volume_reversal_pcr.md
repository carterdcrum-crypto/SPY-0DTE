# SPY 0DTE: Momentum, volume, completed-close price action, reversal, MACD & put/call *volume* ratio

**Fixed, preregistered rules. One SPY instrument only. No 1DTE. No live orders.**

## What we're testing

This compares three models on **exactly the same previously inspected 128-session archived SPY 1-minute snapshot dataset** (Apr–Jul development, Aug–Sep validation, previously analyzed Sep–Oct diagnostic):

1. **Baseline:** existing three-family SPY close-breakout, 5-close trend pullback/reclaim and rolling 20-close mean-reversion setup. Identical 10-minute hold, next completed 1m CLOSE spot proxy; fixed 10-minute portfolio non-overlap lock.
2. **Four-factor ablation:** add frozen momentum, MACD, as-of 1-minute volume ratio, completed CLOSE price action, and explicit reversal logic. Evaluate without put/call sentiment to establish what actual existing price data proves.
3. **Five-factor full fusion:** require also **historical as-of SPY-specific PUT volume / CALL volume** from a **complete SPY options chain**. If genuine complete intraday SPY option-chain cumulative volumes are absent for ANY eligible opportunity in a period, the entire period's five-factor model is **NOT TESTED / MISSING DATA**, not zero profits or cherry-picked signals.

## Causal signal construction (as-of original completed SPY close)

For each candidate from the three prior families:

**Momentum (breakout or pullback reclaim):**
- Price action vote 2 pts: signed change over past 3 complete closes >=2bps and last complete 1m bar not opposing the forecast.
- MACD vote 2 pts: current MACD(12,26,9) line above signal for CALL, below for PUT.
- Volume vote 2 pts: original observed `volume_ratio >=1.25`.
- Five-minute SPY momentum vote 1 pt: signed 5-minute SPY close move >=3bps.
- Require **>=5 of 7** and volume confirmation.

**Reversal (previously defined 20-close mean deviation crossing):**
- Completed 1m price turn toward the reversal candidate >=0.5bps: 2 pts; this is a required price-action vote.
- MACD histogram improving toward proposed direction since previous completed minute: 2 pts.
- Original archived volume ratio >=1.10: 1 pt.
- Wilder RSI(14) below 45 for CALL reversal, above 55 for PUT reversal: 2 pts.
- Require **>=5 of 7** and a completed-close price turn.

**Verified SPY put/call option volume sentiment**, a secondary gate after 4 factors:
- Momentum CALL: SPY cumulative put/call volume <=0.95; momentum PUT: >=1.05, following options sentiment.
- Reversal CALL: ratio >=1.15; reversal PUT: <=0.85, fading extreme options positioning.
- These **are fixed research hypotheses, not fitted/optimized parameters**, and put/call flow has ambiguous interpretation; full-chain volume is not directional tape.

All indicators and ratios must be known before the signal timestamp. This is **NOT** OHLCV candlestick pattern analysis, actual aggressor-side order flow, VWAP, or option contract execution. Existing snapshot `volume_ratio` is an archived proxy. The archived minute option list is an INCOMPLETE subset, so it **cannot** represent true put/call volume. Cboe publishes **daily market-wide statistics**, but same-day FINAL ratios cannot be read at 10:15 a.m. without lookahead, and a market-wide total is not the requested SPY-specific intraday chain ratio. See [Cboe daily market statistics](https://www.cboe.com/us/options/market_statistics/daily/) and [Cboe historical ratios](https://www.cboe.com/us/options/market_statistics/historical_data/).

To supply **already-owned valid historical intraday SPY cumulative option volume** in the future (no purchase authorized), place:

```
/data/research/spy_pcr_YYYY-MM-DD.csv[.gz]
observed_at,put_volume_so_far,call_volume_so_far,coverage,source
2026-08-12T11:00:00-04:00,100000,80000,all_spy_listed_options,validated_provider
```

Each observation must reflect **all SPY option expiry/strikes/rights**, not a sampled collection. Timestamp must be the actual time the volume was available; prior observation may be reused for **at most five minutes**. Timezone mandatory. Per-day put and call cumulative volume must be nondecreasing, no future end-of-day totals. CSV is not self-authenticating; historical source documentation and full-chain coverage must be checked before trusting actual results.

## Score and gate

Every chosen forecast represents a hypothetical ten-minute directional change in the **underlying SPY 1-minute closing price** from the next completed minute to ten completed minutes later. It is **NOT an executable option entry/exit or premium P&L**.

For each model and period report: original candidates, accepted signals, correct direction, move >2 and >5bps, daily signed SPY basis points after *hypothetical* 2bps per selected signal, positive/negative/zero SPY-price days, number of signal days, worst week, additive SPY basis-point decline (NOT account drawdown), and seeded 1,000 replicate 5-session block bootstrap intervals. A **0-signal** day is neutral, not a profitable day.

**Acceptance threshold:** positive signed SPY-bps/day including zeros in **all three** previously observed regimes, decent multi-day confidence, and later fully untouched data. EVEN if successful, this does not prove a 0DTE options-money edge. True 0DTE profitability requires sufficiently timestamped actual option bid/ask, fees, spread, theta/IV movement, contract sizing, settled cash, realized and mark-to-market drawdown.

## Run

```bash
pytest -q tests/test_spy_pcr_momentum_reversal.py
python -m engine.spy_pcr_momentum_reversal \
  --data-dir /data/research \
  --output-dir /data/research/spy_pcr_momentum_reversal
```

Research Railway service `spy-0dte-research` uses **`RESEARCH_MODE=spy_pcr_momentum`**. It loads ONLY existing archived data, writes `report.json`, prints separate named reports per period and model; no buying feeds and no broker operations. Adaptive trailing-return signal gate remains OFF and live auto-trading remains OFF.

**Never claim a five-factor PCR backtest was completed when PCR was missing.**
