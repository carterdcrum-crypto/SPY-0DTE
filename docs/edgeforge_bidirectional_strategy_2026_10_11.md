# EDGEFORGE — SPY 0DTE Call / Put / No-Trade Decision Engine
**Research execution: October 11–12, 2026. Historical archive only.**

### The concept

Instead of treating momentum as an instruction to buy a call or put,
use the detected momentum event as a **decision opportunity**. For
every qualified entry timestamp:

1. Resolve the completed-bar SPY momentum, price action, MACD and
   relative volume information **without consulting future option exits**.
2. Construct both independently simulated long CALL and long PUT entries
   at the first candidate entry-quote bucket. Either side can fail
   separately when the sampled quote or ten-minute future bid path
   is missing; the model is never given later outcomes when deciding.
3. Search analog patterns from the **previous 45 completed SPY sessions**.
   Require at least 30 examples for a comparable original-direction
   versus countertrend subgroup; weight 40 closest patterns.
4. Estimate historical win frequency and **net one-contract option P&L**,
   shrinking the local estimate toward the prior subgroup base rate,
   subtracting an extra $2 roundtrip modeled execution cost, and
   adding an uncertainty lower bound.
5. Route the event to **CALL, PUT, or NO TRADE**. Higher tiers demand
   independently positive local and pooled expert results and some
   demand at least 60% or 75% estimated win rate **and positive
   uncertainty-adjusted net profit**.

All policies keep the **same 30% high-water trailing stop**, 35%
independent initial-premium stop, ten-minute max hold, full position
exit, and **zero profit targets or scale-outs**.

The model explains its proposed option, historical analog sample count,
locally matched average profit, pooled profit, estimated win frequency,
profit after friction and stress lower bound.

### Completed results — actual archived Railway study

128 sessions / 49,792 SPY minute snapshots, with 562 original
four-factor signals. On 534 events, both long CALL and long PUT
candidate option quotes and ten-minute exit traces could be modeled.

The 45-session rolling prior-only training window leaves
**83 after-warmup calendar sessions**, across three predeclared
chronological market groups. Each number below models a separate
one-contract hypothetical entry when admitted; no funded portfolio.

| Entry policy | Trades | Actual modeled wins | Win rate | One-contract scenario aggregate | Extra $2/trade stress |
|---|---:|---:|---:|---:|---:|
| Always original momentum direction | 351 | 120 | 34.19% | **-$2,275.30** | -$2,977.30 |
| Always take the **opposite** direction | 353 | 124 | 35.13% | **-$1,770.90** | -$2,476.90 |
| **EdgeForge** best predicted net side, if estimated >$0 | 14 | 4 | 28.57% | **-$192.20** | **-$220.20** |
| Local AND pooled positive expert agreement | 0 | — | — | $0 (NO TRADE) | $0 |
| Agreement plus >=60% and positive predicted lower bound | 0 | — | — | $0 (NO TRADE) | $0 |
| Agreement plus >=75% and positive predicted lower bound | 0 | — | — | $0 (NO TRADE) | $0 |
| Agreement plus illustrative <=$115 one-contract debit | 0 | — | — | $0 (NO TRADE) | $0 |

**The dynamic CALL/PUT router did NOT discover an edge.** It mainly
preferred the countertrend side (13 of 14 executed research candidates),
and those entries lost. On the historical validation group the
best-net router admitted exactly one trade; it lost $31.30. The
same setup lost $160.90 during its development group. Historical
sample preference does not guarantee future directional skill.

The five-session block-bootstrap descriptive interval for its
additional-friction-adjusted average hypothetical profit per
calendar session was **[-$5.4494, -$0.4735]**, fully negative.
This does not constitute execution-grade proof because the archive
has already been inspected in previous experiments and the quoted
market is bucket-based.

The previous highest observed morning/high-volume subset (+$290.70
over 81 scenarios) was likewise sample-sensitive and failed stricter
past-only profitability evidence. Nothing here overrides that result
or licenses a claim of 75% wins.

### What was actually built and what is not built

**Built:** research-only bidirectional feature representation,
causal event-to-option entry mapping, two-sided historical return
labels, rolling prior-session analog ranking, cost-stressed expected
option P&L and lower-bound filtering, deterministic tie-breaking,
full abstention behavior, example one-contract affordability gate,
per-day and regime performance, 5-day block bootstrap confidence
diagnostics, structured prediction logs and tests.

**Not built:** trustworthy live-broker order placement, new Android
trading controls, an executable NBBO dataset, option market-depth
model, fully observed SPY PCR factor, real-money profit strategy or
a validated 75% accuracy model.

**Data warning:** historical OPRA CBBO-1m observations were stored in
one-minute buckets. They are NOT independently verified as-of
event-time, executable NBBO quotes. Greeks, sampled premium and
bid pricing, historical put/call ratios, and simulated fills have
substantial measurement limits. Slippage and fees were imposed, but
are not guaranteed to match live broker execution. Previously
inspected calendar partitions are NOT untouched forward testing.

### Engineering recommendation

- **Do not merge this model into live trading** merely because the
  architecture is more elaborate; the observed net EV was negative.
- Keep EdgeForge as a **research infrastructure experiment** capable
  of safely comparing direction, matched analogs, and *no trade*.
- For genuinely stronger entries, the bottleneck is **new data and
  better ground-truth fill economics**, particularly timestamped SPY
  OHLCV, option event-time NBBO, actual spread, available size and
  possible true same-time SPY-chain put/call data.
- During forward paper observation, define small, auditable entry
  hypotheses before looking at results and measure **both hit rate
  and post-fee expected return**. Do not fit ever narrower bins to
  the already-inspected 128 sessions.
- No new paid quotes were purchased and no orders were placed.

### Reproduction and artifacts

```bash
python -m pytest -q tests/test_edgeforge_bidirectional.py
python -m engine.edgeforge_bidirectional \
  --data-dir /data/research \
  --output-dir /data/research/edgeforge
```

- Engine: `engine/edgeforge_bidirectional.py`
- Regression suite: `tests/test_edgeforge_bidirectional.py`
- Results: `research/results/edgeforge_bidirectional_2026_10_11.json`
- Railway artifacts on the existing research volume:
  `/data/research/edgeforge/report.json`,
  `/data/research/edgeforge/trade_ledger.csv`,
  `/data/research/edgeforge/forecast_diagnostics.json`
- GitHub Actions workflow: `exit-policy-research-tests`.

No Android production app, cash balance, stored paper positions,
live permissions or broker orders were changed.
