# SPY 0DTE — MACD crossover, RSI, SMA, price action + genuine tape experiment

**Experimental only. No broker orders, no live strategy changes, no purchased market data. Adaptive return-based filter stays OFF.**

## Exact setup frozen before running historical archive

Work on the **same completed-bar 15-prior-CLOSE breakout opportunities**
and **three-minute confirmation / next-completed-minute proxy entry**
from existing research. Score the signed SPY price change over the
following 10 completed minutes on exactly the same timestamps.

**Indicators** calculated solely from previous and current COMPLETE
SPY one-minute close observations, starting at the beginning of each
session, with at least 75 continuous observed one-minute bars:

- **MACD (12, 26, 9):** fast 12-period EMA minus slow 26-period
  EMA; nine-period EMA of the MACD is the signal; each EMA
  starts from an SMA seed after enough bars. Require a MACD line
  crossover above the signal for CALL, below for PUT, within the last
  **five completed 1m bars** and the same-direction MACD sign at
  the three-minute confirmation clock. Don't act on future crosses.
- **RSI (14), Wilder smoothed:** for CALL require 50–75, for PUT
  require 25–50, to avoid extremes and reduce trend-opposing entries.
- **SMA(20)/SMA(50):** for CALL require close>SMA20>SMA50;
  for PUT close<SMA20<SMA50.
- **Price action:** use the existing completed-close breakout boundary,
  observed no snap-back through three confirmation bars, and signed
  three-minute SPY close change >=1 bp, with no interim completed
  close excursion more than 2 bp against initial direction. Because
  the retained archive has no full OHLCV, **this is CLOSE-PRICE ACTION
  PROXY, not real candle high/low, tick pressure or level-two depth.**
- **Original minute volume_ratio >=1.25**: a secondary at-signal
  *volume proxy* filter; MUST NOT be described as tape reading.
- **Genuine tape reading, only if available:** with explicit
  timestamped SPY trade events (action T, aggressor side A/B,
  quantity, trade price), classify side from actual exchange/vendor
  metadata. Within the final 180 seconds up to the confirmation
  timestamp require >=10 signed trades and signed buy-sell size
  imbalance in the direction of the breakout >=20%. Reject any
  quote update/action other than T or a missing/unknown aggressor,
  invalid timestamps/size, mixed symbols or reordered trade rows.
  **Do not infer the tape from SPY spot bars or 1m volume_ratio.**

Databento's trade data defines aggressor side B for buyer and A for
seller when action indicates a trade: see
[Databento MBP-1 schema](https://databento.com/docs/schemas-and-data-formats/mbp-1)
and [trade-side conventions](https://databento.tech/docs/standards-and-conventions).
MACD default (12,26,9) and crossover semantics follow
[Fidelity's MACD technical guide](https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/macd).

## Six fixed side-by-side *research* policies (no tuning or overfitting)

| Policy | Research admission |
| --- | --- |
| `same_delayed_clock_baseline` | Every original breakout that has >=75 continuous prior 1m bars |
| `macd_rsi_sma` | MACD crossover + RSI range + SMA alignment |
| `macd_rsi_sma_close_price_action` | All 3 indicators + 3m completed-close breakout price action |
| `macd_rsi_sma_price_action_volume_proxy` | Above plus contemporaneously observed minute volume ratio >=1.25 |
| `macd_rsi_sma_price_action_true_tape` | Indicators + close action + **actual signed SPY trade tape** |
| `full_fusion_true_tape_plus_volume` | Indicators + close action + volume ratio + actual signed SPY trade tape |

**Critical tape availability rule:** The existing 128 historical
minute-snapshot CSV files do **not** include signed event-by-event SPY
trades or market-by-order. For this zero-new-data run, both actual-tape
policies must report `UNAVAILABLE_VERIFIED_SPY_TAPE`,
**NOT** zero hypothetical wins, fabricated order flow, or estimated
tape outcomes. For a future run, a separately owned/licensed already
available trade feed may be supplied in a CSV:

```csv
ts_event,symbol,action,side,size,price
2026-08-12T10:40:01.123-04:00,SPY,T,B,200,700.45
2026-08-12T10:40:01.456-04:00,SPY,T,A,100,700.44
```

The parser demands the exact fields and explicitly rejects
timezone-naive, unsigned, malformed, or non-SPY records.
The events must be genuine SPY underlying trades and have
*true trade aggressor side*, not guessed from price changes.
An actual 0DTE option order would additionally need event-time option
bid/ask, size, latency, fee and cash-settlement data, even if SPY trade
tape exists.

## Causal walk-forward and score

Do not peek beyond the third completed confirmation bar to calculate
any indicators, volume checks or tape imbalance. **After** selection,
observe the next completed one-minute SPY CLOSE and 10-minute-later
CLOSE as a conservative directional *proxy*, not an executable stock
fill, not an option entry/exit, and not profit.

For Apr–Jul development, Aug–Sep validation, Sep–Oct already-studied
diagnostic report baseline opportunities, accepted, skipped,
direction correct, signed 10m SPY move >2 and >5bps, signed-bps
mean after a *hypothetical* 2bps underlying friction hurdle,
daily signed basis points **including abstention and zero-trade days**,
worst ISO week, additive bps decline (**NOT account maximum
drawdown**), and seeded 1,000-replicate circular 5-day bootstrap
confidence bounds. Real net options return and trading-account max
drawdown are **NULL**.

All of these archives have been previously inspected in this
project; no period can be claimed untouched, even if a signal appears
to work. Indicator combinations increase the number of trials.
Never optimize thresholds on this data and then describe the same
period as independent validation.

## Run using existing research worker

```bash
python -m pytest -q tests/test_macd_rsi_sma_tape_experiment.py
python -m engine.macd_rsi_sma_tape_experiment \
  --data-dir /data/research \
  --output-dir /data/research/indicator_tape_fusion
```

On the isolated Railway `spy-0dte-research` service configure
`RESEARCH_MODE=macdrsisma`; output uses the existing persistent
research volume `/data/research/indicator_tape_fusion`. No
broker API or costly data download occurs.

To optionally evaluate a *genuinely sourced, already-owned* SPY tape
CSV, pass `--verified-spy-tape-csv /data/verified_spy_tape.csv`
(or `RESEARCH_VERIFIED_SPY_TAPE_CSV` on the isolated research
service). **Do not set this merely to a minute snapshot CSV.**
Expected results: `report.json`, and
`MACD_RSI_SMA_spot_event_ledger_NOT_OPTIONS_PNL.csv`.

This cannot establish +100% weekly option returns or a safe maximum
drawdown. Any finding is a price-prediction screening experiment
until real options quote/fill paths, fees and fresh unseen sessions
are available.


## Completed Railway experiment — October 9, 2026

The research-only Railway service completed the **128 historical trading
sessions / 49,792 observed archived minute snapshots**. **2,138**
initial breakout events remained after the preregistered continuous
75-completed-minute indicator warmup. The results were written to
`/data/research/indicator_tape_fusion/report.json` and the
complete event ledger on the existing research volume. No new data
purchase or live broker order occurred. Python CI was green.

### Combined technical indicators versus the same-clock baseline

All price results below are the **mean signed ten-minute underlying
SPY CLOSE-price basis points per admitted opportunity after subtracting
a hypothetical 2bps price hurdle**. They are **NOT actual stock fills,
options P&L, premiums, fees or account drawdown**.

| Rule (predefined) | Development accepted / mean net bps | Aug–Sep validation accepted / mean net bps | Sep–Oct previously viewed accepted / mean net bps |
| --- | ---: | ---: | ---: |
| Same delayed-close baseline | 1,371 / **−1.9420** | 426 / **−1.7953** | 341 / **−2.0239** |
| MACD crossover + RSI + SMA | 233 / **−2.3731** | 68 / **−2.5893** | 53 / **−1.9582** |
| All indicators + 3-minute CLOSE-price action | 119 / **−1.4860** | 27 / **−1.4751** | 21 / **−3.7280** |
| Indicators + close action + minute **volume proxy** | 36 / **−1.5254** | **8 / +1.0255** | **6 / −6.0783** |
| Indicators + close action + verified tick trade tape | **UNAVAILABLE** | **UNAVAILABLE** | **UNAVAILABLE** |
| All above + volume ratio + verified tick trade tape | **UNAVAILABLE** | **UNAVAILABLE** | **UNAVAILABLE** |

**The best-looking validation configuration is misleading if treated
as proven edge.** Eight combined indicator+close-action+volume-proxy
signals predicted the correct SPY direction **6/8 times**. Four of
those eight exceeded the hypothetical 2bps *SPY spot* hurdle, and
three exceeded 5bps. The model averaged **+1.0255 spot bps/event
after subtracting 2bps** during Aug–Sep (25 sessions).

On the **next, previously inspected Sep–Oct 21-session interval**, the
**same exact frozen rule** took six opportunities, correctly predicted
just **2/6 directions**, and averaged **−6.0783 spot bps/event
after the hypothetical 2bps**. Positive-validation apparent
performance **did not generalize**. The small 8- and 6-event
samples cannot establish a repeatable trading advantage.

For the eight-signal validation subset, the seeded 5-session-block
descriptive 95% confidence interval for average signed spot bps
**per day including no-trade sessions** was
**−0.9323 to +1.8126**, including zero. The subsequent six-signal
diagnostic interval was **−3.9015 to +0.5241**, also including
zero. Neither interval establishes reliable positive forecasting
returns, never mind options returns. Reducing the number of signals
mainly decreases exposure to the negative baseline.

### Tape status and proper limits

**Genuine SPY tick tape was NOT present** in any of the 128 existing
minute-summary archives. Both source-dependent tape policies
therefore intentionally report **UNAVAILABLE_VERIFIED_SPY_TAPE**,
rather than inventing aggressor-side volume or pretending the
minute volume ratio is order flow. The standalone tick parser and
as-of timestamp tests are implemented and CI verified for future
already-licensed tape input if ever supplied.

**Conclusion:** MACD/RSI/SMA, completed-close price action, and
volume-ratio confirmation were combined and tested, but **no
repeatable positive SPY underlying price-proxy result survived the
historical period change**. The previously inspected dates are NOT
sealed holdouts. Do not activate the adaptive selector, enable live
trading, increase size or pay for data based on the temporary +8-signal
result. Actual 0DTE expected value still requires correctly timestamped
option bid/ask size, liquidity, adverse fill assumptions, fees,
cash settlement and future untouched observations.
