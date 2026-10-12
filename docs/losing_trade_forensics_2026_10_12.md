# SPY 0DTE — Forensic Diagnosis of Losing Options (12 October 2026)

## Executive finding

We stopped adding entry rules and instead **reconstructed every modeled trade** from the last Net Edge experiment, joining its first sampled option-ask entry and sampled option-bid liquidation to underlying SPY at the same archived minute and calculating option **mid-to-mid movement vs realistic modeled ask-to-bid execution**.

**All 1,506 historical simulated positions from the four active research arms were reconstructed exactly.** The 128-day archive is not a new holdout. Outcomes overlap across competing arms; do NOT add numbers across arms.

**The main loss driver is wrong SPY direction, with option execution drag compounding the losses.** For the analog + cost-gated arm, trades actually rose **+$226 at theoretical midpoint marking**, but simulated ask-to-bid liquidation plus model fees and slippage incurred **$467.60 in execution drag** — a net loss **-$241.60**. Midpoint marks are NOT executable fills and must not be marketed as prospective profits.

## The four archived research arms — same frozen exit

| Research selection policy | Matched historical option trades | Won | Hypothetical net P&L | Hypothetical mid-to-mid price change | Modeled spread, fees and adverse-slippage drag |
|---|---:|---:|---:|---:|---:|
| All independent opening + legacy signals | 701 | 242 | **-$4,270.30** | -$1,085.50 | **$3,184.80** |
| Cost filter only | 586 | 203 | **-$3,701.80** | -$1,050.50 | $2,651.30 |
| Historical analog option net EV positive | 117 | 54 | -$381.10 | **+$152.50** | $533.60 |
| **Analog EV positive AND cost hurdle** | **102** | **48** | **-$241.60** | **+$226.00** | **$467.60** |

**Extra $2 of adverse execution cost per roundtrip is a separate stress**: for the 102-trade analog+cost arm, stress-adjusted modeled P&L falls to **-$445.60**. This total includes the old 45-session initial warmup/descriptive history and therefore differs from the 33-trade post-warmup policy evaluation (-$207.90). The 102 historical trades CANNOT be interpreted as an 83-day forward holdout.

## Among the 102 analog + cost-gated scenarios

**54 lost or broke even**, including:

| Descriptive POST-EXIT classification | Losing trades |
|---|---:|
| SPY moved >1bp **opposite** the option's direction | **43** |
| SPY moved correctly (>1bp) but option still lost | **4** |
| Option midpoint rose, but modeled executable net P&L was <=$0 | **2** |
| SPY was roughly flat (within ±1bp), option lost | **5** |

These four categories are mutually exclusive, retrospective labels. They are **not causal attributions** or lookahead-safe entry features. The correct/flat classification depends on sampled SPY spots, which do not prove what happened intraminute. A flat or correct SPY movement can still lose in 0DTE because of IV, theta, gamma, timing and spread.

**The average winner was +$31.82 while the average loser/flat was -$32.76** on this historical gated arm. At that payoff ratio, a roughly **50.7% win rate** is needed to break even *before* adding further friction. Observed win rate was only **47.06%**. Neither win rate nor payoff distribution is proven stable.

For the unfiltered 701-trade alternative:
- **459** lost or broke even.
- **342 of those** were aligned with the **wrong sampled SPY direction**.
- 26 had positive midpoint change but still negative ask/bid net P&L.
- **210 losing scenarios** showed positive sampled bid-liquidation value at some point before their modeled exit, then finished negative. This does NOT prove the 30% trailing threshold was wrong; minute samples, gap-throughs and full max-hold stops preclude that conclusion without fresh data.
- Across all 701 trades, modeled execution drag averaged **$4.54 per contract round trip**, or **$3,184.80** in aggregate. The costs are mechanically derived from sampled bid/ask half-spreads plus modeled $0.65/side commission and $0.01/share/side adverse slippage, with a zero-credit floor where applicable.

### Timing — important but NOT a trading rule yet

| Time band (Eastern) | Analog + cost arm trades | Historic modeled net P&L |
|---|---:|---:|
| **09:30–10:29** | 35 | **-$445.50** |
| **10:30–10:59** | 11 | **+$148.70** |
| 11:00–12:59 | 29 | -$38.70 |
| 13:00–15:24 | 27 | +$93.90 |

These breakdowns are **exploratory, on the same inspected data**. It would be **data mining** to convert the 10:30–10:59 11-trade winner slice into a deployed trading window. Later it might be a hypothesis for **prospective** validation, but not an established edge. We do not change the user-requested 09:30–11:00 morning rule based on this retrospective table.

Historical period splits for the 102-trade policy were development **96 / -$112.80**, validation **6 / -$128.80**, and previously studied diagnostic **0** (abstained).

## Recommendation based on this evidence

1. **STOP changing take-profit/trailing parameters** in an effort to fix a directional problem. Keep **30% whole-position trailing**, independent 35% premium stop, max 10-minute hold and NO fixed profit target as the frozen research exit.
2. **Prioritize as-of directional reliability and trade timing.** An early-breakout regime classifier ought to distinguish genuine continuing drive from open/overnight retrace, failed breakouts and balanced range, using independent true completed OHLCV and timestamps. Current close-only proxy **cannot validate a real ORB**.
3. **Make the option-profit predictor account for EXECUTION costs** as a first-class gate, and compare observed executable option markouts versus signed underlying motion at entry. Midpoint improvements are insufficient without sellable bid / buyable ask.
4. **Record all prospective signals, including rejected/no-trade**, once genuine event-observed option NBBO/size and SPY opening OHLCV are available. Use frozen rules and evaluate **new** market days, rather than carving more profitable bins out of these 128 reused dates.
5. **Integrate actual cash-affordability modeling at entry**, with the updated illustrative **$1,000** starting settled cash and T+1 settlement; maintain no live promotion without demonstrated positive after-cost edge.

No order placement, no external quote purchase, no new account deposit, and no change to Android/live Auto Trade. Historical data are archived **CBBO-1m**, NOT independently verified executable NBBO event-time quotes. This is a **post-trade forensic diagnostic**, not another profitable strategy or an unseen validation study.

## Reproduce

```bash
python -m pytest -q tests/test_trade_forensics.py
python -m engine.trade_forensics \
  --data-dir /data/research \
  --ledger /data/research/option_net_edge/trades.csv \
  --output-dir /data/research/trade_forensics
```

Files:
- `engine/trade_forensics.py` forensic engine;
- `tests/test_trade_forensics.py` guardrail tests;
- `research/results/trade_forensics_2026_10_12.json` executed summary report;
- `/data/research/trade_forensics/trade_forensics.csv` complete 1,506-row working-volume ledger;
- `/data/research/trade_forensics/report.json` full worker results.

**No production merge: research branch `research/losing-trade-forensics-20261012`.**
