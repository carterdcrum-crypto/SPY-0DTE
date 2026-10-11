# SPY 0DTE — 20% trailing stop, 50% versus 100% target (2026-10-11)

## Actual executed research

The isolated Railway research worker analyzed **128 archived sessions**
(49,792 SPY one-minute canonical frames, 2026-04-01 to 2026-10-06)
from already-persisted Databento EQUS.MINI and OPRA.PILLAR **cbbo-1m** samples.
**No new market data was purchased or billed**, and this was not a live
or paper-broker trade. Source research deployment:
`4d55b549-aadb-4a2d-b3af-910a69c4513c`.

This is an **exit sensitivity study, NOT a full, genuine executable
SPY 0DTE profitability backtest**. It tests the previously preregistered
momentum / volume-proxy / completed-price-action / reversal / MACD
**four-factor ablation**, with the PCR-dependent fifth factor NOT TESTED.
It does not reproduce AI model exits or live broker slippage. No prior
study period is an untouched holdout.

For every fixed candidate, use the first qualifying NEXT-minute
sampled 0DTE SPY CALL/PUT quote, with modeled delta 0.4–0.6, and
spread/mid <=15%. Simulated buy at archived ask + $0.01/share adverse
slippage, sell at sampled bid - $0.01/share adverse slippage, each side
$0.65/contract fee. Maximum hold ten minutes and common 35% hard stop.
A 20% trail uses best **net sampled bid** starting at entry. For 100%
targets, a four-contract experiment sells three and leaves one runner
until trailing, hard or time exit. All arms use identical entry samples;
the historical signals share the prior ten-minute nonoverlap rule.
Capital sizing, intratrade account equity, settled cash, actual broker
fills, and executable stops **are not simulated**.

The four-factor rule identified 562 eligible entries; 537 had matching
option entry and exit sampled quotes. Another 25 lacked a qualifying
sampled entry. The broad three-family baseline accepted 2,289 entries,
of which 2,170 had matched quotes (119 lacked entry quote).

## Comparison (537 matched four-factor sampled option scenarios)

Every outcome below assumes **one contract per identical hypothetical
signal**, summed without any capital or cash-account constraint.

| Exit rule | Win rate | Mean option premium return | Sum of scenario net P&L | Cumulative closed-trade P&L drawdown (NOT account) |
| --- | ---: | ---: | ---: | ---: |
| 50% target, no trail | 34.45% | -5.30% | -$3,060.10 | $3,538.20 |
| 50% target, 20% trail | 28.31% | -5.72% | -$3,271.10 | $3,766.00 |
| 100% target, no trail | 33.71% | -5.77% | -$3,270.10 | $3,566.20 |
| **100% target, 20% trail** | **28.31%** | **-6.09%** | **-$3,492.10** | **$3,866.20** |

Under a fixed **four-contract** assumption (not verified affordable):
the old rule summed -$12,240.40 and the new 100%/20%-trail staged
rule -$14,144.40. Both are unfinanced scenario totals, NOT account
return, bankroll results, max account drawdown or live P&L.

Full new bundle versus old in the one-contract study:
**-$432.00** difference over 128 days (-$3.375/day).
A descriptive seeded 5-session day-block 95% paired bootstrap range
is **[-$10.766, +$3.836] per day**, crossing zero.
At an unchanged +100% target, adding 20% trailing alone changed the
one-contract summed scenario P&L by **-$222.00**.

By prior research period, the old→new one-contract summed scenario
P&L was:
- Development, 352 pairs: -$1,866.60 → -$2,415.60.
- Validation, 90 pairs: -$558.00 → -$588.00.
- Previously studied diagnostic, 95 pairs: -$635.50 → -$488.50.

All configurations were negative in this lower-quality price sample.
The 20% trail and +100% target **did not reliably help**; the
full new bundle was worse overall. The uncertainty band includes zero,
the periods had already been inspected, and findings cannot validate
a profitable retail trading system.

## Why this does not prove live profitability or an actionable stop

Stored OPRA CBBO-1m prices are minute **buckets**, not independently
observed event-time executable NBBO. Complete option quote events,
quote sizes, precise NBBO observation times and full-chain as-of SPY
put/call cumulative option volume are missing. Delta is modeled, and
sampled quote presence does not prove a fill. No real four-factor +
PCR five-factor profitability can be claimed. Variable risk sizing,
cash T+1 settlement, live hold-versus-sell soft exits, order latency
and after-entry account drawdown were deliberately not reconstructed.

The results should not be used to arm the autonomous live switch.
A credible next phase requires properly licensed SPY event-observed
bid/ask with point-in-time timestamps and verified intraday SPY
put/call volumes, an untouched holdout, and live-like fills with
order reconciliation. Do not backfill prices or declare positive results
from underlying signed SPY basis-point proxies.

See `research/results/exit_policy_comparison_2026_10_11.json` and
`engine/exit_policy_comparison.py` for reproducibility. The raw
minute archive and detailed matched-trade CSV remain on the existing
persistent research volume; historical third-party raw quotes were
not copied into public GitHub.
