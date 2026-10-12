# Frozen Regime Reclaim — Prospective Event-Data Shadow Validation

**Frozen source:** `2aa21568ff72e4cafbf718e75084d3c0fa4ee83b`
**Selection arm:** `blend_trend_or_stretch_cost_aware`
**No changes to model parameters:** 8/21 EMA reclaim or stretch-band
mean re-entry with as-of option spread <=3.5%, option implied/realized
volatility ratio <=1.5. **Do not tune** thresholds on additional
previously inspected SPY sessions.

**Frozen exits:** whole-position 30% trailing stop, separate 35% original
premium hard-loss stop, 10-minute max hold, NO take-profit, no partial
sells, no runner. $1,000 illustrative starting settled cash; option
debit reserved immediately, T+1 sale proceeds not recycled same day.
Exactly one 0DTE call or put position at a time.

## What this implements right now

`engine/frozen_regime_forward.py` provides a complete, fail-closed
**offline paper event-data replay** over only genuinely new trading
days **strictly later than the most recent legacy research date**.
It will:

1. **Refuse to claim prospective results** from the 128 legacy
   inspected CBBO-minute session files. These are NEVER substituted
   as event quotes.
2. Load only strict real SPY OHLCV bars and independently observed
   true event-time 0DTE option NBBO/size through existing
   `engine/weekly_options_data.py`, with matching option expiry.
3. **Require** additional as-of `volume_ratio` and
   `realized_volatility` on each completed SPY bar, and as-of
   `implied_volatility` on each option quote. Omission means
   **BLOCKED**, not a synthetic placeholder. Verify input quote
   provenance and volume/volatility derivation before upload.
4. Generate the FROZEN algorithm's price-only entry signals from
   **already completed** SPY bars, choose the earliest eligible
   event-observed option quote **60–120 seconds AFTER the signal**,
   require original delta+liquidity screen and frozen option-cost
   gate; log both accepted and rejected decisions.
5. Produce hypothetical ask-entry/bid-exit option paper results at
   30% trailing / independent -35% / 10-minute cap using the
   event NBBO source. Missing future exit quotes are UNSCORED
   and leave premium reserved; no invented profitable fills.
6. Calculate one-episode settled-cash simulation on the new
   data and write machine-readable `manifest.json`,
   `decisions.csv` and `paper_positions.csv` into a
   research-only working directory.

**Important:** this is an OFFLINE prospective replay when new
source files ARRIVE, not a continuously subscribed stock/OPRA
feed or Webull paper broker. It does not place orders, assume
a current account balance or purchase market data. Even fully
populated event quotes remain reported observations, not actual
exchange/broker guaranteed fills.

## Required input contract

One pair for each calendar session `YYYY-MM-DD` in the
`--event-dir` directory:

```text
spy_bars_YYYY-MM-DD.csv
  ts_start, open, high, low, close, volume,
  volume_ratio, realized_volatility

spy_option_quotes_YYYY-MM-DD.csv
  observed_at, symbol, expiration, right, strike,
  bid, ask, delta, bid_size, ask_size, volume,
  open_interest, schema, implied_volatility
```

All timestamps MUST include timezone or UTC offset. Bar timestamps
are their START and become usable ONLY after the full minute completes.
Quote timestamps must be the actual as-of **event observation**,
not the end of an aggregated CBBO one-minute bucket.
Quote schema is checked by the strict event loader; OHLCV values
and OCC contract/expiry parsing are validated.

These extra as-of volatility and relative volume observations are
**not yet provided** by our original historical archive. There is
no invented fallback.

## Commands

```bash
python -m pytest -q tests/test_frozen_regime_forward.py
python -m engine.frozen_regime_forward \
  --event-dir /data/research/forward_events \
  --legacy-dir /data/research \
  --output-dir /data/research/frozen_forward
```

### Current data availability

The 128 inspected SPY historical session files provide only
minute-bucket CBBO and SPY one-minute close / volume-ratio.
The most recent audited inventory had **zero** paired true
OHLCV plus event-time NBBO option sessions. The expected
first forward gate status is
`BLOCKED_NO_NEW_EVENT_SESSIONS` — not a 0% win rate and
not a completed unseen backtest.

This is intentionally separate from the Voss public-price-structure
research branch. Don't mix research arms, sample periods,
or promote after an isolated lucky week. Measure win rate,
*actual net option payoff after costs*, calendar weekly consistency,
0DTE exercise/exposure constraints, and $1,000 T+1 equity
drawdown once several entirely new weeks of event observations
have accumulated.

**No market orders, data purchases, or autonomous live changes.**
