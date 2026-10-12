# SPY 0DTE: Entry with demonstrable net edge — research checkpoint (2026-10-11)

**Conclusion:** The existing entry engine has no validated positive edge.
A prespecified high-volume morning subset produced positive *sampled*
returns, but results depended strongly on a small number of days. A
strict, causal walk-forward admission test rejected **all new trades**.
We must not confuse zero P&L from abstention with a profitable algo.

## Locked exit, all entry experiments

- Stop: **30% trailing drawdown on executable-net-bid proxy**, from
  position high-water mark, whole remaining position closed together.
- **No take-profit targets or partial take-profit/runner sales**.
- Independent initial-premium 35% loss safety limit, ten-minute hard max
  hold. Both are independent of the trailing rule.
- Research quotes are **one-minute archived CBBO buckets**, not independently
  timestamped executable event NBBO; modeled option greeks and incomplete
  SPY PCR. Costs assume $0.65 per contract per side and an adverse
  $0.01/share per side of option slippage.
- All figures one hypothetical contract per accepted signal, without
  cash-account constraints. No licensed data purchases.

The existing archive comprises 128 previously inspected sessions and
49,792 historical minute snapshots, April 1–October 6 2026.
These periods do **not** constitute genuinely untouched holdouts.

## Nine entry tests

Pre-entry factors derived from completed prices and archived volume proxy:
- Original four-factor momentum selection
- SPY minute relative volume >=1.50 or >=2.00
- Historical sampled option relative spread <=8%
- Volume >=1.50 AND spread <=8%
- Volume >=1.50 AND MACD + completed-close price action confirmation
- Pullback only, and pullback with >=1.50 volume
- **Volume >=1.50, signal between 10:00 and 11:59 Eastern time**

No parameter is selected using its own future 10-minute price outcome.
Entry quote spread is checked at the first simulated entry quote, AFTER
which sampling an actual sale remains approximate.

| Rule | Matched cases | Win rate | Average premium return | Summed 1-contract research P&L |
|---|---:|---:|---:|---:|
| Four-factor baseline | 537 | 33.89% | -6.027% | -$3,312.10 |
| Volume >=1.50 | 381 | 34.38% | -4.811% | -$1,920.30 |
| Volume >=2.00 | 200 | 36.50% | -2.362% | -$493.00 |
| Spread <=8% | 537 | 33.89% | -6.027% | -$3,312.10 |
| Volume >=1.50, spread <=8% | 380 | 34.47% | -4.744% | -$1,878.00 |
| Volume >=1.50, MACD+price | 319 | 34.48% | -3.451% | -$1,223.70 |
| Pullback only | 82 | 41.46% | -1.682% | -$250.60 |
| Pullback, volume >=1.50 | 58 | 39.66% | -1.517% | -$202.40 |
| **Morning volume >=1.50** | **81** | **41.98%** | **+5.438%** | **+$290.70** |

These are scenario aggregates, not funded portfolio performance or proof
of repeatable profits.

### Stress test of the initially promising morning variant

| Original pre-existing time segment | Cases | Net hypothetical sum |
|---|---:|---:|
| Development | 54 | **-$202.20** |
| Validation | 11 | **+$104.70** |
| Previously inspected diagnostic | 16 | **+$388.20** |
| All 128 sessions | 81 | **+$290.70** |

Under an additional **$2 per contract round trip** adverse execution
stress the total falls from +$290.70 to **+$128.70**.

The historical largest winning day, **June 9, 2026**, accounts for
**+$273.70**. Omitting that best day shrinks the result to **+$17.00**.
Omitting the three best days makes the aggregate **-$346.10**; omitting
the five best days makes it **-$565.50**.

Its 5-session moving-block bootstrap descriptive 95% uncertainty interval
for *unstressed* one-contract hypothetical dollars **per archived
calendar session**, including days with no trades, is
**[-$3.7500, +$8.6937]**. With the extra $2/trade friction stress
the interval is **[-$5.5547, +$7.4141]**. Both span zero. These
intervals **do not adjust for nine tested variants** or reused research
periods, so they are too optimistic as proof of a newly found edge.

The morning variant's 34 winning trades averaged **+$49.318** and its
47 losing trades averaged **-$29.491**. At those observed payoff averages,
an approximately **37.421%** win rate would break even. Its observed
41.98% rate is fragile because of the concentrated winning dates and
uncertain executable quote times.

### Strict rolling actual-decision feasibility check

For each candidate trading day:
1. Examine ONLY the preceding **30 completed trading sessions**.
2. Require >=30 prior hypothetical matched cases across >=8 distinct days.
3. Debit an **additional $2 per contract** modeled adverse execution.
4. Evaluate each rule's historical daily net P&L, including zero-trade
   days, with a conservative **2.58 standard-error lower-bound hurdle**
   for nine screened candidates. The approximate family-wise correction
   is not a formal exchange fill confidence interval.
5. Select the rule with best **positive** prior-only stressed lower
   confidence bound; if none passes, **NO_TRADE**.

**Observed result: no rule passed on any of 128 sessions.**
The gate entered **0 trades**, with $0 hypothetical P&L.
This is safer than forcing a money-losing trade, but not a positive
trading edge or an income strategy.

## Research findings and next work

1. **Real trading should remain OFF.** Keep 30% full-position trailing
   logic a *research/paper candidate*, not live-proven protection.
2. The frozen **morning volume >1.5 entry** is the best *candidate for
   prospective paper monitoring*, not for enabling live money. Do not
   optimize the timeframe/volume threshold after inspecting its outcomes.
3. Capture *properly observed bid/ask NBBO*, quote size and complete
   real SPY OHLCV as-of data for future sessions using a provider
   with acceptable licensing and spend approval. Complete as-of
   SPY-specific put/call volume is still missing.
4. Calibrate a signal's projected **net bid-executable option premium
   distribution**, not just whether SPY direction seems right.
   Abstain when evidence is insufficient. Log quote spread, delta,
   realized volatility vs implied, setup subtype, signal timestamp,
   event proximity and realized net fills, then challenge the
   hypotheses on wholly **future** sessions.
5. Do not infer an edge from a high raw accuracy or from a sparse
   premium-return sample; require a positive risk-adjusted result
   across separate periods, realistic fee/slippage stress, daily risk,
   cash settlement constraints and a true untouched holdout.

## Files and tests

- `engine/cost_aware_entry_edge.py` — reproducible entry + causal prior-only gate.
- `tests/test_cost_aware_entry_edge.py` — no data leaks into prior gate,
  overfit/no-trade controls, quote spread, and outlier stress tests.
- `research/results/cost_aware_entry_robustness_2026_10_11.json`
  — full vendor-free historical results and exact stress metrics.
- `research/results/cost_aware_entry_edge_2026_10_11.json` — initial run.
- Research CI `exit-policy-research-tests` passes.

No modifications to production trades, UI, owner login, broker accounts,
standing live authorization, or main-branch trading parameters.
