# V5 max option bid/ask spread — 128-session archived sensitivity (2026-10-28)

**Executed on Railway research volume**, deployment `75b004a5-ce4d-4492-8fba-3d2dffd2c213`; report also saved at `/data/research/v5_spread_sweep_2026_10_28/report.json`. This was research-only and made **no modifications to original live/paper V5 policy**.

Fixed assumptions: V5 IV/RV <= **1.5**, absolute delta **0.35–0.65**, initial equity **$1,000**, 128 reused archival 2026-04-01 to 2026-10-06 sessions, same three close-proxy signals, frozen quote selection, 35% hard stop, 25% trail, 18min hold, one position at a time, cash settlement next session, full-cash EOD drawdown throttle. Only *post-selection maximum sampled option bid/ask spread* changed.

| Max spread | Ending hypothetical equity | Trades | Win rate | Profit factor | True max EOD DD | Max hypothetical contracts/order |
|---:|---:|---:|---:|---:|---:|---:|
| 1.0% | $4,425.30 | 96 | 54.17% | 1.731 | 33.44% | 29 |
| 1.5% | $74,194.40 | 112 | 57.14% | 2.246 | 33.91% | 587 |
| 2.0% | $142,922.80 | 125 | 54.40% | 2.069 | 33.94% | 1,131 |
| 2.5% | $831,948.20 | 124 | 55.65% | 3.605 | 32.24% | 10,771 |
| 3.0% (current) | $930,011.50 | 121 | 54.55% | 3.548 | 32.31% | 12,042 |
| 3.5% | $963,432.00 | 122 | 54.92% | 3.547 | 32.26% | 12,473 |
| 4.0% | $1,021,945.60 | 124 | 54.03% | 3.607 | 32.24% | 13,231 |
| 5.0% | $1,808,494.60 | 121 | 53.72% | 6.621 | 32.24% | 25,612 |
| 6.0% | $1,808,494.60 | 121 | 53.72% | 6.621 | 32.24% | 25,612 |

Baseline $930011.5 reproduces exactly against prior archival 0.35–0.65 delta sweep **on every session**, which ensures same starting model.

## Interpretation and limitations

* Increasing to 3.5%, 4%, 5% improved the **in-sample modeled balance**, but this is not evidence of more executable profit. 5% and 6% are identical in this 128-session sample because no additional selected candidates qualify between those caps.
* A 5% cap produced **$1,808,494.60** on paper while modeling **25,612 contracts in a single order** with no displayed executable size. This is precisely why the large headline result cannot be treated as a profit forecast.
* Looser bid/ask filters mean fewer liquidity safeguards, more slippage exposure and a greater chance of rejected or poor fills, especially in 0DTE options.
* The archive uses minute sampled bid/ask quotes, not time-synchronized, depth-qualified executable NBBO or true intrabar OHLCV, and contains no usable full size/impact model.
* These same 128 sessions have been researched repeatedly; parameter selection from them is overfit. Drawdown is EOD only; intraday losses can be substantially worse.
* Do **not** promote a looser spread cap directly to autonomous live orders based on this retrospective run. Test 3.5% and 4% on new timestamped option quotes with actual depth and market impact, alongside unchanged 3% paper baseline.

## Proof and operational controls

Research source `engine/v5_spread_sensitivity.py` on branch `research/v5-max-spread-sensitivity-2026-10-28`; Railway log lines `V5_SPREAD_META`, `V5_SPREAD_RESULT`, and `V5_SPREAD_REPORT_SAVED`. No broker orders, no new data purchases. Original live V5 still uses 3% max spread.
