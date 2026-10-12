# Voss public-principles-inspired SPY 0DTE — Completed Independent Backtest
Executed October 12, 2026 on the same **128 already-inspected archival sessions**.

## What is—and is not—attributable to @officialvosstrading

The user's intended channel is
https://www.youtube.com/@officialvosstrading .
The publicly available Voss Trading Education site
https://vosstradingvip.com/ teaches **market context, mapping support
and resistance, waiting for confirmation, defining invalidation/risk,
and reviewing outcomes**. The public website mentions live open-hour
analysis 09:30–10:30 ET.

**Specific timestamped YouTube videos, subtitles, exact trade formulas
and VIP proprietary rules could NOT be retrieved and were NOT verified
for this run.** The research below is OUR independently invented,
mechanical, TESTABLE implementation of broad publicly available
principles, *not a direct reproduction or endorsement of Voss's
own personal entry/exit system*. Every numerical threshold and
asset-specific option selection was defined here, not attributed
to him. No membership bought.

## Five exploratory research policies and net outcome

Start with **$1,000 hypothetical settled cash**; T+1 options
settlement with no intraday recycling of sale proceeds.
One 0DTE contract open at once. Whole-position **30% trailing
stop**, independent **35% original premium loss stop**, max
**10-minute hold**, **NO fixed profit targets or partial sells**.
Fees/slippage already in modeled option ask/bid. Extra $2 per
completed roundtrip stress calculated separately.

| Independent arm | Trades | Win rate | Trades/calendar session | Simulated net account P&L | Ending $1,000 account | Extra $2/trade stress P&L |
|---|---:|---:|---:|---:|---:|---:|
| Pre-mapped 20-close level break → retest → confirmation | 78 | 32.05% | 0.609 | -$843.40 | $156.60 | -$999.40 |
| Prior multi-touch CLOSE support/resistance rejection | 306 | 37.58% | 2.391 | -$969.80 | $30.20 | -$1,581.80 |
| Trend-context pullback → directional close confirmation | 141 | 36.88% | 1.102 | -$931.30 | $68.70 | -$1,213.30 |
| All 3 price structure entry setups, unfiltered | 212 | 33.02% | 1.656 | -$974.60 | $25.40 | -$1,398.60 |
| **All 3 + option bid/ask/IV cost gate** | **103** | **44.66%** | **0.805** | **+$453.10** | **$1,453.10** | **+$247.10** |

**Most combinations LOST and quickly depleted the modeled cash
balance**. Adding an as-of option-pricing filter (entry spread
<=3.5%, option IV / underlying realized-vol proxy <=1.5) turned the
three-setup mix positive in this already-inspected history. But its
44.66% hit rate is not particularly high. The positive result
does not establish repeatability.

### Look at the historical split before celebrating

For the 103-trade option-gated arm:

| Earlier inspected period | Matched trades | P&L |
|---|---:|---:|
| Development | 58 | **+$186.60** |
| Former "validation" (NOT untouched) | 24 | **-$132.20** |
| Previously studied diagnostic | 21 | **+$398.70** |
| Total | 103 | +$453.10 |

**Important:** The 21-trade diagnostic segment contributed
**+$398.70 of the $453.10 total**. That's about 88% of the
profit; the previously used "validation" slice was negative.
This is a significant concentration and selection-risk warning.
No genuine prospective data support the result.

The 103 historical trades occurred on **67 of 128** market days;
fewer than one per market day on average. The user's preference
for higher intraday frequency is unmet by the filtered candidate.

The modeled maximum end-of-day account-equity drawdown for the
filtered arm was **$270.30**, based on this one-contract
$1,000 cash simulation; NOT true intraminute peak-to-trough.
No claim about reliably doubling weekly.

## Entry setups implemented in separate code

All require 24 prior completed 1-minute SPY CLOSE observations
and as-of relative-volume proxy; real OHLCV high/low, true VWAP,
volume-at-price, order flow and event NBBO were unavailable.

1. **Level break/retest:** at least two sufficiently separated
   20-close touches identify a frozen level (zone ±2.5bp);
   completed break >=2bp and RVOL >=1.25; subsequent
   completed pullback retests frozen level ±3.5bp, THEN
   later close reclaims >=2bp in direction within 8 bars.
2. **Level rejection:** previous completed close within ±3.5bp
   of prior multi-touch level; next completed close rejects
   away from that level by >=2bp. Treat as support bounce
   CALL or resistance rejection PUT.
3. **Trend-confirmed pullback:** 15-close movement >=8bps,
   previous close near/through the prior 5-close mean, next
   close reclaims that mean by >=2bps with trend context.
4. **Combined** chronological one-position simulator with
   priority/reentry and T+1 cash-account affordability.
5. **Combined + as-of option gate** described above.

All signals use only previously completed minute close and
RVOL observations before submission to the next sampled option
quote screen. The option archive contains CBBO-1m bucket quotes,
not verified live executable event NBBO. The one-contract
fill model buys ask (+1¢/share slippage and $0.65 fee),
sells bid (-1¢/share slippage and $0.65 fee).
True intraminute trailing-stop slippage and real market spreads
may differ materially.

## Decision / independence

Do NOT merge these Voss-inspired hypotheses into our
separate frozen Regime Reclaim arm merely because one
inspected-period combination looks profitable.

They remain **independent research ONLY**, on separate GitHub
branch `research/voss-price-structure-20261012`, with the
unmodified previously inspected SPY archive and exactly
the same capital/exit assumptions. One genuinely forward
event-data test is needed before trusting any positive
outcome, particularly the performance that concentrates
in one previously studied subperiod.

Reproducible research:
```bash
python -m pytest -q tests/test_voss_public_structure_research.py
python -m engine.voss_public_structure_research \
 --data-dir /data/research \
 --output-dir /data/research/voss_public_structure
```

GitHub files:
- `docs/voss_public_research.md` source assessment / honest attribution;
- `engine/voss_public_structure_research.py` separate code;
- `tests/test_voss_public_structure_research.py`;
- `research/results/voss_public_structure_2026_10_12.json`.
- full trade rows in Railway research volume
  `/data/research/voss_public_structure/trades.csv`.

**No market data purchases, no paper broker orders, no live orders
or Android app changes.**
