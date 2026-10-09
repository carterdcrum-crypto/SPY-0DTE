# SPY ONLY: paired 0DTE versus 1DTE option quote return experiment

## User request and exact scope

Add **1DTE SPY option research** to existing SPY 0DTE signals, instead
of researching unrelated individual stocks. No more stocks mode.
Adaptive trading signal gate remains **OFF**, live broker auto trade
remains **OFF**, and no paid historical data will be ordered.

**1DTE is defined here precisely as a SPY option expiring on the
next verified NYSE trading session.** A Friday 1DTE expires Monday
(unless Monday is closed), even though that is three or more calendar
days away. Display both actual expiration dates and this convention
to avoid confusion. Existing NYSE calendar must explicitly recognize
the date, otherwise this experiment fails closed.

## Frozen comparison, not hindsight optimization

**Same SPY price-action setups and weights** as PR #36:
close-breakout / trend-pullback / rolling-mean reversal with MACD
(12,26,9), Wilder RSI14, SMA20/50, completed-price-action score
and historical previous-20-day same-minute volume estimate. Three
fixed policies: all nonoverlapping, weighted score >=4, >=6. No new
threshold tuning; spot signals are computed once per session, then
applied to *both* option expiry cohorts.

All three candidate cohorts are matched on the exact underlying
direction, signal timestamp, day, and nominal ten-minute holding
period. Only the *actual option contract tenor* differs. Equal
initial scenario cash; one long contract for each book, <=10% of
initial scenario capital in premium and always limited by available
settled cash (long options only; no leverage). Cash sale proceeds are
not reused before T+1 trading-session settlement. No overnight hold:
both books **exit the same day**.

Use actual, timezone-aware **event-observed OCC SPY NBBO quotes**,
not sampled CBBO-1m timestamps (minute BUCKET STARTS), midprices,
model-implied prices or future quote peeks. At each original signal,
wait >=2 seconds, require the first qualifying observed quote within
90 seconds and choose contemporaneously visible strike with
absolute *observed* delta nearest 0.50 (range 0.4–0.6), bid>0,
bid<ask, 1x1 size or better, prior observed open interest >=25,
and NBBO spread/midpoint <=15%.

**Entry debit = actual observed ask + 1 cent/share adverse option
slippage, times the 100 share multiplier, plus illustrative $0.68
one-contract fee.** Same-contract exit credit = first observed bid
no sooner than ten minutes after the quote entry (within 90s),
less one cent/share adverse slippage, times 100, minus $0.68 fee.
The 0DTE and 1DTE entry event timestamps can differ slightly;
results will show both. Enforce same-day exchange-close and
15-minute broker-risk cutoff for exits.

All assumed costs are exactly identical across expiration models.
We do **not** claim these are executable broker fills; observed NBBO
liquidity cannot guarantee a fill at the expected price.

### What success would mean

Measure matched contract quote-scenario dollars, premium-return %
per trade, account-cash % growth, fee-adjusted profit factor,
positive/negative/no-trade days, closed-trade additive drawdown
(**not intratrade mark-to-market max drawdown**), data coverage and
number of quoted matched pairs.

A quote-scenario advantage can be described as *historical*, not as
future repeatable edge. Do not name a prospective winner absent at
least 20 shared trading sessions and 30 **fully observed matched
0DTE/1DTE round trips**, plus a future untouched evaluation. This
minimum is an audit hurdle, **not a statistical guarantee**.

When a signal is only tradable in one expiration, skip both for this
**strict matched comparison**, record missing one-tenor quote.
An unresolved entered option position blocks later trades for that
session: it can never be quietly discarded to make P&L look better.
No position or quote data means profitability is **unknown, not 0%**.

## Exact real historical data needed

The existing archive has 128 SPY-only one-minute spot snapshots,
but its old option CBBO snapshots do not include independently
timestamped events with precise option bid/ask receive times,
and it lacks genuine 1-minute SPY OHLCV source bars. These are
**not usable** as entry/exit quotes. A new options data purchase
is NOT authorized by this experiment.

Research looks for already supplied files on the volume:

```
/data/research/spy_bars_YYYY-MM-DD.csv[.gz]
  ts_start,open,high,low,close,volume

/data/research/spy_option_quotes_YYYY-MM-DD.csv[.gz]
  observed_at,symbol,expiration,right,strike,bid,ask,delta,
  bid_size,ask_size,volume,open_interest,schema
```

Quote file needs **both** expiry cohorts on each historical
session: option expires on trade date and on next verified trading
session. Each quote `observed_at` must truly be the availability
timestamp of an event NBBO update; `schema` must correspond to
`cmbp-1`, `tcbbo`, or `nbbo-event` (real event observations).
Date-specific contract expiration data is necessary; quote data
for only 0DTE is insufficient.

When required files or contract tenors are missing, the worker
writes `readiness.json` with the exact blocker and does not
invent profits.

## Reproduce

```bash
pytest -q tests/test_spy_expiry_pair_study.py
python -m engine.spy_expiry_pair_study \
  --data-dir /data/research \
  --output-dir /data/research/spy_0_vs_1dte
```

On the **isolated research-only** Railway service
`spy-0dte-research`: `RESEARCH_MODE=spyexpiry01`.
This worker does not make live broker or paid market data calls.

- `spy_0_vs_1dte/readiness.json` — no-fake-data input audit.
- `spy_0_vs_1dte/comparison.json` — paired strategy metrics if
  genuinely sufficient historical event quotes exist.
- `spy_0_vs_1dte/matched_observed_quotes_NOT_BROKER_FILLS.csv`
  — if genuine paired quote scenarios were found.

The previously viewed dates are **NOT an untouched holdout**.
Live 0DTE code, Android UI, Webull connection, and account risk
controls remain unchanged. No auto-trading enabled.


## Actual research-worker input audit — October 9, 2026

Full Python GitHub Actions CI passed, including the new synthetic
unit tests for Friday-to-Monday/holiday 1DTE, timestamp order,
actual quote bid/ask, matched contract comparison, hypothetical
option-premium percentages, cash-account affordability, and no
fabricated P&L.

The isolated Railway research deployment
`4cde444f-3eb8-44c7-ba84-36c35398ad47` **completed SUCCESS**
and wrote `/data/research/spy_0_vs_1dte/readiness.json` to the
existing research volume. Real observed input audit:

| Existing dataset kind | Count |
| --- | ---: |
| Previously archived SPY 0DTE snapshot files (not event NBBO) | **128** |
| True SPY 1-minute OHLCV session files | **0** |
| Independent event-observed SPY option NBBO files | **0** |
| Matched 0DTE/1DTE historical session pairs | **0** |

Status: `ready_to_score=false`; both expiry-specific quote
coverage fields `MISSING`. Therefore **there are NO genuine
0DTE versus 1DTE historical option profit percentages, account
drawdown figures, or winning expiration to report**. Existing
snapshot-derived signed SPY bps are not real options profits.

New market data purchases: **$0**. No production change or
live execution; adaptive research gate OFF. The research comparison
code itself is now ready to accept an already-licensed source
satisfying the stated data contract if it becomes available.
