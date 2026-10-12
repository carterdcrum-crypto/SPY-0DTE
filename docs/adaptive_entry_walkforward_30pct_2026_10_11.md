# SPY 0DTE: Adaptive two-expert entry walk-forward (Oct 11, 2026)

**Actual executed historical experiment**, not a prospective test or account
return. No paid market data calls or broker orders.

## Question

Can an adaptive probability model combining high-volume morning momentum
with confirmed trend pullbacks produce a robust, net-profitable SPY 0DTE
entry strategy — ideally one with a calibrated 75% winning probability —
while leaving the exit as a **30% high-water trailing stop**?

**Result: not demonstrated.** The strict model rejected all 75%-threshold
trades. An expected-profit gate took 9 hypothetical trades, won 5
(55.56%), but lost money. A select-all two-expert reference looked
profitable in an inspected 68-session segment, but was not stable across
development, validation and diagnostic slices.

## Frozen research conditions

- One SPY 0DTE options contract per signal, simulated full-position exit.
- **Trailing 30% off peak modeled net exit value**, without targets,
  scale-out sales or runners.
- Independent 35% premium loss stop and ten-minute time exit remain.
- $0.65 per-contract per side, plus adverse $0.01/share per side.
  Additional explicit **$2 round-trip** friction stress reported separately.
- 128 previously inspected sessions, 49,792 minute frames, SPY
  minute-bucket OPRA CBBO; NOT fully observed event-time executable NBBO.
- No full-chain as-of SPY put/call volume, verified fill sizes, real
  cash-account portfolio or true out-of-sample data. Complete
  five-factor model NOT tested.
- No strategy branch alters main, paper positions or live trading.

## Experimental entry construction

Expert A: confirmed **SPY breakout morning momentum**, relative
volume >=1.5, from 10:00 to 11:59 Eastern.

Expert B: **trend pullback/reclaim**, confirmed completed-close price
action plus MACD alignment.

Each candidate's feature vector was computed from as-of signal
closes, time, relative volume, indicator votes, and its first
entry-observation option quote (delta, premium, spread). No option
exit outcomes are passed to entry decisions.

The logistic classifier (pure Python, L2-regularized; no trained
external model) is fitted anew each day on the **preceding 60 finished
market sessions**, requiring at least 30 prior matched trades. No
training uses any label from the trading day under evaluation. The
model's win probability is combined with *prior-only* shrunken average
winner/loser payouts to estimate expected value after an extra $2
friction stress.

68 historical sessions followed the first 60-session warmup. For
comparison below **all policies use those same 68 calendar sessions**
(including no-trade days).

## Completed 68-session post-warmup outcomes

| Research entry policy | Trades | Win rate | Sum modeled option P&L | Sum with +$2/trade friction stress |
|---|---:|---:|---:|---:|
| Both experts, unfiltered reference | 45 | 46.67% | **+$112.50** | +$22.50 |
| Morning momentum expert only | 32 | 46.88% | -$25.60 | -$89.60 |
| Confirmed trend pullback expert only | 13 | 46.15% | +$138.10 | +$112.10 |
| Logistic predicted win >=45% | 8 | 37.50% | -$37.40 | -$53.40 |
| **Positive predicted *net* expected value** | **9** | **55.56%** | **-$16.70** | **-$34.70** |
| Probability >=60% AND positive expected value | 0 | N/A | $0 | $0 |
| **Probability >=75% AND positive expected value** | **0** | **N/A** | **$0** | **$0** |
| Prior evidence-of-positive-profit gate | 0 | N/A | $0 | $0 |

NOTE: "$0" in a 0-trade arm **does NOT** signify profitable performance;
it means no signals had sufficient evidence. The "pullback-only" 13-case
P&L is a particularly small, *already-inspected* and unstable sample:
- Development (22 post-warmup sessions): 5 trades, -$12.50.
- Validation (25 post-warmup sessions): **zero** trades.
- Previously inspected diagnostic (21 sessions): 8 trades, +$150.60.

Both-experts reference:
- Development: 14 trades, -$87.20.
- Validation: 11 trades, +$104.70.
- Previously inspected diagnostic: 20 trades, +$95.00.

The extra-friction five-session-block descriptive uncertainty interval
for both experts' hypothetical USD **per post-warmup calendar day** is
**[-$7.4118, +$7.6074]**, including zero.

The model's expected-value-gated 9 trades:
- Development: 4 trades, -$38.20.
- Validation: 3 trades, -$10.90.
- Previously inspected diagnostic: 2 trades, +$32.40.
Their extra-friction five-session-block per-day interval is
**[-$2.2544, +$0.9515]**, including zero.

## Does the predicted win percentage mean anything?

The daily model produced **45** historical post-warmup
point-in-time predictions.

- Test Brier loss = **0.259132** (lower is better).
- Observed 21/45 wins = **46.67%**.
- A naïve constant 46.67% prediction on these same 45 labels would have
  Brier loss **about 0.2489**. So this probability model did
  **worse than that simple descriptive reference**.
- **Zero** predictions reached 75%.
- Two predictions between 60% and 75% confidence both lost.
  This is much too small for a reliable calibration finding.

**Do not represent a 75% threshold as a 75% realized win rate**; the
experiment supplied zero applicable trades.

## Interpretation and decision

These findings **do not support promoting the adaptive ensemble to
production or live trading**. The prior-only confidence scores are not
calibrated, the 68-session post-warmup sample is historically
inspected, and quote timestamps and fill liquidity remain approximated.

Keep **30% trailing-only exits as a research/paper candidate**,
but **leave production main and live automation unchanged**.

Next meaningful action: collect a new prospective stream of
*real* as-of SPY OHLCV, options bid/ask timestamp and size, and
logged paper fills with commissions and slippage, then freeze and
evaluate the current morning and pullback entry definitions against
untouched upcoming sessions. Improve model capacity **only after**
adequate training examples; do not lower win thresholds or cherry-pick
subsets in response to these results.

## Reproduction

```bash
python -m pytest -q tests/test_adaptive_entry_walkforward.py
python -m engine.adaptive_entry_walkforward \
  --data-dir /data/research \
  --output-dir /data/research/adaptive_entry_walkforward
```

Results: `research/results/adaptive_two_expert_entry_2026_10_11.json`.
Source: `engine/adaptive_entry_walkforward.py`.
Regression suite: `tests/test_adaptive_entry_walkforward.py`.
