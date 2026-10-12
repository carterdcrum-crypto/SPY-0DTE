# SPY 0DTE: Faster Opening Retests vs Profitable Frequency
Executed on the archived 128-session research volume, October 11–12, 2026.

## Executive conclusion

We implemented and **ran eleven predeclared research arms** for 5-minute
and 15-minute opening **CLOSE-range** breaks, retests, and
volatility/option-cost filters. The simulator permits a **fresh trade
after an earlier modeled position actually closes**, rather than
locking out all subsequent entries for a fixed 10 minutes.

**More trades were generated; NONE of the eleven arms produced
positive full-sample one-contract option P&L.** The 5-minute direct
breakout produced **144 trades / 128 market days (1.13 per day)**,
compared with only 14 trades for the legacy existing four-factor
09:30–11:00 ET high-volume subset. Its simulated net total was
**-$1,165.20**. The 15-minute retest had the strongest
historical win rate of the reasonably sampled research arms (48%
over 25 trades) but still lost **-$120.50**.

No results establish a repeatable profitable setup, much less a
dependable **+100% account return each week**.

## Frozen exits: no profit targets

All eleven arms use:
- Full-position **30% trailing stop** from peak sampled bid net credit;
- separate independent **35% initial premium loss stop**;
- forced exit at **10 minutes**;
- **NO fixed take-profit**, no scaling out, no runner.
- Entry at modeled option ask with extra $0.01/share slippage and
  $0.65 per-contract fee per side, and later modeled bid exit with
  analogous costs.
- Independent **additional $2 per completed roundtrip** stress shown.

Market clock is America/New_York. Primary tests honor the 09:30–11:00
intent, with the first actual 5-minute-close-range eligibility around
09:36 and 15-minute around 09:46.
Three additional arms **explicitly expand** eligibility through
15:25 ET to test the request for more daily chances, without changing
the primary 09:30–11:00 research policy.

## Strategy construction

The data have close only, NOT high/low or tick VWAP.
Consequently the reference levels are the **maximum and minimum
of the first five or 15 completed SPY minute CLOSES**, never a
real exchange OHLC opening-range high or low.

**Direct break**: first completed bar closes outside this range, with
relative volume >=1.25.

**Confirmed retest**: after the direct break, a *separate later*
completed bar must return near its former range level
(within +3 or -7 basis points depending on side), then a
*third distinct completed bar* must reclaim the level by >=1bp
with direction-aligned SPY close movement and relative volume >=1.25.

After the old range is recaptured on two completed closes, the
scanner can arm another breakout event; never read future labels.

**Volatility-aware option variant**: requires sampled option IV /
concurrent modeled realized-volatility proxy <=1.5. A distinct
**execution-cost quality** variant requires <=8% quoted spread and
a previous-five-minute directional delta-move proxy at least 1.5
times the modeled bid/ask + fee/extra-cost friction. These are
**heuristics**, NOT a mathematically calibrated option fair-value
forecast.

Option selection retains the pre-existing research 0DTE roughly
50-delta screen and existing quote liquidity filters; it does not
pretend cheap options are all available to a very small account.

## Actual result: 128 historical sessions

| Research entry arm | Trades | Trades/day including no trade | Max trades in a day | Observed win rate | P&L per hypothetical one contract summed | Additional $2/trade cost stress |
|---|---:|---:|---:|---:|---:|---:|
| Direct 5-minute range break, 09:30–11 | 144 | **1.125** | 4 | 35.42% | **-$1,165.20** | -$1,453.20 |
| Direct 15-minute break, 09:30–11 | 128 | 1.000 | 4 | 33.59% | -$1,033.40 | -$1,289.40 |
| 5-minute break **and confirmed retest**, morning | 35 | 0.273 | 2 | 31.43% | -$588.50 | -$658.50 |
| 15-minute break **and confirmed retest**, morning | 25 | 0.195 | 1 | **48.00%** | -$120.50 | -$170.50 |
| 5-minute retest + RVOL >=1.5 | 25 | 0.195 | 2 | 24.00% | -$444.50 | -$494.50 |
| 5-minute retest + option cost proxy filter | 28 | 0.219 | 2 | 32.14% | -$546.40 | -$602.40 |
| 5-minute retest + modeled IV/RV <=1.5 | **8** | 0.062 | 2 | 37.50% | **-$7.40** | -$23.40 |
| 5-minute hybrid direct OR retest, morning | **145** | **1.133** | 4 | 35.86% | **-$1,087.50** | -$1,377.50 |
| 5-minute retest allowed through 15:25 | 72 | 0.562 | 4 | 31.94% | -$579.60 | -$723.60 |
| 15-minute retest through 15:25 | 63 | 0.492 | 3 | 38.10% | -$304.90 | -$430.90 |
| 5-minute retest + cost proxy through 15:25 | 62 | 0.484 | 3 | 32.26% | -$537.60 | -$661.60 |

**Frequency tradeoff:**
- 5-minute direct strategy traded on **95 of 128 days**; 37 days
  had >=2 entries, nine days had >=3, with a maximum of four/day.
- The hybrid also traded on 95 days; 38 days had >=2,
  nine had >=3, maximum four/day.
- In the 5-minute direct setup, we achieved higher frequency
  but **lower total simulated profitability** than the 14-trade
  late-morning legacy setup. Trade frequency is not the objective
  function; risk-adjusted net expected value is.
- Although the 15-minute retest often abstained, its smaller average
  loss did **not** produce profit.

**All periods were previously studied**; apparent regime gains
are not genuinely untouched out-of-sample evidence. The five-session
block bootstrap per-day friction-stressed uncertainty for the 15-minute
retest includes negative outcomes. More detail in the JSON results.

## IMPORTANT: cash account feasibility with a hypothetical $115 balance

The research models **re-entry after a trade closes**, which is
**not the same as reusing sales proceeds immediately**.

On the user's desired Webull-style **cash account**, securities
and options generally settle **T+1**. Webull states day-trading
unsettled-fund purchases can produce Good Faith Violations:
https://www.webull.com/help/faq/11160-Buying-Power
https://www.webull.com/help/faq/10775

A separate **research-only, post-hoc T+1 settled-cash overlay**
starts at an illustrative $115 settled cash, independently per
research arm. It uses one modeled contract per accepted entry and
holds sale proceeds out of same-day buying power until next
market session. This **is not a full account-level backtest**
because earlier unaffordable hypothetical trades had already
participated in the no-overlap signal selection. It does not
rebuild alternate signal selection, and starting $115 is NOT
an observed current user balance. Historic quotes/fills are
not execution-proven.

| Policy | Already-selected candidate trades | Candidate trades affordable under the $115 T+1 diagnostic | End simulated cash equity | Weeks with >=100% simulated growth |
|---|---:|---:|---:|---:|
| 5-minute direct | 144 | **69** | **$76.30** | **2 of 28** |
| 15-minute direct | 128 | 36 | $59.20 | 0 of 28 |
| 5-minute direct/retest hybrid | 145 | **70** | **$84.00** | **2 of 28** |
| 15-minute morning retest | 25 | 7 | $106.90 | 0 of 28 |
| 5-minute morning retest | 35 | 12 | $89.40 | 0 of 28 |
| 5-minute morning retest + modeled IV/RV filter | 8 | **6** | **$178.20** | **0 of 28** |
| 5-minute all-day retest | 72 | 22 | $63.40 | 0 of 28 |

**Do NOT infer the 6-trade $178.20 line has real edge.** A
cash-after-filter calculation can selectively skip modeled
losers, and on a tiny sample can give the opposite sign
from the unfiltered eight-trade study (-$7.40). It is
NOT an independently established positive account return.

Even arms that happened to double the illustrative $115
equity in **two** individual historical weeks ended
BELOW their initial $115 after the full sequence. No
arm produced repeatable weekly doubling, and the capital
constraints further limit same-day frequency.

The extra-$2-friction overlay simply subtracts the number
of accepted trades times $2 from resulting equity; it is a
**diagnostic**, NOT a recomputed affordability path after
changed slippage.

## What we should actually build next

To meaningfully increase opportunity count while pursuing
positive net EV rather than forcing transactions:

1. **Acquire real opening OHLCV + event-time options NBBO/size
   prospectively**, with as-of timestamped observation and
   paper fill outcomes. The current legacy history cannot
   validate genuine opening HIGH/LOW retests.
2. **Segment trading opportunities by actual spread/fee friction,
   expected delta move and regime**, train only on previously
   finished sessions; abstain when edge net of costs is absent.
3. **Combine non-overlapping morning and afternoon/event-day
   setups ONLY when each has independently demonstrated positive
   net expectancy**. Do not add more losing setups to
   increase trade count.
4. **Simulate real settled cash, premium affordability, exercise
   cutoff, end-of-day risk, T+1 constraints and actual fills**
   before authorizing any autonomous trading.

### Evidence limitations and repo files

- 128 sessions / 49,792 pre-used SPY minute-close frames.
- Option bid/ask observations are **CBBO-1m buckets**, not
  executable true event NBBO; liquidity depth and intraminute
  stop fills unverified.
- No genuine 0DTE full-SPY-chain intraday put/call volume available.
- Cumulative trade P&L assumes one hypothetical contract per
  historical admitted signal, NOT real account growth.
- Twelve-plus archived candidate comparisons already made:
  severe post-selection/multiple-testing fragility.
- No orders were placed, market data purchased, or production
  live/paper automation changed.

Implementation:
- `engine/or_retest_frequency.py`
- `tests/test_or_retest_frequency.py`
- `research/results/or_retest_frequency_2026_10_11.json`
- `engine/settled_cash_ledger_audit.py`
- `tests/test_settled_cash_ledger_audit.py`
- `research/results/settled_cash_115_audit_2026_10_11.json`
- Raw per-signal output on the already configured Railway
  research volume `/data/research/or_retest_frequency/simulated_trades.csv`
- Frozen 30% trail / no fixed take-profit, consistent with
  `engine/trailing_only_sweep.py`.

**Verdict: NO LIVE PROMOTION.** Market research branch only.
