# V5 signal-variation research — 128 archived sessions (executed 2026-10-28)

**Execution:** Railway research deployment `c9557fb8-1879-4ac2-9648-b8c7cbe15f86` (SUCCESS), script `engine/v5_signal_variations.py`. Complete machine-readable data retained on the research volume at `/data/research/v5_signal_sweep_2026_10_28/report.json`.

**Controls:** Each independent hypothetical portfolio starts with $1,000 on the same 128 previously researched sessions (2026-04-01–2026-10-06). All use the fixed latest user-selected screens IV/RV <= 1.5, delta absolute 0.35–0.65, 5% maximum sampled bid/ask spread; full cash with EOD drawdown throttling, 35% premium hard stop, 25% trail, reversal exit, 18-minute max hold, cash settling following session and one position at a time. Only ONE signal's entry thresholds are changed at a time; separate arms keep only one signal or remove one signal.

Baseline signal and candidate sequences were compared **on every session** against the prior 5%-spread archival implementation and the modeled $1,808,494.60 final equity matched exactly. Neither source code nor running LIVE/PAPER trading policy was altered by these backtests.

| Variant | Condition | Model ending equity | Trades | Win rate | Peak-to-trough EOD drawdown | Max imaginary single-order size |
|---|---|---:|---:|---:|---:|---:|
| baseline_all_three | All three unchanged | $1,808,494.60 | 121 | 53.72% | 32.24% | 25,612 contracts |
| momentum_looser | Momentum: 8-close breakout, efficiency >=0.18, momentum >=2 bps | $1,824,275.10 | 123 | 53.66% | 32.24% | 25,834 contracts |
| momentum_stricter | Momentum: 16-close breakout, efficiency >=0.34, momentum >=4 bps | $909,932.50 | 124 | 53.23% | 32.24% | 7,201 contracts |
| reversal_looser | Reversal: prior 15-close range recross, efficiency <0.75 | $2,055,376.50 | 125 | 55.20% | 39.11% | 29,107 contracts |
| reversal_stricter | Reversal: prior 25-close range recross, efficiency <0.45 | $900,525.90 | 122 | 52.46% | 37.03% | 10,066 contracts |
| expansion_looser | Expansion: 6-close compression, ratio <=0.90, momentum >=1.5 bps | $737,590.10 | 135 | 52.59% | 44.66% | 10,445 contracts |
| expansion_stricter | Expansion: 10-close compression, ratio <=0.55, momentum >=3 bps | $24,459.80 | 110 | 51.82% | 33.91% | 193 contracts |
| momentum_only | Only momentum | $10,293.80 | 63 | 55.56% | 33.61% | 81 contracts |
| reversal_only | Only reversal | $1,237.40 | 21 | 52.38% | 26.30% | 10 contracts |
| expansion_only | Only expansion | $135,401.60 | 103 | 52.43% | 34.16% | 1,248 contracts |
| without_momentum | Reversal + expansion | $247,766.00 | 124 | 53.23% | 32.89% | 1,670 contracts |
| without_reversal | Momentum + expansion | $299,151.10 | 112 | 52.68% | 37.11% | 2,515 contracts |
| without_expansion | Momentum + reversal | $61,491.60 | 90 | 55.56% | 33.86% | 486 contracts |

## Observations
- Looser *liquidity reversal* has the largest **in-sample** ending equity ($2,055,376.50), with five additional positions relative to baseline, but its EOD drawdown worsens from 32.24% to 39.11%. It hypothetically fills **29,107 contracts** in one trade.
- Looser *momentum ignition* slightly improves modeled equity to $1,824,275.10, with virtually unchanged EOD drawdown (32.24%) and +2 positions, but demands up to **25,834 contracts** in one order, without verified depth.
- Looser *volatility expansion* increases historical trades to 135 but decreases modeled ending equity ($737,590.10) and increases EOD drawdown (44.66%).
- Every single-signal-only and every leave-one-out portfolio ended with less model equity than the all-three baseline. **This does not demonstrate independent synergistic returns**, because entry ordering, account cash and compounding paths all change.
- Tightening individual signals sometimes changes account path more than trade count; comparison is not causal attribution of per-signal profitability.

## Do not promote these parameters directly to live
All 128 sessions have been examined repeatedly; picking a winning variation on this in-sample archive is susceptible to **overfitting**. The source is close-only SPY minute data with sampled option bid/ask quotes: genuine OHLCV, synchronized executable NBBO, displayed contract depth, realistic market impact and independent live fill evidence are **missing**. The hypothetical multi-million-dollar equity is not a defensible expected live outcome, particularly with 25,000–29,000 contracts imagined at single quotes. End-of-day drawdown masks potentially larger intraday losses, including the total loss of any 0DTE premium.

**Next validation:** unchanged V5 versus *momentum_looser* and *reversal_looser* in parallel isolated forward/paper trials using executable timestamped option bid/ask with real sizes; report order rejections, fill slippage, risk and statistically meaningful out-of-sample performance. No live parameter change based on this study.
