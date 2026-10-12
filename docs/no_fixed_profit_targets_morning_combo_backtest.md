# SPY 0DTE: 09:30–11:00 ET, NO fixed take-profit — 12-entry-combination study
Research run October 11–12, 2026; executed on the existing Railway archive. No new market data purchases.

## Exit policy actually used

No fixed profit target whatsoever in **this backtest**:
- **30% whole-position trailing stop** from sampled peak net bid liquidation value;
- separate independent **35% loss stop** relative to original option entry cost;
- **maximum 10-minute hold**;
- **NO take-profit price**, **NO partial profit taking**, **NO runner**;
- one modeled contract per candidate, option entry modeled at ask + $0.01/share adverse slippage + $0.65 brokerage fee; exit bid - $0.01/share adverse slippage - $0.65 brokerage fee;
- an additional **$2 per round trip** adverse-friction stress reported independently.

The previous 09:30–11:00 tests ALSO already used these no-target exits. This is a rerun with new entry combinations, **not** evidence that disabling targets alone increased performance.

### Earlier benchmark (same 30% no-target trailing exit)

| Entry | Hypothetical cases | Win rate | One-contract summed modeled net P&L |
|---|---:|---:|---:|
| Original 10:00–12:00 four-factor + RVOL≥1.5 | 81 | 41.98% | **+$290.70** |
| Shifted 09:30–11:00 four-factor + RVOL≥1.5 | 14 | 57.14% | **-$6.20** |
| Opening 15-CLOSE breakout 09:30–11:00 | 163 | 39.26% | **-$558.90** |

Existing MACD/RSI four-factor requires **75 completed SPY one-minute bars**; its new early window actually admitted original signals only around **10:46–11:00**. A distinct opening 15-CLOSE breakout was therefore used to explore earlier entries from roughly **09:46**. This is not a true opening HIGH/LOW range since OHLC highs and lows are missing.

## Twelve new opening-window combinations: results

128 already-inspected trading sessions with 49,792 archived minute snapshots. All 12 candidates use only previously completed SPY close/relative-volume data and modeled sampled option quotes at entry.

| Rule / combination | Matched trades | Win rate | Simulated one-contract P&L | Additional $2/trade stress |
|---|---:|---:|---:|---:|
| Opening breakout baseline | 163 | 39.26% | **-$558.90** | -$884.90 |
| Relative volume ≥2.0 | 75 | 38.67% | **-$353.50** | -$503.50 |
| **15-minute directional efficiency ≥0.55** | **31** | **35.48%** | **+$57.70** | **-$4.30** |
| Volume accelerating vs previous five completed bars | 153 | 39.22% | -$468.90 | -$774.90 |
| ≥5bps directional move over last five completed closes | 138 | 39.86% | -$358.40 | -$634.40 |
| Volume ≥2.0 AND 15-minute directional efficiency ≥0.55 | 16 | 37.50% | -$87.80 | -$119.80 |
| Option bid/ask spread ≤8% of mid | 163 | 39.26% | -$558.90 | -$884.90 |
| Volume ≥2.0 AND option spread ≤8% | 75 | 38.67% | -$353.50 | -$503.50 |
| Directional efficiency ≥0.55 AND spread ≤8% | 31 | 35.48% | +$57.70 | -$4.30 |
| Signal within 09:46–10:30 only | 93 | 36.56% | -$665.90 | -$851.90 |
| **Opening trend → 5-close pullback → reclaim** | **49** | **36.73%** | **-$750.70** | **-$848.70** |
| Opening pullback reclaim AND efficiency ≥0.55 | 3 | 66.67% | +$11.10 | +$5.10 |

### An important discovery about spreads

The extra <=8% spread condition **did not remove any of the already selected opening option quotes** from their matched sets, so it produced literally identical results. That condition adds **no discriminatory power to this archived sample**; it is not a source of option-trading edge here.

### Most promising-looking subset: directional efficiency ≥0.55

Only 31 trades, 11 winners, and **+$57.70** modeled one-contract P&L, *before* the additional $2/contract stress.
- Development: **-$75.60**.
- Historical validation: **+$4.80**.
- Previously inspected diagnostic: **+$128.50**.
- Additional $2 round-trip adverse execution stress: **-$4.30**.
- **Single best day: +$193.70**, larger than the entire +$57.70 profit.
- **Removing that one day: -$136.00**.

Therefore the "best" apparent combination is **not a demonstrated positive edge**. The 3-case pullback + efficiency 66.67% win rate is far too sparse to imply a 67% strategy. Both are hypothesis generators only.

### Genuinely new ideas for further research (NOT validated by this backtest)

1. **Real opening-range break, retest, re-acceleration**: first form a true 5/15-minute opening high/low using OHLCV; require a completed-close break, a controlled retest, and renewed volume before entering. Research using properly sourced minute OHLCV and event-time option quotes, not a close-only proxy.
2. **Gap-and-go *versus* gap-fade market-regime classifier**: premarket gap magnitude, first 5/15 minutes balance, opening volume, prior-day range/overnight trend, broad-market correlation. Select continuation, fade, or no-trade before an option order; do not infer these missing quantities from the current snapshots.
3. **Failed-breakout reversal only after confirmation**: opening close breakout fails, completed SPY close returns within reference range with adverse delta/volume context, retest and rejection; then opposite-side option. Predeclare strict *as-of* rules. Earlier naive immediate reversal lost, so do not reintroduce it unconditionally.
4. **Gamma/IV cost-aware pullback**: select debit at or near the money based on independently timestamped executable spread, real delta, IV vs expected same-day realized movement, event risk, and realistic bid liquidation. If no sufficient post-fee expectancy, skip the trade.

### Limitations

The actual archived SPY data contain **1-minute CLOSE and relative-volume series** rather than trustworthy completed OHLCV VWAP/order-flow data, and option CBBO-1m **minute buckets** rather than independently time-stamped executable event NBBO, quoted sizes or broker fills. Full-chain as-of SPY put-call ratio is unavailable. There is no unseen holdout: multiple experiments have reused the same 128 sessions. Research figures assume one hypothetical contract per accepted signal and **are NOT real or paper account returns**, and do not model actual cash-settlement or all capital restrictions. Scanning twelve rules on these previously viewed dates adds multiple-comparison selection bias.

**Decision:** Do not promote any of the 12 to autonomous live trading based on this archive. Keep 30% trailing with no take-profit as a frozen research exit; collect prospective true fill-quality and opening OHLCV observations before claiming an edge.

## Files

- `engine/no_tp_morning_entry_combos.py`: complete reproducible 12-rule study and no-target options simulation.
- `tests/test_no_tp_morning_entry_combos.py`: entry causality tests and fixed no-target exit settings.
- `research/results/no_tp_opening_entry_combos_2026_10_11.json`: actual archived-backtest results and five-session bootstrap robustness.
- Prior old/new window comparison: `docs/morning_window_0930_1100_backtest_2026_10_11.md`.
- No live strategy, production app, or existing paper order/account records changed.
