# SPY 0DTE research: freeze 30% trail, diagnose entries (2026-10-11)

**Experimental research, no production changes, no data purchases.**

Both studies ran successfully on our existing 128 SPY sessions,
49,792 minute observations. SPY option bids/asks were historical
**minute-bucket CBBO** snapshots, not event-observed time-matched NBBO.
Trades assume one contract, adverse $0.01/share option slippage each side,
$0.65 per-contract fee on each side, independent 35% entry stop, and a
10-minute hard time exit. There are **no profit targets, no scale-outs**.
The 30% trailing stop is calculated from the high-water **net exit value**;
all remaining contracts exit together.

## Frozen 30% trailing-only comparison

- 562 accepted four-factor proxy candidates, 537 option quote scenarios.
- **30% trailing:** 33.89% wins; -6.027% average option premium return;
  **-$3,312.10 summed hypothetical P&L**; $3,609.20 closed-trade
  cumulative drawdown (**NOT** account drawdown).
- 20% trailing: 28.31% wins; -6.433% average; -$3,668.10 hypothetical
  P&L. 30% is $356 less negative across all archived paired cases.
- Paired 5-session bootstrap interval for the 30%-minus-20% difference
  was [-$2.727,+$8.727] **per calendar day**, includes zero.
- No profitable edge is demonstrated for 30% in the existing archive.

## Entry ablations: leave 30% stop frozen

Ablations apply to **as-of signal inputs before the ten-minute
nonoverlapping lock**. They may select different trades and counts.
Do not compare aggregates without also checking mean return, frequency
and the small sample sizes.

| Entry rule | Matched 1-contract cases | Average option return | Win rate | Summed hypothetical P&L |
|---|---:|---:|---:|---:|
| Four-factor baseline | 537 | -6.027% | 33.89% | -$3,312.10 |
| Breakout-only | 499 | -6.518% | 32.46% | -$3,504.70 |
| Momentum-only | 537 | -6.027% | 33.89% | -$3,312.10 |
| Stronger observed volume >=1.50 | 381 | -4.811% | 34.38% | -$1,920.30 |
| MACD and price-action alignment | 444 | -5.837% | 32.43% | -$2,856.20 |
| Weighted factor score >=6 | 336 | -6.797% | 32.44% | -$2,530.80 |
| Pullback-only | 82 | -1.682% | 41.46% | -$250.60 |
| **Reversal-only** | **0** | Not applicable | Not applicable | **Not tested** |

The pullback subset has only **two matched trades in the designated
25-session validation group** (+$7.40 summed) and must not be promoted
as proven profitable. Its lower cumulative loss partly reflects trading
much less. The high-volume filter remained negative in the development,
validation and previously inspected diagnostic groups: -$1,483.60,
-$303.90 and -$132.80, respectively.

### Discovered algorithm defect

The old 'reversal' candidate first triggers when the latest completed
close extends *away* from the 20-close rolling mean across a threshold.
The entry classifier then demands a **same completed-minute** price
move *back toward* the mean. This internally contradictory timing
requirement resulted in **zero accepted reversal trades** across all
128 archival sessions. It was not truly a combined momentum/reversal
options strategy.

A separate research-only implementation **completed testing** a causal
repair: accept the excursion as an alert, and wait for a subsequent
completed-bar turn within a fixed three-minute window. If a turn
never arrives, skip the trade. Assess optional MACD improvement,
observed volume and RSI extreme using only data known at the later
confirmation time. This fixes an entry-construction issue, not
automatically its expected profitability.

### Executed deferred-reversal repair: still not profitable

The historical archive contained **1,294 original reversal excursion
triggers**. The repaired strategy confirmed a later completed-minute
reversal in **819** cases using price-turn + RSI and in **314** cases
when also requiring improving MACD histogram and adequate observed
volume. 308 strict reversal cases had matched option price paths.

All use the SAME 30% no-target full-position trailing exit:

| Research variant | Matched trades | Win rate | Average option return | Summed hypothetical P&L |
|---|---:|---:|---:|---:|
| Original momentum-only baseline | 537 | 33.89% | -6.027% | -$3,312.10 |
| Delayed reversal price turn + RSI | 797 | 34.38% | -5.154% | -$4,339.10 |
| Delayed reversal + MACD + volume | 308 | 35.39% | -5.168% | -$1,780.40 |
| Original baseline plus confirmed reversals | 725 | 35.45% | -5.735% | -$4,315.50 |

Combining repaired reversals with the original strategy increased
summed losses. The strict reversal-only variant was negative in both
development (-$1,472.00) and validation (-$427.90); a +$119.50
positive previously inspected diagnostic group is not independent
evidence of profitability. No profit claim or live promotion.

Detailed results:
`research/results/deferred_reversal_30pct_2026_10_11.json`.

### What we should change to develop a real edge

1. **Entries, not exits first:** strengthen price/volume and execution
   cost gates while keeping 30% trailing-only fixed.
2. **Repair reversal chronology:** no trade until a separate completed
   confirmation bar verifies the turn; avoid lookahead.
3. **Net economic filter:** estimate bid/ask, modeled commission,
   adverse fill and conditional upside BEFORE buying, then abstain
   unless evidence supports positive expected value. Existing
   borrowed modeled Greeks are not proof.
4. **Test true quote timestamps and as-of SPY-wide PCR** when user-owned
   admissible provider data becomes available; do not infer full-chain
   volume from a tiny 0DTE quote subset.
5. **Prospective paper walk-forward only**, then independent untouched
   future event-grade holdout. The 128 historical days were already
   inspected, so their validation splits are descriptive only.

## Artifacts

- `research/results/trailing_only_sweep_2026_10_11.json`
- `research/results/entry_filter_30pct_ablation_2026_10_11.json`
- `engine/trailing_only_sweep.py`
- `engine/entry_filter_30pct_study.py`
- `engine/deferred_reversal_30pct_study.py` (executed causal repair)
- GitHub Actions test workflow `exit-policy-research-tests`.

**No change to live strategy, live authorization, target logic, paper
account balances or order execution** was made by these studies.
