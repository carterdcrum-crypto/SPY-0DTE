# Research result: trailing-only exit stop grid, 2026-10-11

This is the **completed experiment** on the previously licensed and saved
Databento 1-minute bucket SPY option snapshots, without any new data charges.
It is **not** a validated strategy backtest with exchange-timestamped bids.

## Design

- 128 archived SPY sessions, 49,792 one-minute frames.
- Fixed momentum/volume/price-action/reversal/MACD proxy four-factor entries,
  562 signal candidates, 537 paired hypothetical one-option-contract entry
  and 10-minute quote-path scenarios; 25 signals had no suitable entry quote.
  Full-chain point-in-time put/call ratio NOT available.
- Sweep 20%, 25%, ..., 90% trailing drawdown from running high-water net
  exit value; plus no trailing control. **No profit targets and no scaling /
  runners. Exit all held contracts at the same price event** (results
  normalized to one contract for each scenario).
- Independent -35% from entry premium stop remains in every case.
  Maximum hold 10 minutes. Cost model: $0.65/contract/side plus adverse
  one cent/option-share slippage each side.
- Exact same entry candidates and quote paths across all variants.
- Select parameter on archived development sessions only; validation and
  previously studied diagnostic groups are reported separately.
- Sampled minute *bucket* CBBO quotes do not prove executable fill time or
  quote size, and using model greeks and imperfect as-of timestamps risks
  overstating performance. These data were already inspected; no fresh holdout.

## Results (all 537 matched single-contract scenarios)

| Trailing drawdown | Simulated closed-trade P&L sum | Mean option return | Win rate | Additive trade-P&L drawdown (NOT account) |
|---:|---:|---:|---:|---:|
| 20% | -$3,668.10 | -6.433% | 28.31% | $4,018.20 |
| 25% | -$3,581.10 | -6.337% | 32.03% | $3,906.20 |
| **30%** | **-$3,312.10** | **-6.027%** | **33.89%** | **$3,609.20** |
| 35% | -$3,513.10 | -6.431% | 33.52% | $3,767.20 |
| 40% | -$3,580.10 | -6.588% | 33.33% | $3,845.20 |
| 45% | -$3,577.10 | -6.548% | 33.52% | $3,849.20 |
| 50% | -$3,565.10 | -6.506% | 33.52% | $3,837.20 |
| 55% | -$3,565.10 | -6.506% | 33.52% | $3,837.20 |
| 60% | -$3,594.10 | -6.545% | 33.52% | $3,866.20 |
| 65% | -$3,553.10 | -6.533% | 33.71% | $3,825.20 |
| 70–90% | -$3,555.10 | -6.552% | 33.52% | $3,827.20 |
| No trailing | -$3,555.10 | -6.552% | 33.52% | $3,827.20 |

70–90% is identical to no-trailing under the independent -35% stop
in this specific archive: none of those trailing rules fired.

## Development choice and diagnostics

| Scope | 20% trailing P&L sum | 30% trailing P&L sum | Difference |
|---|---:|---:|---:|
| Development (82 sessions; 352 cases) | -$2,604.60 | -$2,334.60 | +$270.00 |
| Validation (25 sessions; 90 cases) | -$588.00 | -$440.00 | +$148.00 |
| Previously examined diagnostics (21 sessions; 95 cases) | -$475.50 | -$537.50 | -$62.00 |
| **Total (128 sessions; 537 cases)** | **-$3,668.10** | **-$3,312.10** | **+$356.00** |

30% has the best development result and the least-negative result on
all inspected data. The paired five-session block bootstrap 95% interval
for 30% minus 20% in **USD per calendar day** is **[-$2.727, +$8.727]**;
it includes zero, so no convincing positive improvement is established.

## Recommendation

**Do not claim 30% is profitable.** It was merely the least-loss-making
trail width in a noisy, archive-constrained sensitivity study. All 16
trailing/no-trailing configurations remained negative, even before
account-level financing, settlement and order-fill frictions.

Treat 30% as an **unvalidated paper-only research candidate**, alongside
25% and 35%; not an automatic live-trading setting. Missing independently
timed event NBBO, actual fill-size and complete as-of put/call volume keep
the true five-factor options-profitability experiment blocked.

The original production Android app and live auto-execution were not
modified and no billable historical API request was made.

## Reproduction

```bash
python -m pytest -q tests/test_trailing_only_sweep.py
python -m engine.trailing_only_sweep --data-dir /data/research \
    --output-dir /data/research/trailing_only_sweep
```

Full detailed JSON:
`research/results/trailing_only_sweep_2026_10_11.json`
