# New SPY 0DTE research mode — HIGH-BETA OPTIONABLE STOCKS

The new *separate* "High-Beta Stock Options" experiment tests exactly
the requested stock universe alongside the existing SPY 0DTE mode.
It reuses the previously committed **same three signal families**
(close-breakout, trend pullback reclaim, mean reversal), identical
MACD/RSI/SMA/close-price-action weighted score (no gate, >=4,
>=6) and 10-minute opportunity horizon. It does **not** change
the live Android trading toggle, production Webull broker execution,
global risk safeguards or the SPY-only baseline.

## STRICT screener, AS OF EACH signal minute (not today's completed day)

| Must be true simultaneously | Frozen rule / true measurement |
| --- | --- |
| Optionable | Verified listed optionable **on that date**, not currently |
| RVOL > 1 | **Cumulative shares to the completed 1-minute bar**, divided by 20 previous complete sessions' **same-clock cumulative** volume average; not future day-end volume |
| ATR > 1 | **ATR(14) in USD**, computed from preceding 14 fully completed daily OHLC bars, INCLUDING overnight gaps, as of previous session, no current day high/low |
| Current volume >1,000,000 | **Shares traded SO FAR on that session as of the candidate decision minute**, not shares eventually traded by closing bell |
| Stock price >$30 | Current completed 1-minute observed last/close >30 |
| Beta >1.5 | **Signed beta to SPY** using up to 252 paired previous complete daily stock/SPY returns (not beta recomputed using future/current outcomes) |

Every predicate is a strict **>**, not >=. Any missing/invalid
point-in-time metric **fails closed** rather than searching with
hindsight. Unfiltered entire optionable stock-universe history
must be provided for each testing session to avoid selection/
survivorship bias. Stocks changing tickers, splits and listing status
require historical reconciliation; test source records cannot
substitute today's winning tickers for the past universe.

ATR(14) is a **DOLLAR range**. A stock trading at $100 with $2 ATR
qualifies (subject to all other tests). It's not equivalent to a
2% normalized ATR; that normalization would be a different rule.
Beta >1.5 is directional beta to SPY, **not absolute beta >1.5**.

Data contract: `stock_minutes_YYYY-MM-DD.csv[.gz]`. Each record
is for a bar starting at `ts_start`; the record is available only
at `ts_start+60s`. Requires columns:

```csv
ts_start,symbol,close,minute_volume,cumulative_volume,prior20_mean_cumvol_same_clock,prior20_mean_minutevol_same_clock,atr14_prev_session_dollars,beta252_prev_session,optionable_asof,metrics_last_session,history_source
```

**All fields must come from genuine historical records**; `metrics_last_session`
strictly predates each current trading session. The additional previous-20
**same-minute** volume mean is for the separate, existing 1m
MACD/RSI/SMA/price-action **volume proxy**, not tape reading.
All entries require an actual historical source identifier.

The SPY row family is required in this same normalized dataset to
run the **identical weighted indicator strategy** without applying
the beta>1.5 stock filter to SPY itself. Existing SPY-only archived
minute-close files lack complete comparable historical
stock-universe eligibility metrics. Reusing them as if they
represented the broad universe would be invalid.

## Actual option quote requirements and conservative long-options math

To compare **money and premium percentage** instead of simply SPY
direction predictions, use actual *event-observed*, timezone-aware
listed options consolidated NBBO, with as-observed time and real
option contracts. Feed file:
`option_nbbo_YYYY-MM-DD.csv[.gz]`.

```csv
observed_at,underlying,option_symbol,expiration,right,strike,bid,ask,bid_size,ask_size,source
```

A valid `source` is one of `nbbo-event`, `tcbbo` or `cmbp-1`,
all of which must be true **event-observation** timestamps.
**Minute bucket-start quotes are disallowed**, as are estimated
option prices, fabricated contracts, modern quotes for historical
dates and inferred options profits from underlying price movement.
Option `option_symbol` must match listed OCC contract details
(underlying, expiration, right, strike). There is no automatic
paid provider request or data purchase.

The comparison fixes these identical execution assumptions:

- Long **one actual contract**, one open option position at a time;
  no margin, short options, forced daily bets or simultaneous
  overlapping trades across stock symbols.
- Illustrative starting **settled cash $10,000** each side and at
  most **5% of original cash** committed as entry premium per trade;
  a user-configured $115 scenario is available but may admit zero
  contracts at that budget. Never imply this is the user's bank
  or that the 5% cap represents total dollar loss safety.
- Only underlying setup evidence known at completed signal time
  selects direction. First *event-observed option quote* is no
  sooner than signal+2 seconds, at most +90 seconds. No favorable
  contract pick using a future quote or future option P&L.
- Prefer observed ATM-ish listed options with strikes within
  5% of the signal spot and event NBBO spread <=25% of midpoint,
  observed positive bid, >=1 share/contract in both bid/ask
  displayed size. Price/strike liquidity constraints may exclude
  many cheap, illiquid options.
- **SPY:** require option expiration **that session (0DTE)**.
  **Screened stocks:** accept nearest observed listed options
  with **0–7 calendar days to expiry**; report 0DTE separately
  from 1–7DTE options rather than pretending every individual
  stock has daily expirations.
- Illustrative entry purchase at observed ASK **+ $0.01/share
  adverse slippage**, exit at first observed BID at or after
  ten minutes from actual entry event, **− $0.01/share adverse
  slippage** (floor zero), plus **$0.68 per contract per side**
  in illustrative fees, multiplier 100.
- Report **realized net quote-scenario dollars**, percent per
  original premium cost, average premium percentage, positive
  versus negative money days, days with no trades, profit factor,
  trade win frequency, and closed-trade equity drawdown. Closed-
  trade drawdown is a lower bound on actual intratrade risk.
- Missing entry quotes, missing exits or an unaffordable option
  count explicitly against coverage. A missing exit is an
  **incomplete unresolved position**, not a $0 outcome or a win.
  Incomplete sources cannot be used to declare a winner.

**Expiration fairness warning:** SPY 0DTE and individual-stock options
at 1–7DTE have different theta, gamma, spreads and often
underlying/corporate event exposures. Rank SPY 0DTE vs stock 0DTE
as the genuinely like-for-like tenor; otherwise explicitly mark
different expiry as a descriptive comparison, not a fair matched
trade hypothesis. Ideally also stratify by day-of-week,
earnings events, liquidity, and trading-session overlap.

## Research objective / proof threshold

Select stocks in real-time as of eligible bar, reuse unchanged
strategy variants for both cohorts and compare **matched trading
session dates** under the same capital, quote execution, one-contract
and stop/overlap rules. Report *positive net option P&L dollars*
and **percentage growth, max drawdown, frequency and fees**,
not SPY bps direction. Never claim repeatable edge from a 10–20
trade lucky run. The harness refuses a complete historical
ranking until **>=20 common trading dates, >=30 fully observed
roundtrips per mode, and no unpriced/adverse missing entry/exit
or unaffordable skipped candidates**. These are coverage checks,
not guarantees of statistical significance. Previously viewed
dates still need **new, untouched future testing**.

## Current availability and run

**As of October 9, 2026:** this repository's retained archived
128-session corpus is SPY-only price/volume snapshots with
insufficient event-time SPY options data, and it has **no full
high-beta stock universe/point-in-time 20-day RVOL, 14-day ATR,
252-day beta or consolidated event NBBO for those stocks**.
The new mode therefore **does not currently have the inputs
needed to truthfully declare whether SPY 0DTE or the screened
stock mode is more profitable**. We must not make up numbers.

The mode's standalone first run returns an explicit machine-readable
`BLOCKED_MISSING_HISTORICAL_REAL_MARKET_INPUTS` audit and the
requested strict thresholds, rather than a fake winning result.

```bash
pytest -q tests/test_high_beta_stock_options_mode.py
python -m engine.high_beta_options_compare \
  --data-dir /data/research \
  --output-dir /data/research/high_beta_stock_mode \
  --initial-cash 10000
```

On isolated Railway service `spy-0dte-research`, use
`RESEARCH_MODE=highbetastocks`. This looks for existing
`stock_minutes_*.csv[.gz]` and `option_nbbo_*.csv[.gz]`;
it **never downloads or purchases them**. Readiness file
`high_beta_stock_mode/readiness.json`, or, with genuine inputs,
`high_beta_stock_mode/summary.json` and the
`OCC_NBBO_ASK_BID_CASH_SCENARIO_NOT_LIVE_TRADES.csv` ledger.

All coding/testing can be done at $0 added **market data** expense.
Existing Railway hosting may still incur its normal charges.
No Android UI changes, main branch merges, broker order flags or
production risk-control overrides were made in this research PR.


## Executed research-worker readiness (October 9, 2026)

GitHub Actions passed for this branch. The isolated Railway
`spy-0dte-research` deployment
`95ba08f8-0729-4aef-acbe-cdb67a54f3f8` **completed successfully**
and wrote the report `/data/research/high_beta_stock_mode/readiness.json`.
Its actual status was:

```json
{
  "status": "BLOCKED_MISSING_HISTORICAL_REAL_MARKET_INPUTS",
  "missing": [
    "stock_minutes_*.csv(.gz) spanning UNFILTERED stock universe AND SPY with point-in-time historical screen metrics",
    "option_nbbo_*.csv(.gz) observed-event SPY+stock OCC options NBBO and sizes"
  ],
  "stock_universe_screened": false,
  "historical_option_PnL": null,
  "profitable_strategy": null,
  "new_data_purchased_usd": 0,
  "live_order_changes": 0
}
```

The **research engine is operational** and **honestly blocked at the
data layer**. It cannot establish a real-world winner or claim any
profitable screened ticker yet. Current public optionability tables
or modern fundamental beta snapshots cannot replace full
historical point-in-time universe/quotes without survivorship and
lookahead bias. Do not order paid historical market data without
specific user authorization.
