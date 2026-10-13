# V4 entry-signal quality dynamic-sizing experiment — October 12, 2026 (CT)

**Executed on Railway research archival volume**, deployment `15060511-1b59-4aeb-aa2d-e3990af24f15`.
128 previously inspected sessions from **April 1–October 6, 2026**.
Starting T+1 settled cash $1,000. Original V4 entry signal,
option selection and conditional hold/stop exit are **unchanged**.
Only integer number of *long SPY 0DTE option contracts* differs.

| Variant | Trades | Contracts | Win rate | Modeled net P&L | Ending modeled cash | Max EOD drawdown ($) | Worst EOD DD as % of its preceding peak |
|---|---:|---:|---:|---:|---:|---:|---:|
| Fixed-size V4 | 146 | 146 | 53.42% | $2173.20 | $3173.20 | $177.00 | 5.71% |
| Entry quality 2x | 146 | 231 | 53.42% | $3491.70 | $4491.70 | $227.60 | 5.12% |
| Entry quality 3x | 146 | 265 | 53.42% | $4109.50 | $5109.50 | $238.90 | 4.81% |
| 2x without drawdown veto | 146 | 246 | 53.42% | $3692.20 | $4692.20 | $259.80 | 5.61% |

**Core finding:** The 3x quality multiplier increased modeled net
profit from **$2,173.20 to $4,109.50**, but increased **absolute maximum
end-of-day drawdown from $177.00 to $238.90** (+$61.90).
Relative drawdown as a fraction of the preceding modeled peak was
lower (5.71% vs 4.81%), largely because the equity base grew. Neither
is the **maximum intraday loss** or a reliable risk ceiling. Win rate
stays **53.42%**, as sizing does not affect the set of trades
selected here (all 146 score).

The 2x variant yielded +$3,491.70 / $227.60 EOD DD, and without a
4% previous-day drawdown veto yielded +$3,692.20 / $259.80 DD.
So the drawdown veto improved worst dollar drawdown versus its
2x-without-veto comparison, but it did not reach the original fixed-size
drawdown ceiling of $177.00.

## Entry-quality score — exploratory, not calibrated

A six-point additive score uses ONLY as-of candidate/selected quote:
completed SPY trend/efficiency, signal RVOL >=1.2, bid/ask spread
<=2%, |delta| between .45 and .55, IV/RV <=1.25. For strong score>=4,
the 2x version purchases two contracts **if** settled cash and 35% of
open equity premium budget permit and previous-session EOD drawdown <=4%.
For 3x, scores>=5 may purchase three; score>=4 may purchase two.
Otherwise it remains at one contract. No pending sale proceeds are
recycled within the same day. Fees/slippage are applied separately per contract
($.65 per side, $0.01 per option share slippage per side).

**Post-run score-bucket diagnostic (fixed-size):**

| Entry score | Number of trades | Win rate | Average modeled net P&L / contract |
|---|---:|---:|---:|
| 2 | 9 | 44.44% | $0.59 |
| 3 | 33 | 51.52% | $20.06 |
| 4 | 61 | 49.18% | $10.77 |
| 5 | 37 | 67.57% | $23.62 |
| 6 | 6 | 33.33% | $-4.13 |

**Important:** Score **6** did worse than score **5** in this archive
(only six score-6 cases); high entry quality is not a
statistically validated estimate of profitability or winning
probability. This dataset has been examined repeatedly for prior
V4 studies; an apparent benefit from this rubric may simply be
overfitting. No parameter selection or extra optimization
is justified from these same sessions.

The model uses SPY completed-close proxy and minute-bucket historical
option CBBO quotes. It cannot provide actual OPRA event-time
executable bid/ask fills; size-dependent slippage and quote depth,
intraday/account drawdown, gaps, forced liquidation and risks of
total premium loss are not characterized. A 35% modeled stop
does **not** cap the actual maximum option loss.

**Research-only decision:** The 3x entry-quality arm is a promising
forward-test candidate, **not ready for live auto trading**.
Our stated risk goal of drawdown no worse than fixed-size V4
is not achieved **in dollar terms**. True fresh timestamped OPRA
event quotes + full SPY OHLCV are still absent from the forward watcher.
No orders were placed and no market data were bought.

[Full JSON report](v4_quality_sizing_128_sessions.json).
