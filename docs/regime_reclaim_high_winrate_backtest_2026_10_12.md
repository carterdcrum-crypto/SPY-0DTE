# SPY 0DTE: Regime Reclaim & Stretch Reversion — Actual Backtest
Executed October 12, 2026 on the existing 128-session SPY minute/option archive.

## Verdict

We **did** identify a promising, historically profitable **exploratory**
entry combination: **EMA Trend Reclaim OR Stretch Reclaim, only when
first eligible modeled option quote has <=3.5% bid/ask spread and
option IV / SPY realized volatility proxy <=1.5**.

**$1,000 starting settled cash → $2,942.20 modeled ending equity,
146 one-contract trades, 52.05% historical win rate**, +$1,942.20
modeled account P&L across 128 market sessions. Additional $2/trade
friction lowers additive profit to +$1,650.20. $5/trade stress:
+$1,212.20. $10/trade stress: +$482.20.

**This is NOT a validated 52% hit rate, is NOT a particularly high
win rate by our ambitious criteria, and is NOT a demonstrated
repeatable trading edge.** The Wilson 95% interval on the 146 observed
binary outcomes (which incorrectly assumes independent trades) is
approximately **44–60%**. We tried eight arms against a 128-day
history already studied many times; selecting its biggest winner adds
data-snooping risk. Both the sampled option quotes and 30% trailing
exit executions remain uncertain in the real broker.

Still: unlike most previous research, **we have one promising
positive-after-transaction-cost historical hypothesis** to freeze
and test on entirely new market days.

## Strategy definition (predeclared before this test)

**Direction comes from completed SPY minute closes only**, not
future information or fake full-chain PCR:

- **Trend reclaim CALL**: prior 8-EMA >? not required; 21-EMA
  slope over trailing 10 observations positive >=2bp, previous
  completed close <= prior EMA-8, current completed close > new
  EMA-8 AND above EMA-21, price uptick and 15-minute directional
  efficiency >=18%. Inverse for **PUT**.
- **Stretch reclaim CALL**: previous completed close below
  historical 20-close mean minus 1.2 standard deviations,
  next completed close returns inside frozen lower band with
  upward reversal and <60% trailing direction efficiency.
  Inverse above upper band for **PUT**.
- **Trade only when option quote at entry** has bid/ask spread
  <= **3.5% of mid** and quoted option IV / concurrently
  computed SPY realized-volatility proxy <= **1.5**.
  Both are as-of available heuristic filters, NOT fair-value
  pricing or profitable-fill guarantees.
- The standard ~50-delta, liquidity-eligible option contract
  is chosen **from first observed archive quote one–two minutes
  after completed signal**; exit path is never used to pick it.
- Earliest price-history eligible entry is about **10:06 ET**,
  and latest hypothetical signal time is **15:25 ET**.
  This is an exploratory **whole-day** arm, NOT a replacement
  of the user-requested primary 09:30–11:00 morning strategy.

**All exit rules unchanged: 30% whole-position high-water trailing;
separate 35% initial loss stop; max 10-minute hold; NO fixed
take-profit, no scaling out, no runner.**

One historical 0DTE option contract per trade. Starts with
**$1,000 settled buying power** and accounts for T+1 settlement:
an option debit immediately reduces same-day settled cash,
and modeled sale proceeds are only available next market
session. One open contract max at once. Not real Webull fills,
actual option exercise handling, or an audited brokerage ledger.

## The 8 actually executed arms

| Research arm | Trades | Observed win% | Simulated 1-contract net P&L | Modeled ending $1k equity |
|---|---:|---:|---:|---:|
| EMA trend reclaim only | 107 | 33.64% | -$966.10 | $33.90 |
| EMA trend reclaim with RVOL >=1.5 | 102 | 29.41% | -$955.60 | $44.40 |
| EMA trend reclaim + cost/IV quote gate | 41 | 51.22% | **+$198.70** | $1,198.70 |
| Stretch-band reclaim only | 155 | 38.06% | -$974.50 | $25.50 |
| Failed rolling range re-entry | 139 | 35.97% | -$970.70 | $29.30 |
| Trend OR stretch, no cost filter | 94 | 31.91% | -$974.20 | $25.80 |
| **Trend OR stretch + option cost/IV gate** | **146** | **52.05%** | **+$1,942.20** | **$2,942.20** |
| Trend reclaim only, morning window | 103 | 43.69% | -$702.90 | $297.10 |

**Win% does not automatically imply profitability.** The best
arm's 76 winners averaged **+$47.90**, whereas its 70 losing
or flat positions averaged **-$24.26**. Approximate break-even
hit rate at these specific observed payoff sizes is only
**33.6%** before further stress.

Frequency: **146 entries across 128 trading sessions** =
**1.14 per calendar market day** (74 active days, ~1.97
per active day), **40 days with at least two entries**,
22 days with at least 3, max **five in one day**.
The rule therefore does NOT currently provide many profitable
opportunities every day or support 100% weekly doubling.

## Calendar partition results (NOT an untouched validation)

| Period | Market days | Trades | Win rate | Net P&L | Additional $2 per trade |
|---|---:|---:|---:|---:|---:|
| Development | 82 | 92 | 57.61% | +$1,631.40 | +$1,447.40 |
| Former "validation" (previously inspected) | 25 | 21 | 47.62% | +$170.70 | +$128.70 |
| Former "diagnostic" (previously inspected) | 21 | 33 | 39.39% | +$140.10 | +$74.10 |
| **Total** | **128** | **146** | **52.05%** | **+$1,942.20** | **+$1,650.20** |

The decreasing historical hit rate in the later partitions is
a **significant warning**. The positive later P&L relies on
favorable mean winner/loser sizes, not high hit rate.
The labels validation/diagnostic were **already inspected in
previous research**, so these are descriptive subperiods,
not new out-of-sample confirmation.

## Stress and concentration

| Added option execution cost (each round trip) | Additive historical net P&L | 5-market-session block bootstrap 95% mean USD/market day |
|---|---:|---:|
| $0 additional over already modeled costs | +$1,942.20 | [+6.279,+24.147] |
| $2 additional | +$1,650.20 | [+4.260,+21.970] |
| $5 additional | +$1,212.20 | [+1.192,+18.174] |
| $10 additional | +$482.20 | [-4.391,+12.490] |

**Caveat:** Extra execution-cost stresses subtract a fee
after strategy selection; affordability and subsequent entries
are NOT recomputed at each higher cost. Block bootstrap is
descriptive on a pre-inspected, multiple-tested archive.
Intervals DO NOT correct data snooping, quote-bucket artifacts,
stop-fill latency, or regime drift.

- Best one session **April 23, 2026: +$335.10**.
- Without its top winner day: **+$1,607.10**.
- Without the three largest winning days: **+$1,121.80**.
- **18 positive, 8 negative, 2 no-trade/flat weeks**
  using additive hypothetical option proceeds.
- **0 modeled calendar weeks doubled** the simulated account.
- Peak-to-trough **end-of-day** modeled cash equity drawdown
  **$165.00**, or **16.5% of initial $1,000**;
  not an intraday mark-to-market worst case.
- CALL side: 77 trades, 53.25% win, +$797.90.
  PUT side: 69 trades, 50.72% win, +$1,144.30.
- Stretch: 112 trades, 51.79% win, +$1,621.40.
  Trend reclaim: 34 trades, 52.94% win, +$320.80.
  These are **post-hoc descriptive breakdowns**, not
  additional deployable systems.

## Research precedent (concepts, not evidence that THIS 0DTE strategy works)

1. Gao et al. (2018), *Market Intraday Momentum*,
   Journal of Financial Economics:
   https://doi.org/10.1016/j.jfineco.2018.05.009
   Finds SPY half-hour intraday return momentum in a
   long historical sample; the study does not validate
   our 10-minute option strategy.
2. Iwanaga & Sakemoto (2026), *Does overnight return
   predict the first half-hour return for U.S. market
   indices?*: https://doi.org/10.1016/j.najef.2026.102707
   Presents distinct intraday mean-reversion effects
   and warns some historical signals weaken in newer
   periods; our source data do not contain all premarket
   features.
3. Cboe 0DTE overview and risk discussion:
   https://www.cboe.com/tradable-products/0dte
   Risks include 0DTE near-expiration sensitivity
   and option-bid/ask execution.

## Concrete next steps, no live promotion

**Freeze this exact branch's code and parameters NOW**.
Stop trying more entry conditions against the same 128 sessions.
For a genuinely separate forward test, capture:
- true completed SPY OHLCV (this archive has only minute close);
- event-observed as-of SPY 0DTE option NBBO bid/ask, size,
  expiry and exact quote timestamps;
- every signal, both accepted and declined, without future
  fill information;
- paper ask entry, sellable bid, quote age and actual 30%
  trailing stop events, 35% independent stop and 10m max hold;
- T+1 cash affordability, $1k start, realistic position sizing,
  exercise/assignment risk and option expiration deadlines.

Pre-register actual high-win objective e.g. **>=60% hits AFTER
realistic spreads and fees** with sufficient trades and
positive post-cost payoff expectation, positive multiple
independent weeks and controlled account drawdown.
Do not turn on live autonomous trading until independently
verified and broker-approved.

## Reproducibility

```bash
python -m pytest -q tests/test_regime_reclaim_winrate.py tests/test_regime_reclaim_robustness.py
python -m engine.regime_reclaim_winrate \
 --data-dir /data/research --output-dir /data/research/regime_reclaim
python -m engine.regime_reclaim_robustness \
 --ledger /data/research/regime_reclaim/trades.csv \
 --data-dir /data/research \
 --output /data/research/regime_reclaim/robustness.json
```

Files in branch `research/regime-reclaim-winrate-20261012`:
- `engine/regime_reclaim_winrate.py`: frozen hypothesis entry
  scanner, option quote selection, exits and T+1 cash simulation;
- `tests/test_regime_reclaim_winrate.py`;
- `engine/regime_reclaim_robustness.py`;
- `tests/test_regime_reclaim_robustness.py`;
- `research/results/regime_reclaim_winrate_2026_10_12.json`;
- `research/results/regime_reclaim_robustness_2026_10_12.json`.
- Full 128-session research trade ledger on Railway volume:
  `/data/research/regime_reclaim/trades.csv`.

**No broker trades, data purchases or production Android app
changes were made.**
