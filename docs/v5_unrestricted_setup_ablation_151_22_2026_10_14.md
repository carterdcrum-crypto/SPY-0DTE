# V5 unrestricted research: independent entry setup ablation ($151.22)

**Executed on Railway research service** using archived real SPY sampled minute closes, as-of volume ratio and option minute CBBO, 128 previously inspected sessions **2026-04-01 through 2026-10-06**. This is a **close-only exploratory historical proxy**, not true V5 OHLCV, independent out-of-sample, or live execution evidence.

- Railway deployment ID: `9f6fa5d4-db51-4855-8a00-e1b75ce7fb99`.
- Code at commit `6163992e65aedd2440e0523dbccf5b5e9a491a45`.
- Command: `python -m engine.v5_archive_proxy --data-dir /data/research --output-dir /data/research/v5_15122_three_setups_2026_10_14 --starting-cash 151.22 --arm v5_close_proxy_unrestricted_RESEARCH_ONLY --split-setups`.
- Persisted full `report.json`, `trades.csv`, and `daily_equity.csv` on Railway research volume at `/data/research/v5_15122_three_setups_2026_10_14/`. Runtime log `RAILWAY_V5_CLOSE_PROXY_RESULT` contains full report.

## Independently replayed outcomes

**Each arm starts over with its own $151.22.** The combined arm accepts all three setups; each single-setup arm removes the other two from entry eligibility *before* the competition/occupancy and position-sizing steps. The same all-settled-cash integer sizing, cash settlement delays, contract filters, simulated fills/exits and fees apply.

| Policy | End equity | Net profit | Trades | Win % | Profit factor | Max end-of-day drawdown | First >=5x ($756.10) | First >=$5,000 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| All three combined | **$71,706.82** | +$71,555.60 | 89 | 56.18% | 2.084 | **92.34%** | Session 8 | Session 35 |
| Momentum ignition only | $4,966.92 | +$4,815.70 | 51 | 56.86% | 2.246 | 81.34% | Session 42 | Never |
| Liquidity reversal only | $1,050.72 | +$899.50 | 38 | **60.53%** | 1.332 | **58.91%** | Session 70 | Never |
| Volatility expansion only | $698.02 | +$546.80 | 68 | 50.00% | 1.214 | 81.67% | Session 35 | Never |

The **combined run exactly reproduced** the earlier $151.22 V5 Unrestricted replay at $71,706.82. Note that the combined number is **not** the sum of the three standalone arms: running the strategy separately changes fill eligibility, capital paths, day-to-day compounding, trade priority and position availability. More signals may diversify opportunities in a backtest, but no statistically valid causal claim or live profitability prediction follows from this in-sample replay.

### End-of-day drawdown dates
- Combined: peak day 2026-07-10 → trough 2026-09-08, dollar drawdown $21,997.20.
- Momentum only: peak 2026-06-26 → trough 2026-09-01, dollar drawdown $1,568.10.
- Liquidity reversal only: peak 2026-08-25 → trough 2026-09-29, dollar drawdown $1,187.30.
- Volatility expansion only: peak 2026-06-17 → trough 2026-08-20, dollar drawdown $667.40.

### Execution and validity limits

1. Real SPY minute OHLCV **high/low/raw volume** and option as-of **event NBBO size** were unavailable. Signals here are identified by `*_CLOSE_PROXY` names. Results **are not the complete QuantConnect V5 backtest**.
2. The archive's sampled option bid/ask is **not guaranteed executable at modelled prices**. The replay assumes entry ASK + $0.01/share slippage plus $0.65/contract and exit sampled BID - $0.01/share plus $0.65/contract, with one-session delay before sold option proceeds can be re-used.
3. Every date was previously investigated; this is in-sample, not a blind holdout. Historical extrema are *end-of-day cash-account equity only*, understating potential intraday declines. Stop triggers do not guarantee fill prices or prevent total premium loss.
4. No broker orders were placed, no OPRA purchased, and no live/paper trading strategy configuration changed during this research. The normal forward watcher was restored after replay.
5. This is a research **setup-isolation comparison**, not a legitimate forecast of what $151.22 will earn.

**Working hypothesis only:** momentum ignition contributed the strongest standalone ending equity and profit factor; liquidity reversal yielded the highest standalone win rate and least observed drawdown; the combined historical policy ended far higher than any isolated policy, with the worst drawdown. Independent walk-forward and real quote-depth replay must precede claims that all three together are superior.
