# V4.1 risk-guard experiment — October 12, 2026

## Result: risk guard did not improve maximum drawdown

All figures are hypothetical **historical CBBO-1m / SPY close-only** model
results from the **same previously inspected 128 sessions** between
2026-04-01 and 2026-10-06. These results are *not independently confirmed
forward data or executable broker fills*. One-contract cash ledger, T+1
settlement, $1,000 starting modeled funds, $0.65 per-side fee and $0.01
per-option-share modeled adverse slippage.

| Arm | Modeled profit | Win rate | Scored trades | Max end-of-day drawdown |
|---|---:|---:|---:|---:|
| Original V1 10-minute | $1942.20 | 52.05% | 146 | $165.00 (5.62% preceding peak) |
| V4 conditional extension | $2173.20 | 53.42% | 146 | $177.00 (5.71% preceding peak) |
| **V4.1 prior-day equity high-water guard** | $2042.90 | 53.74% | 147 | $177.00 (5.92% preceding peak) |

Guard: the optional 10-minute extension is disabled whenever the **previous
session's end-of-day cash equity** is below the highest EOD account equity
observed before then. Stops, entries, positions, costs and default 10-minute
exit are otherwise unchanged. This has no future information in its decisions.

**Finding:** V4.1 slightly increased the measured win rate, but its
$177.00 worst end-of-day drawdown
**remains greater than V1's $165.00**
and also does not improve over V4's dollar drawdown.
It reduced modeled profits compared with V4 and changed the number
of accepted trades due to occupied-position timing.

**Decision:** Do not promote any V4 variant to autonomous real-money trading.
We have not met our previously stated max-drawdown requirement, and the
V2/V3/V4 true event-time forward replay remains blocked on genuinely new,
independently observed OPRA NBBO + SPY OHLCV. The newly added
`engine.regime_reclaim_v4` module follows the forward worker and will
score V4 and V4.1 as *research arms only* when real qualifying event files
arrive. Existing live/broker execution strategy remains unchanged.
No new market-data subscriptions, purchases or live orders.

Raw Railway machine-readable results:
[conditional_hold_v4_1_archive.json](conditional_hold_v4_1_archive.json).
