# Archived SPY V5 account drawdown: minute-sampled bid liquidation equity (2026-10-28)

## Final result: **58.1371% sampled account maximum peak-to-trough drawdown**

This is a completed replay on the existing **128 sampled-option-quote sessions, 2026-04-01 to 2026-10-06**, using the executable research script `engine/v5_intraday_drawdown.py`, stored JSON report `/data/research/v5_intraday_drawdown_2026_10_28/report.json` (Railway's research volume), deployment `a7ef7930-f538-4352-8f5f-3ad44bca8fc3` with completion `V5_INTRADAY_REPORT_SAVED`.

It is **not** the unknowable true intraday account drawdown. It is the worst *observed once-per-minute* mark-to-bid-minus-$0.01/share-and-$0.65/contract account-liquidation-equity decline. Existing research exits remain as in the previous backtest. Missing quoted minutes with positions are skipped, never imputed. Option size and executable impact are **unknown**, and backtest compounding reaches unrealistic capacities.

| Measure | Existing all-three V5 signals | Looser liquidity reversal |
|---|---:|---:|
| Starting hypothetical cash | $1,000 | $1,000 |
| Modeled final equity (exact frozen match) | $1,808,494.60 | $2,055,376.50 |
| Scored entries | 121 | 125 |
| **Worst overall peak-to-trough from minute-sampled equity** | **58.1371%** | **58.1371%** |
| Peak value at worst decline | $37,753.70 | $37,753.70 |
| Peak timestamp, UTC | 2026-05-21 17:23 | 2026-05-21 17:23 |
| Trough value at worst decline | $15,804.80 | $15,804.80 |
| Trough timestamp, UTC | 2026-06-01 17:41 | 2026-06-01 17:41 |
| **Largest within-single-session observed peak-to-trough** | **41.1252%** | **41.1252%** |
| Largest within-session episode | 2026-05-21 17:23–19:22 UTC | same |
| Max day-close-to-day-close drawdown (previous measure) | 32.24% | 39.11% |
| Total observed account liquidation marks | 49,917 | 49,917 |
| Minutes with open option position sampled | 1,039 | 1,089 |
| Missing open-position quote marks | 3 | 3 |
| Largest consecutive missing observed minutes | 1 | 1 |
| Historical max sampled position premium decline from entry debit | 24.6838% | 24.6838% |

The **worst overall episode happened before the entry differences produced by the looser reversal variation**, which is why both runs share the same peak and trough values. The looser version *increased* the historical EOD-only worst drawdown from 32.24% to 39.11%, even though its sampled all-hours worst remained 58.1371%.

### Valuation and reproduction notes

- `equity_at_minute = unspent settled cash + pending proceeds of completed sales + open contract quantity × max(0, observed bid×100 − $1 slippage − $0.65 fee)`.
- An entry debits actual modeled ask plus 1-cent/share slippage and a $0.65 fee; exits credit the same `proxy_exit` simulated historical cash sale. Proceeds remain unsettled for same-day buying but count toward equity. Overnight pending proceeds settle next session.
- Position mark at time T is used **only when that option's bid was recorded at that sampled timestamp**. No interpolation, synthetic quote, or stale price carry.
- Underlying data are SPY **close-only signal proxies**, not true historical intrabar OHLCV. Market impact, real order book depth, intraminute extrema and observed live fills are missing.
- Tested source is a research-only branch, no live, paper or Dad's/Grandpa's trading changes. The earlier 5% spread and 0.35–0.65 delta research are held fixed.
- Daily and aggregate ending modeled equity were asserted identical to the previous 13-arm signal-sweep results; the 128-session length and minute-sampled EOD drawdown dominance were asserted before publishing.
- Because there are quote gaps and no sub-minute order book records, the true peak-to-trough risk, realized fills and portfolio trajectory are **not established** by 58.1371% and could be materially worse.

**Decision context:** The looser liquidity reversal boosted the repeatedly in-sample modeled ending equity but showed a 58.14% once-per-minute observed account drawdown and 39.11% EOD-only drawdown. Neither result is executable-fill evidence; do not adopt it into live automatic orders without a forward test and explicit risk acceptance.
