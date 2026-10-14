# QuantConnect SPY 0DTE V4-vs-V5 comparison (2026-10-14)

**Status: implemented; historical QuantConnect runs NOT submitted.** No connected
QuantConnect account/API or logged-in cloud backtest node is available. Do not
describe any future fills, backtests or optimizations as completed.

## Free QuantConnect setup

QuantConnect's **Free** organization includes **one cloud backtest node**
and cloud access to most minute/daily datasets, including US equity options.
The free tier **does not include API/CLI access** or free Object Store storage.
No subscription, QuantConnect API key, broker credential, or payment is needed
to copy these files into the *web* Algorithm Lab and press **Backtest**.

1. Create a **new Python algorithm project** in https://www.quantconnect.com/.
2. Copy `quantconnect/v5_main.py` to that project's `main.py`.
3. Add `quantconnect/v5_core.py` as `v5_core.py` alongside it.
4. Choose a `variant` in project **Parameters** and Backtest each independently.
5. Archive native backtest results, orders, fees, detailed holdings history,
   statistics and log line `V5_QC_RESEARCH_RESULT` as actual evidence.

**DO NOT overwrite this repository's existing** `quantconnect/main.py` or
`v4_strategy.py`: they belong to an unrelated earlier spread-EV V4 exploration,
not the frozen original V4 3x. The `v4_original` arm in `v5_main.py` is a new
isolation of the frozen **trend / stretch reclaim + quality sizing** original.
`tests/test_quantconnect_v5.py` checks the frozen signal-side/setup parity.

## Trading and fill realism

- `SPY` RAW 1-minute stock bars and `include_weeklys().expiration(0, 0)`
  1-minute option quote bars, chain Greeks and recorded bid/ask sizes.
- Signals use only *completed* SPY minute bars. Select contracts after a
  full extra minute, exclusively from same-slice option NBBO quote bars.
- Skip missing/stale quotes, missing depth, wide spreads, unreasonable IV/RV,
  delta outside 0.40–0.60, or unavailable settled cash.
- Use LEAN long option market orders against built-in ask/bid fill modeling,
  $0.01 per option share additional adverse slippage, $0.65 commission per
  contract on **each** side, CASH account with T+1 US option settlement.
  The cash-account buying-power model remains authoritative.
- Options have 100 multiplier; option premium can be lost in full. Modeled
  stops are **not guaranteed** to execute at the trigger price.
- At most one position per arm, dynamic quantities constrained by NBBO depth,
  verified settled capital and full-premium exposure. The 'unrestricted' arm
  only removes the research exposure limit; it cannot borrow or disregard
  quote-depth limits. **Never promote unrestricted sizing to LIVE.**
- Liquidate before session close with LEAN's session-calendar scheduling,
  including early-close days. If no executable quote exists, the order can
  remain unfilled; count as a data/execution blocker, never fabricated P&L.
- The QuantConnect minute NBBO archive is distinct from verified tick/event
  NBBO and actual market fills. Never call this a validated live fill history.

## Parameter matrix — run each window in chronological order

| Arm (`variant`) | Signal model | Sizing | Exit |
|---|---|---|---|
| `v4_original` | Frozen trend/stretch reclaim | V4 quality 1/2/3 | 35% hard, 30% trail; conditional 10m |
| `v5_current` | Momentum, liquidity reversal, expansion; RVOL >=1.5 | 20–30% full premium | 35% hard, 30% trail; 12m time |
| `v5_optimized` | **Unvalidated challenger** RVOL >=1.25 and wider compression definition | 25–35% premium | 35% hard, 25% trail; 18m time |
| `v5_unrestricted_research` | Same V5 challenger signals | Settled cash and NBBO size only; **stress** | 35% hard, 25% trail; 18m time |

Research parameters: `variant`, `start` and `end` (YYYY-MM-DD),
`rvol_min`, `trailing` (fraction), `max_exposure` (fraction, at most .35).
Default dates 2024-01-02 through 2025-12-31, $1,000 initial cash every run.

- **Train/discovery:** 2024-01-02 → 2024-09-30
- **Validation:** 2024-10-01 → 2025-03-31
- **Locked, untouched holdout:** 2025-04-01 → 2025-12-31
- Repeat 42-session windows through locked holdout *only after freezing*
  the best parameter set from the earlier window. The free platform's
  backtesting limit is one job concurrently; parameter sweep is sequential.
- Precommit search grid on **training only**:
  `rvol_min ∈ {1.25, 1.50, 1.75}`,
  `trailing ∈ {0.20, 0.25, 0.30}`,
  `max_exposure ∈ {0.20, 0.30, 0.35}`; not 27 independent discoveries
  if one set is tuned post-hoc. Fewer param configurations is preferable.

## Comparison criteria

Each reported arm requires its *own* real QuantConnect run ID,
date window, bar/quote coverage confirmation and filled orders.
Report: sessions to $5k (if any), net liquidation-value ending equity,
intraday & EOD equity-peak drawdowns, closed-trade win rate, profit factor,
daily Sharpe, spread/slippage sensitivities, execution failures, and
41/42-session rolling target frequency and downside frequency **as descriptive
in-sample or untouched holdout observations, not guaranteed probabilities**.
Risk-of-ruin model is unspecified until actual holdout trade/equity data exist.

Promotion gate: no lookahead or unverified fills; no fewer than three
chronologically distinct validation windows; transaction-cost stress,
parameter stability and materially favorable holdout growth versus V4.
A robust 42-session target-rate and credible downside distribution are
required before assigning a 'winner'. Failure to reach $5k within 42
sessions is a negative result, not a reason to alter untouched holdout.

## Railway / Webull

Existing production: unchanged `engine/v4_live_strategy.py`,
`engine/v4_operational.py`, autonomous owner switch and Webull connector.
New research functions live only under `quantconnect/`. This release **never
sets a Railway LIVE strategy flag or sends an actual Webull order**.
The original V4 LIVE remains selected. The current delayed market feed
must be resolved and independently timestamped for any forward-paper
or eventual owner-authorized V5 LIVE rollout. Research source/paper promotion
should be done with a separately tested policy adapter, not a silent swap.

**Historical reference (NOT same dataset / NOT a head-to-head comparison):**
Prior 128 reused, already-inspected CBBO-minute SPY sessions (April 1–October
6 2026): V4 3x model $1,000→$5,109.50; 146 modeled trades; 53.42% wins;
worst *EOD* drawdown $238.90. These are retrospective proxies and are
**not** evidence of V5 outperforming V4 or hitting $5k in 42 sessions.
