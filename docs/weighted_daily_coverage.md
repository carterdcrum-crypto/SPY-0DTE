# Research: signals on more trading days without faking daily profit

**User's requirement:** a *repeatable net economic edge*, preferably earning
money on most trading days. The previous 8/6-event strict confluence model
was sparse and failed the later archive, so its apparent 75% hit rate was
not compelling. More signals alone cannot create profitability.

## Predeclared design — no hindsight changes

Run all three causal signal families already in
`engine.free_signal_screen`:

- Breakout of the previous 15 **completed CLOSES** (not an OHLC opening range).
- Trend pullback reclaim of the last five completed closes.
- Reversal after an abnormal excursion from the last 20 completed closes.

Each family can propose an independently timestamped CALL/PUT
**directional** opportunity. Use the pre-existing one-minute-close
signal moment; accept a forecast on the next completed one-minute
close and exit at the observed CLOSE 10 minutes later.
These are **price forecast proxies** and not executable entry/exit
prices for 0DTE options.

Require 75 continuous one-minute observations before scoring the signal;
MACD 12/26/9, Wilder RSI14, SMA20/50. None of this uses future bars.

The point score is fixed now, not fitted to these dates:

| Evidence observable at signal time | Points |
| --- | ---: |
| Recent MACD crossover toward CALL or PUT (within last 5 complete bars) | 2 |
| Wilder RSI(14) CALL 50–75 / PUT 25–50 | 2 |
| Close above SMA20 above SMA50 for CALL, reversed for PUT | 2 |
| Last 3 completed closes demonstrate >=1bp move toward proposed side, no >2bp interim adverse CLOSE excursion | 2 |
| Actual contemporaneous minute SPY volume ratio >=1.25 (NOT real tape reading) | 1 |

All three strategies share the **same event selection clock** and
one portfolio-level non-overlap rule: once a directional SPY price
proxy is selected, no additional selection before its 10-minute
exit. Same-minute ties are sorted by fixed setup-family order, never
future profitability. Each policy applies the same no-overlap rule
but differently decides which eligible opportunities to admit:

1. `all_setups_nonoverlapping`: all three families, **no score gate**.
2. `weighted_score_ge4`: select if score >=4 of 9.
3. `weighted_score_ge6`: select if score >=6 of 9.

Unlike the previous strict AND rule, one missed indicator will not
automatically reject a potentially valid signal. No forced daily
entry, no adaptive/trailing-return gate (it remains OFF). The
experiment explicitly allows **zero trades** if candidates fail.

## What actually counts as success?

A desired real **daily net options profit** would need option-chain
event timestamps, entry ask, exit bid, order size, spread,
commissions, time decay, margin/cash-settlement rules, and a
legitimate position-capital accounting curve. Current archives
retain sampled 1m SPY spots/volume and no complete 0DTE option
quote execution path. **Daily money earned/lost remains UNKNOWN.**

This experiment instead reports the less useful but available
*underlying SPY signed price-basis-points diagnostic*:
10-minute signed close-to-close SPY basis points minus a fixed,
hypothetical **2bp per accepted opportunity**. That isn't a
plausible estimate of real option spread/premium costs. Do not
call those values dollars, trade profit, or account P&L.

Predeclared per-period outputs:
- Signals per calendar eligible trading session.
- Sessions with >=1 selected signal versus zero-signal days.
- **Days with positive, negative, and zero sum of signed SPY bps after
  the hypothetical 2bp hurdle.** A zero-signal day is NOT a winning day.
- Correct SPY directional forecasts, price moves >2bps and >5bps
  in the right direction; mean signed bps per opportunity.
- **Average signed bps per market day including zero-trade days**,
  worst week, max cumulative *additive signed-bps decline*
  (NOT account maximum drawdown), and a seeded 1000-replicate
  circular 5-session block-bootstrap 95% descriptive interval.

Compare **three unchanged chronological periods** Apr1–Jul31
development, Aug3–Sep4 validation, Sep8–Oct6 previously inspected
diagnostic. All have previously been studied: this is not a new
untouched holdout, and there are three setup/threshold variants,
meaning multiple comparison risk.

The bar for declaring a useful research candidate is positive
mean signed underlying daily bps **in every studied period** with
supportive uncertainty and a high fraction of positive SPY-price
days. Even that is NOT proof of real options profitability.
If the broad or weighted models lose more days than they win, or
a promising validation result fails in later data, state that
clearly rather than claiming signals make money daily.

## Reproduce with already held archives

```bash
python -m pytest -q tests/test_weighted_daily_coverage.py
python -m engine.weighted_daily_coverage \
  --data-dir /data/research \
  --output-dir /data/research/weighted_daily_coverage
```

On the isolated `spy-0dte-research` Railway service set
`RESEARCH_MODE=weighteddaily`. It writes
`weighted_daily_coverage/report.json` and
`weighted_daily_coverage/all_weighted_setups_NOT_OPTIONS_PNL.csv`
to the existing data volume. No data purchase, order placement,
Android change, account connection, or reactivation of live trading.


## Measured results — October 9, 2026: frequency increased; daily edge FAILED

The isolated Railway experiment completed on **128 archived sessions /
49,792 minute observations**, generating **4,232** candidate setup
events before a shared portfolio nonoverlap lock. No new market data
was purchased; GitHub tests passed. The complete candidate ledger
and per-period JSON report were saved under
`/data/research/weighted_daily_coverage`.

These results are not option-contract profit or financial drawdown.
A "positive day" below means **sum of signed SPY ten-minute
underlying CLOSE-price basis points minus a hypothetical 2bps
per accepted direction forecast >0**. A dollar account can lose
even if that proxy is positive due to spreads, IV/time decay, fees,
and non-executable close quotes. Every archive period is previously
inspected, not an independent holdout.

| Period | Fixed policy | Signals per session | Positive SPY proxy days | Negative SPY proxy days | Mean net signed SPY bps / day (2bp hypothetical) |
| --- | --- | ---: | ---: | ---: | ---: |
| Apr–Jul (82 days) | All three setups, common no-overlap clock | **18.09** | 15 | 67 | **−31.97** |
| Apr–Jul | Weighted score >=4 | 16.35 | 10 | 72 | −34.76 |
| Apr–Jul | Weighted score >=6 | 10.91 | 14 | 68 | −23.33 |
| Aug–Sep (25 days) | All three setups | **17.88** | **0** | 25 | **−34.01** |
| Aug–Sep | Weighted >=4 | 16.40 | 2 | 23 | −30.45 |
| Aug–Sep | Weighted >=6 | 10.68 | 5 | 20 | −14.34 |
| Previously inspected Sep–Oct (21 days) | All three setups | **17.10** | 2 | 19 | −31.94 |
| Sep–Oct | Weighted >=4 | 16.19 | 1 | 20 | −36.42 |
| Sep–Oct | Weighted >=6 | 10.76 | 2 | 19 | −32.78 |

**0 zero-signal days** across all nine cells: broadening setups
succeeded at *daily signal coverage* while severely failing the
user's *daily profit objective*. Every model had **negative mean
signed SPY basis points per trading day in every historical
period**, and every fixed variant had substantially more negative
proxy days than positive days.

In Aug–Sep the supposedly better weighted >=6 model still had
**20 negative days out of 25**, average **−14.34 SPY bps/day**
and a 5-session block bootstrap 95% descriptive interval of
**−18.22 to −10.72 bps/day**. That is materially worse than the
always-abstain zero-return proxy.

Some apparent positive trade outcomes can coexist with heavy
daily losses when many weak signals are entered. Thus **more
opportunities are NOT a repeatable edge, and no one should
deploy these policies to a live cash account**. Do not add a
mandatory per-day trade quota, average down, martingale, or
increase risk to chase profitability. Focus next on prospective,
unseen timestamped SPY 0DTE contract bid/ask, fees, fill
uncertainty and genuine *positive option net expected value*;
until the edge is proved, the correct action on weak days is
**do nothing**.

**Outcome:** no profitable research candidate identified;
leave adaptive gates OFF, live trading OFF and existing broker
risk controls unchanged. No actual option P&L, account
drawdown, weekly account growth or percentage daily profit
can be inferred from these SPY price proxies.
