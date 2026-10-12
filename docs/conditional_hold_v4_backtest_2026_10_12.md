# V4 conditional 10-minute hold-extension backtest — October 12, 2026

**Executed** on Railway `spy-0dte-research` using the archived
**128 inspected SPY 0DTE sessions**, April 1–October 6, 2026.
This is an exploratory replay of the original frozen Regime Reclaim
entry/cost policy; the archive does not contain the underlying OHLCV/VWAP
required to validate the full V2/V3 entry router. Results use **CBBO-1m
bucket option quotes, not event-time executable OPRA NBBO or broker fills**.

## Historical modeled results ($1,000 T+1 cash account)

| Policy | Trades | Win rate | Net profit | End modeled balance | Max EOD drawdown (USD) | Peak-relative max EOD drawdown |
|---|---:|---:|---:|---:|---:|---:|
| Frozen original 10-min / 30% trail | 146 | 52.05% | $1942.20 | $2942.20 | $165.00 | 5.62% |
| V4 10-min conditional extension | 146 | 53.42% | $2173.20 | $3173.20 | $177.00 | 5.71% |

Net profit difference **+$231.00**,
win-rate change **+1.37 percentage points**,
but worst modeled end-of-day dollar drawdown **increased by $12.00**.
Both versions scored the same **146** trades, with **0 missing terminal outcomes**.

V4 starts with V1's 30% trailing and independent 35% loss stop, checks
at the 10-minute mark, and extends only when actual observed option
midpoint-to-bid profit (net entry/exit costs) exceeds 10%, last three
completed SPY minute closes have at least 3bp directional momentum,
the latest SPY close is beyond EMA8 in the entry direction, observed
relative volume is at least 0.90, option spread <=3.5%, and current
premium has retained at least 90% of its observed peak. While extended,
the trailing stop tightens to 20%, a failed hold gate exits, and a
conservative 15-minute pre-expiration cutoff applies. The rules were
written **before running this evaluation**, but this older archive
has already been inspected repeatedly by other strategies; there is
therefore **no untouched validation sample**.

Historical frictions modeled: $0.65 per contract/side, $0.01 adverse
slippage per option share per side, one open contract/position,
T+1 settlement with no same-day sale proceeds recycling. Drawdown
is **end-of-day simulated cash equity peak-to-trough**; intraday
loss and exchange-executable fills are **unknown**.

## Promotion gate outcome: REJECTED FOR LIVE

The agreed research targets were win rate >= original, net profit >
original, and *max drawdown lower than original's $165*. V4
**failed the drawdown gate**: $177 versus $165, despite beating modeled
profit and win rate. Separately, genuine untouched event-time option
sessions are still absent: the strict forward worker reports
`BLOCKED_NO_NEW_EVENT_SESSIONS`. A credible live promotion requires
real new event data, trustworthy fill modeling, complete position
reconciliation and broker/owner readiness. No switch to live
autonomous execution was made.

Full original machine-readable execution results:
[conditional_hold_v4_128_sessions.json](conditional_hold_v4_128_sessions.json).
