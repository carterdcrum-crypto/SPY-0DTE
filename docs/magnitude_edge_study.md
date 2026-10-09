# SPY price-move magnitude vs directional accuracy — zero-new-data research

Frozen before inspecting historical results. **Not an options
profit backtest**. All signals are the prior close-based breakout (not
OHLC ORB) evaluated with a three-minute causal confirmation, and the
exact same delayed next-bar entry and 10-minute exit observation clock.

## Current experimental setting — adaptive signal gate OFF (October 9, 2026)

The **two retrospective 40-prior-session expected-return signal gates**
are now **disabled by default**. They previously admitted **0 signals**
in every historical period. They are not required for the five static
research comparisons and are not connected to live order execution.

By default the experiment runs **five static, previously specified**
signals only: all-breakouts baseline, forecast SPY absolute movement
>=5bps, forecast >=10bps, confirmed continuation, and confirmed
continuation plus forecast >=5bps. The existing static confirmation
and magnitude rules are **not** relaxed or retrained.

To explicitly reproduce the prior historical **seven-policy research**
comparison, add `--include-adaptive-research` to the CLI. This flag
is **offline analysis only**: it neither connects Webull nor starts
paper or live orders. Without the flag, there is **no evaluation
of trailing 40-session adaptive eligibility** and no adaptive
decisions in the signal ledger.

**This does NOT disable essential live risk controls:** auto-trading
remains OFF, and broker safety, position/capital limits, settlement
and any independent live safeguards are untouched. Turning off an
overly strict unvalidated signal selector is not evidence the
remaining signals have positive expected 0DTE options returns.

## Main question

Even when SPY moves in the predicted direction, did it move enough
to clear a modest (hypothetical) 2- or 5-basis-point *underlying*
hurdle? Can short-horizon price magnitude/volatility identify
opportunities with potential without inventing executable option
break-even, theta, spreads, IV or trading profit?

## Causal magnitude forecaster

At end of confirmation minute, compute the sample standard deviation
of the previous **30 completed one-minute SPY close-to-close returns**
in basis points. Under a zero-drift Gaussian random-walk
approximation, predeclare

`predicted_abs_10min_spy_bps = sigma_1min_bps * sqrt(10) * sqrt(2/pi)`

No future prices, no same-day outcome labels, no hindsight coefficient
fit. This is an intentionally simple probabilistic **absolute SPY
price movement** baseline: it **does not predict direction** and does
not imply a CALL or PUT could be purchased for that many basis
points. Realized volatility can be a bad forecaster, especially around
news and regime changes.

Two secondary historical adaptive screens train exclusively on the
last **40 completed trading sessions**, with at least 30 other
close-breakout events in the **same ex-ante forecast magnitude bucket**
(<5 or >=5bps) for the basic model, or at least 8 confirmed
continuation events for the confirmed model. On that strictly
lagged training set compute the average signed 10m SPY bps after a
hypothetical 2bps hurdle, and an intentionally conservative
one-sided normal-bound screen `mean - 1.645*stdev/sqrt(n) > 0`.
Also require current forecast abs move >=5bps. Samples are small and
model misspecified: this lower bound is an exploratory fail-closed
research screen, NOT a validated statistical guarantee.

## Seven originally tested alternatives (five static by default; two adaptive opt-in)

1. `same_clock_delayed_baseline`: act on every original 15-prior-close
   breakout in the original direction.
2. `spot_magnitude_ge_5bps`: only breakouts with expected
   10m SPY absolute move >=5bps.
3. `spot_magnitude_ge_10bps`: only breakouts with expected
   absolute move >=10bps.
4. `past40_lowerbound_plus_magnitude`: (2) PLUS prior completed
   40-session *same forecast-magnitude-bin* signed net-bps
   lower bound >0, >=30 samples.
5. `confirmed_continuation_baseline`: reproduce the prior frozen
   three-minute-confirmed continuation rule.
6. `confirmed_continuation_plus_magnitude`: (5) AND forecast
   magnitude >=5bps.
7. `confirmed_continuation_past40_lowerbound`: (6) PLUS lagged
   40-session confirmed continuation lower bound >0, >=8 samples.

All 7 represent direction-only, hypothetical SPY spot forecast
outcomes. **Abstain=zero**. Any comparison must beat **doing nothing**
and remain positive in *all historical regimes*, rather than simply
do less poorly than an overtrading negative baseline. Testing seven
filters on the same prior-inspected dataset creates multiple comparisons:
a positive retrospective cell alone does not constitute edge.

## Metrics and discipline

Report separate counts (a) *correct SPY direction* (signed 10m >0),
(b) *SPY moved >2bps in correct direction*, and (c) *>5bps*;
those are all SPY underlying price, **NOT 0DTE option profitability**.

For each of Apr-Jul development, Aug-Sep validation and Sep-Oct
already studied diagnostic, show trade counts, accepted and skipped,
direction wins, successful movement beyond both hurdles, forecast
magnitude versus true realized absolute 10m movement (and MAE),
mean net signed SPY bps after 2 and 5 hypothetical basis-point
hurdles, **net signed bps per trading session including no trades**,
worst session, worst week, and maximum peak-to-trough *additive signed
SPY basis point* decline (**NOT account drawdown**).

Seeded 1,000-resample circular 5-session bootstrap with no-trade days
included; if uncertainty crosses zero, do not claim edge.
Predeclare two bins in calibration (<5, >=5 forecast abs bps) and
show actual absolute move, rate >5 absolute, rate of signed moves
>2 and >5. None is a broker entry-price or premium return.

**No truly untouched holdout exists in this archive.**
Every previously viewed period remains labeled accordingly.
No live/Android/Webull engine changes. No new market data
purchase is authorized. Note Railway compute may incur hosting
charges, not additional purchased market data.

## Run

```bash
python -m pytest -q tests/test_magnitude_edge_study.py
python -m engine.magnitude_edge_study \
  --data-dir /data/research \
  --output-dir /data/research/magnitude_edge
```

On the *isolated* Railway service `spy-0dte-research` set
`RESEARCH_MODE=magnitudeedge`. Persistent research-volume output:
`results.json`,
`ALL_EVENT_PAST_ONLY_GATE_DECISIONS_NOT_OPTIONS_PNL.csv`, and
`volatility_magnitude_calibration_NOT_OPTION_BREAK_EVEN.csv`.

No deployment to production trading service, no autonomous orders.


## Completed 128-session Railway experiment — October 9, 2026

The isolated Railway worker successfully processed **128 sessions /
49,792 archived one-minute observations** containing **2,473
preexisting non-overlapping same-clock close-breakout opportunities**.
It wrote the complete causal decision ledger, the spot magnitude
calibration file and the JSON report to the persistent research
volume at `/data/research/magnitude_edge`. No additional market
data was purchased, no broker order was placed, GitHub CI was green.

### Does the market move far enough?

**Correct direction** (any positive signed spot movement) is not
the same as an underlying move exceeding a hypothetical 2bps or
5bps, and neither is proof of profitable SPY 0DTE options execution.

| Period | Fixed strategy | Accepted | Correct SPY direction | SPY signed move >2bps | SPY signed move >5bps | Mean signed SPY bps/event after hypothetical 2bps |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Apr–Jul development (82 sessions) | Delayed baseline | 1,582 | 808 | 624 | 394 | −1.9617 |
| Dev | Forecast absolute 10m SPY move >=5bps | 1,023 | 521 | 429 | 297 | −2.0076 |
| Dev | Forecast absolute 10m SPY move >=10bps | 277 | 140 | 124 | 102 | −2.2602 |
| Dev | Frozen confirmed continuation | 31 | 19 | 14 | 7 | −0.9534 |
| Dev | Confirmed + forecast >=5bps | 21 | 13 | 11 | 7 | −0.5580 |
| Aug–Sep validation (25 sessions) | Delayed baseline | 497 | 268 | 180 | 85 | −1.7298 |
| Validation | Forecast absolute 10m SPY move >=5bps | 166 | 81 | 59 | 37 | −2.2161 |
| Validation | Forecast absolute 10m SPY move >=10bps | 9 | 5 | 4 | 4 | −0.8614 |
| Validation | Frozen confirmed continuation | **13** | **10** | **7** | **5** | **+1.0317** |
| Validation | Confirmed + forecast >=5bps | **2** | **1** | **1** | **1** | **+0.5262** |
| Sep–Oct prior-studied diagnostic (21 sessions) | Delayed baseline | 394 | 186 | 137 | 65 | −2.1314 |
| Diagnostic | Forecast absolute 10m SPY move >=5bps | 188 | 88 | 74 | 43 | −2.2134 |
| Diagnostic | Forecast absolute 10m SPY move >=10bps | 16 | 7 | 5 | 5 | −2.5242 |
| Diagnostic | Frozen confirmed continuation | **7** | **3** | **2** | **0** | **−3.8674** |
| Diagnostic | Confirmed + forecast >=5bps | **5** | **2** | **1** | **0** | **−4.1325** |

**Both** `past40_lowerbound_plus_magnitude` and
`confirmed_continuation_past40_lowerbound` abstained on **every
historical signal in all three periods** (0 accepted). Under these
preregistered requirements, the prior-session signed-return data
never gave sufficient positive evidence above the 2bps hypothetical
hurdle. Taking zero trades produced **zero signed SPY basis points**
per session. It is **not** a claim of trading profits or lower actual
account max drawdown.

### Volatility magnitude forecast *can* anticipate large absolute movement

The simple 30-completed-minute SPY historical volatility forecast
produced a relationship between expected magnitude and **realized
absolute underlying SPY movement**, but it did NOT identify an
economic directional advantage.

| Period | Forecast magnitude group | Historical breakout events | Actual absolute SPY move >5bps | Signed SPY move >5bps in predicted direction |
| --- | --- | ---: | ---: | ---: |
| Development | Forecast >=5bps | 1,023 | 58.46% | 29.03% |
| Development | Forecast <5bps | 559 | 32.20% | 17.35% |
| Validation | Forecast >=5bps | 166 | 47.59% | 22.29% |
| Validation | Forecast <5bps | 331 | 22.66% | 14.50% |
| Previously studied diagnostic | Forecast >=5bps | 188 | 47.34% | 22.87% |
| Diagnostic | Forecast <5bps | 206 | 21.84% | 10.68% |

This supports the *limited* conclusion that trailing SPY
volatility contains descriptive information about the **scale**
of later SPY changes. It **does not imply option premium returns
are profitable**, and it does not reliably identify the **sign**
of the price change. Direction matters for a purchased call/put.

**Key false-positive check:** validation's 166 high-forecast-move
breakout directions still averaged **−2.2161 SPY bps/event after
the hypothetical 2bps**. In the later diagnostic, 188 high-forecast
signals still averaged **−2.2134 bps/event**. Increasing absolute
volatility alone selects *larger winners AND larger losers*.

The 5-session-block bootstrap 95% interval of **net signed SPY bps
per validation trading session** for high-forecast-magnitude
breakouts was **−22.8597 to −7.6901**, wholly below zero. For
confirmed continuation plus >=5bps forecast, the very small
validation sample (2 events) gave **−0.2950 to +0.4213**,
including zero; later diagnostic was **−1.8447 to −0.2191**,
wholly below zero.

### Decision

- Keep these indicators in the **offline signal research engine**,
  not the autonomous options execution engine. The volatility
  forecaster is useful as a descriptive magnitude feature,
  **not a discovered directional options trading edge**.
- The previously highlighted **10/13** rate declines to
  **7/13** SPY forecasts beyond a 2bps hurdle and **5/13**
  beyond 5bps; in the later period **0 of 7** cleared 5bps.
- Magnitude gating cut the validated 13 confirmed signals to
  **just 2**, one with correct direction. The later period saw
  five, only two correct. This does not solve regime instability.
- No measured positive strategy survived all periods with
  sufficient event counts; the strict past-only expected-return
  gate made the risk-aware decision **not to trade** on these data.
- Market-data incremental spend: **$0**; Railway service itself
  remains subject to ordinary existing hosting charges.
- To move beyond underlying-proxy diagnostics, freeze the entry
  policies and collect unseen future sessions with reliable
  event-time 0DTE option quotes, spread, size, fees, cash settlement,
  volatility and event-day coverage, then run truly executable
  option P&L and account drawdown. Nothing supports +100% weekly
  repeatable account growth yet.
