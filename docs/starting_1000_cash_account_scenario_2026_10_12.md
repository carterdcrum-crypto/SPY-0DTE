# SPY 0DTE — $1,000 starting settled-cash scenario (October 12, 2026)

## User-requested setting
**Starting hypothetical settled cash: $1,000.00** instead of $115.00.

Updated default in `engine/settled_cash_ledger_audit.py`.
The same module can now read the latest Net Edge trade CSV's
`entry_at` column as well as the older entry-frequency
`entry_observation_at` field. With `--calendar-dir`, it includes
archive market sessions that had no trades.

**This is NOT a real deposit, current account balance, live trading
change, portfolio allocation instruction, or a profitable strategy.**
The prior research archives and old $115-specific results remain
unchanged for reproducibility.

## Executed T+1 cash overlay (128 previously inspected sessions)

All research arms start **independently** at an illustrative
$1,000.00; do NOT sum the ending balances across arms. Each
historical candidate is one option contract. There is no leverage;
once an option is bought, the cash debit is removed from settled
buying power. Its modeled sale proceeds cannot be reused until
the next trading session.

| Strategy from Net Edge archive | Trades affordable | Trades blocked by settled-cash budget | Ending modeled cash equity | Modeled P&L |
|---|---:|---:|---:|---:|
| Every setup, one at a time | 244 | 457 | **$32.80** | **-$967.20** |
| Option friction / cost filter only | 302 | 284 | **$32.40** | **-$967.60** |
| Prior analog positive net payoff | 116 | 1 | **$591.20** | **-$408.80** |
| Prior analog positive payoff + friction confirmation | 101 | 1 | **$742.70** | **-$257.30** |
| Strict uncertainty-aware positive payoff | 0 (no signals in study) | — | **$1,000.00 unchanged** | **$0.00** |

The strict arm makes no trades; `$1,000` ending is just
the no-trade control, not a profitability result. It is not
included in the generated CSV-based audit because it has no
candidate trade rows.

This is a **post-hoc filter on an already-selected historical
trade path, not a full capital-aware rerun**. When a historical
candidate is unaffordable, the model does not recompute which
other overlapping candidates would have been available.
Reusing preselected candidate dates makes these numbers
*illustrative diagnostics* only. In particular, the results are
not genuine account backtest outcomes or broker fills.

The scenario does NOT establish a repeatably profitable
strategy, and generating more signals can still rapidly deplete
the balance. Not a demonstrated plan for doubling every week.

## Frozen strategy assumptions

- Historical option CBBO quotes are minute buckets (not independently
  verified event-time executable NBBO).
- 30% high-water trailing stop, separate 35% hard premium stop,
  and 10-minute max hold, whole position.
- **No fixed take-profit, partial exits, or runners.**
- Existing one-contract per entry historical modeled fees and
  slippage preserved; separate additional $2/trade stress
  exists but is NOT recalculated into cash affordability.
- Assumed T+1 cash settlement. No same-day proceeds recycling,
  no margin, no deposits, and no live orders.
- The archived 128 trading sessions were repeatedly studied, so
  there is NO pristine out-of-sample test.

## Reproduce in the Railway research volume

```bash
python -m engine.settled_cash_ledger_audit \
  --ledger /data/research/option_net_edge/trades.csv \
  --output /data/research/option_net_edge/cash_account_1000.json \
  --starting-cash 1000 \
  --calendar-dir /data/research
```

Output committed at
`research/results/option_net_edge_cash_1000_2026_10_12.json`.
Unit tests cover the new default, T+1 settlement, price
affordability, the new Net Edge CSV schema, and zero-trade days.

**No production, connected broker, or Android auto-trade
account balance was modified.**
