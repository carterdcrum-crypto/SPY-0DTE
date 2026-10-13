# SPY-0DTE V4 — Dynamic position sizing historical backtest

**Executed October 12, 2026 (US Central);** Railway research deployment
`dda9e342-aa1d-46f5-a547-f8dbb631a6df`. Previously inspected **128 sessions, April 1–October 6, 2026**,
starting settled cash **$1,000**. Same original frozen Regime Reclaim V4
entry and conditional hold exit. Only the number of option contracts varies.
One position at a time, **T+1 cash settlement**, no reuse of same-day sale
proceeds, no borrowing. Each option contract pays $.65/side fee + $.01/share
adverse slippage, with a separate +$2/contract friction-stress column in raw
JSON. All positions use **long options**, so the entire paid premium can be
lost despite the modeled 35% stop.

## Final simulated results

| Sizing policy | Trades | Contracts | Win rate | Net profit | Ending balance | Max EOD drawdown | Peak-relative EOD DD |
|---|---:|---:|---:|---:|---:|---:|---:|
| v4_fixed_one_contract | 146 | 146 | 53.42% | $2173.20 | $3173.20 | $177.00 | 5.71% |
| v4_dynamic_streak_compound | 146 | 167 | 53.42% | $2205.90 | $3205.90 | $207.70 | 6.23% |
| v4_dynamic_streak_vault | 146 | 167 | 53.42% | $2205.90 | $3205.90 | $207.70 | 6.23% |
| v4_dynamic_compound | 21 | 27 | 38.1% | $108.90 | $1108.90 | $180.60 | 14.01% |
| v4_dynamic_compound_micro | 22 | 27 | 36.36% | $88.90 | $1088.90 | $193.30 | 15.08% |
| v4_dynamic_profit_vault | 21 | 27 | 38.1% | $108.90 | $1108.90 | $180.60 | 14.01% |
| v4_dynamic_fractional_kelly | 0 | 0 | N/A | $0.00 | $1000.00 | $0.00 | 0% |
| v4_dynamic_kelly_micro | 27 | 27 | 44.44% | $324.90 | $1324.90 | $72.00 | 7.2% |

**Only modest upside from streak-compounding:** 146 wins/losses unchanged,
21 extra contracts traded. It added **$32.70** of modeled profit compared to
fixed-one-contract V4 (**$2,205.90 vs $2,173.20**), while maximum EOD
drawdown rose by **$30.70** (**$207.70 vs $177.00**). Accuracy stayed **53.42%**.

**Conventional fixed-fraction budget failed at micro-account scale:**
the 25% capital budget, integer contract floor, and drawdown throttle
excluded **156 potential entries**. The resulting strict compounding
account made only **21 trades**, won **38.10%**, and earned just
**$108.90**. It is not a useful improvement over V4.

**Kelly/ES99:** The strictly data-driven Kelly arm deliberately makes
**zero trades** until at least 25 earlier-session returns exist. Since
it made zero trades, the posterior never warms up; returning a 0% win rate
would be misleading. A separate one-contract learning version made **27
trades**, won **44.44%**, earned **$324.90**, and then vetoed 149 entries
on its insufficient measured-edge criterion.

**Profit vault:** Both the regular and streak-vault arms produced the
same historical outcome as their unreserved counterparts. Reserving 50%
of account gains after +10% did not bind additional sizing decisions here
and therefore has not been shown to reduce drawdown.

## Decision

**Do not switch real-money trading to dynamic sizing.** Even the closest
compounding arm raised drawdown for only $32.70 incremental model profit,
and no version improved win rate versus the fixed-one-contract 53.42%
reference while preserving similar trading frequency. These are
**retrospective comparisons on a heavily reused research dataset**.
The micro-account and streak variants were added after inspecting initial
results; this is explicitly *not* independent strategy validation.

The true forward test of V4 on independent event-time SPY OHLCV and OPRA
quotes is still blocked without new qualifying data. The live Webull
strategy, owner toggle, and broker execution parameters are unchanged.
No market data was purchased and no real orders were placed.

Limitations: legacy SPY close/volume ratios and 1-minute CBBO bucket
quotes are not event NBBO or actual fills. Fees, size-dependent slippage,
quote depth, intraminute stops, and execution risk can be materially
worse as quantities grow. Maximum drawdown here is **modeled account
end-of-day peak-to-trough**, not intraday portfolio maximum or guaranteed
worst-case loss.

Raw results: [v4_dynamic_size_full_results.json](v4_dynamic_size_full_results.json).
