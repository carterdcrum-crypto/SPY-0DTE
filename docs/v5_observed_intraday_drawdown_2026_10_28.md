# SPY V5 — observed minute-bid account drawdown study (2026-10-28)

**Completed research-only replay on Railway**, deployment `2c3ec18f-47d7-4d5e-95a6-de5a05472657`, 128 archived option-CBBO sessions from April 1 to October 6, 2026. Code: `engine/v5_intraday_dd_estimate.py`; raw JSON: `/data/research/v5_sampled_intraday_dd_2026_10_28/report.json` on the Railway research volume.

**Caution: This is NOT the true overall intraday max drawdown.** It marks open options at available 1-minute sampled bids after hypothetical exit slippage/fee, not event-time executable bid/depth. There is no intraminute price path, market impact, verified order size, or outside-sample verification.

## Matched model and outcome

Both variants use initial $1,000; V5 full-cash + prior-EOD drawdown throttle; IV/RV <=1.5, abs(delta) 0.35–0.65, sampled bid/ask spread <=5%; 35% hard option-premium stop, 25% premium trailing stop, 18-minute max hold; T+1 proceeds settlement. Only **liquidity reversal entry rule** changes; momentum ignition and volatility expansion are unchanged.

| Statistic | Original 3-signal V5 | Looser liquidity reversal |
|---|---:|---:|
| Hypothetical closing equity | $1,808,494.60 | $2,055,376.50 |
| Scored positions | 121 | 125 |
| Maximum **end-of-day** account DD | 32.24% | 39.11% |
| Maximum **observed minute-bid** peak-to-trough account DD | **58.14%** | **58.14%** |
| Worst high-water equity | $37,753.70 | $37,753.70 |
| Worst observed equity at trough | $15,804.80 | $15,804.80 |
| Worst observed decline | $21,948.90 | $21,948.90 |
| Worst peak timestamp (UTC) | 2026-05-21 17:23 | 2026-05-21 17:23 |
| Worst trough timestamp (UTC) | 2026-06-01 17:41 | 2026-06-01 17:41 |
| Observed option bid marks | 1,036 | 1,086 |
| Expected one-minute marks | 1,039 | 1,089 |
| Minute bid coverage | 99.71% | 99.72% |
| Longest missing minute-quote interval | 1 minute | 1 minute |
| Hypothetical largest single order | 25,612 contracts | 29,107 contracts |
| Largest modeled committed option premium / account equity | 100% | 100% |

The **58.14%** drawdown is identical because the dominant sampled peak-to-trough path occurred on the same dates in both paths. Looser reversal *did increase* the EOD peak-to-trough drawdown by **6.87 percentage points** (32.24 to 39.11).

## Verification

The new replay exactly reproduces both pre-committed *ending balances*, failing closed if either changes, before emitting a performance result. It uses each original entry signal, quantity, exit decision and settled/pending cash account bookkeeping. Each sampled open-option equity mark is `settled cash + pending realized sales + quantity * max(0, (observed bid - $0.01)*100 - $0.65)`. It does **not** infer a favorable price through missing minutes, and it preserves all previous terminal fills.

A worst trade could still lose 100% of premium; absent execution depth, a 25–29k-contract order at one displayed bid is implausible. Even the observed 58.14% figure cannot be described as a reliable maximum drawdown for a live Webull account: the true intraminute trough is unknown, and an alternative live fill path changes the compounding and peak.

**Production/live/paper rules were not changed.** A much better live-risk study would use timestamped depth-qualified executable NBBO quotes and independently observed sessions, plus bounded realistic capacity.
