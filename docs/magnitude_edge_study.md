# SPY price-move magnitude vs directional accuracy — zero-new-data research

Frozen before inspecting historical results. **Not an options
profit backtest**. All signals are the prior close-based breakout (not
OHLC ORB) evaluated with a three-minute causal confirmation, and the
exact same delayed next-bar entry and 10-minute exit observation clock.

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

## Seven FIXED alternatives, same delayed clock and same opportunities

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
