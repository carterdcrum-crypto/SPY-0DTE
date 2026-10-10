# SPY 0DTE only — event-quote execution research readiness

**Scope:** SPY options expiring **the same session only**. The separate
longer-expiration experiment is paused and not part of this branch.
Historical stock screening stays discontinued. Live trading and the
adaptive research signal filter remain **OFF**.

## Why this gate exists

Our 128 archived SPY minute-close sessions yielded thousands of SPY
directional forecasts but did **not** establish true SPY 0DTE
premium gains. A direction win can still lose option money through
spread, IV crush, theta and contract cost.

The archive retained per-minute close/volume snapshots and sampled
option information, **not** an independently timestamped complete
underlying 1-minute OHLCV series plus **observed-event-time OCC
bid/ask updates** sufficient to demonstrate actual fillable premium
returns. We will not turn estimated SPY basis points into real option
profit percentages or declare a daily edge from them.

This module audits source-quality gaps and **refuses to calculate
performance until there is enough genuine data**. It makes no
market-data purchases, provider API calls, Webull orders, account
changes or capital commitments.

## Required archived files (SPY only)

`spy_bars_YYYY-MM-DD.csv[.gz]`, genuine one-minute OHLCV:

```csv
ts_start,open,high,low,close,volume
```

`spy_option_quotes_YYYY-MM-DD.csv[.gz]`, **true event-observed**
0DTE SPY option quote updates:

```csv
observed_at,symbol,expiration,right,strike,bid,ask,delta,bid_size,ask_size,volume,open_interest,schema
```

Only actual event-observed quote schemas `cmbp-1`, `tcbbo` or
`nbbo-event` are accepted by the existing strict loader;
`cbbo-1m` (minute-bucket timestamps) and fabricated provider
data are rejected. Quotes must represent real available times, not
minute bars timestamped retrospectively.

### Predeclared coverage safeguards

- At least **20 complete matched sessions** with **every completed
  underlying one-minute OHLCV bar**, 390 on a standard US market day,
  adjusted for the verified holiday/early-close exchange calendar.
- The session's event-observed 0DTE option quotes must contain at least
  **50** independently observed events meeting contemporaneous
  liquidity: bid>0, ask>bid, displayed bid and ask size>=1,
  recorded OI>=25, abs(delta) 0.4–0.6 and spread/mid<=15%.
- At least **90% of 5-minute windows** from 9:45 ET to the
  exchange-close 15-minute cut-off must have observed eligible
  quotes from **both** CALL and PUT sides. This is only an
  archival sufficiency proxy: it **does not guarantee** a fill on
  a specific chosen contract or at a specific order time.
- Missing a whole market session, missing the quote file on an
  OHLCV date, or missing the OHLCV file on a quote date
  **blocks the entire run**—no favorable-day selection.
- Market sessions must not be selected just because a quote series
  happened to be complete on a profitable-looking day. Previously
  examined dates are not an untouched holdout.

### What runs after the data passes?

Only the **existing, frozen SPY-only actual-quote scenario**
strategies are evaluated at their existing fixed settings:
`orb_simple`, `orb_filtered` and `vwap_reclaim`, each at a
separately simulated 1%-of-equity option-premium exposure setting.
This is the older **true OHLC opening range / bar-price VWAP proxy**
research, not the 15-prior-close breakout signal—it must **not**
be conflated with our previously reported directional
simulations. The model is research-only.

On enough proper event data, the simulator records 0DTE option
ASK-plus-adverse-slippage entries, BID-minus-adverse-slippage exits,
fees, T+1 cash-account settlement, no-trade days, scenario cash
returns and drawdown. These are **hypothetical executions against
genuine observed quote events, not actual broker fills**. Results
from previously seen sessions are exploratory, and proving a
repeatable edge still requires genuinely fresh untouched sessions
and paper/live brokerage fill fidelity.

### Reproduction and research deployment

```bash
pytest -q tests/test_spy0dte_quote_readiness.py
python -m engine.spy0dte_quote_readiness \
  --data-dir /data/research \
  --output-dir /data/research/spy0dte_quote_readiness
```

Use only the isolated `spy-0dte-research` Railway service with
`RESEARCH_MODE=spy0dtequotes`. No request to purchase historical
data. With missing market data the gate still runs and writes
`/data/research/spy0dte_quote_readiness/readiness.json`,
reporting explicit missing fields/days, legacy snapshot counts,
and **null actual profitability**. Only if every coverage safeguard
passes will the separate
`fixed_strategy_quote_scenarios.json` be created.

No broader-stock or other-expiration mode. No changes to the Android
app, live order execution, or the Auto Trade switch.
