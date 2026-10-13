# Entry Quality 3x — selected forward paper strategy

**Chosen research arm:** `v4_quality_3x_paper`; parent comparator
`engine.regime_reclaim_v4.compare_v4`. This changes **only the
research forward tester**: no new Webull live orders, no activation of
the unrelated live strategy, and no subscription or purchases.

## Trade quantity rule
* Quality score 0–3: **1 contract**.
* Quality score 4: **up to 2 contracts**.
* Quality score 5–6: **up to 3 contracts**.
* Extra contracts require **35% modeled equity premium budget**, T+1
  settled cash, prior day account peak-to-trough EOD drawdown no worse
  than 4%, and executable contract depth observed at the selected option
  event. If restrictions apply, orders reduce to one or are vetoed;
  nothing forces three contracts.

The six-component score is the original historical 3x research rubric:
setup-adjusted trend, directional efficiency, relative SPY volume, option
spread, |delta| and IV/realized SPY volatility. All must be timestamped
and available before the entry quote. Missing data is a hard block.
The same underlying forward candidate / option contract is selected by
V2's VWAP/regime gate; V4's 10-minute conditional hold/30% base trail,
20% extension trail and 35% loss stop remain as they were.

**Historical reference (not new forward results):** on 128 previously
studied SPY minute bucket sessions, $1,000 simulated account, the one-contract
V4 arm ended at $3,173.20 (+$2,173.20), win 53.42%, max EOD dollar
drawdown $177.00. The entry-quality-3x arm ended at $5,109.50
(+$4,109.50), same win rate 53.42% with $238.90 worst modeled EOD
dollar drawdown. Actual intraday drawdown, achievable bid-side execution,
full-premium-loss risk, and larger-size slippage are not known.

**Promote to live:** NOT APPROVED; the 3x setup has not been validated
with truly new OPRA/event-time option data. The live executor is a
different strategy and cannot be renamed or considered equivalent to V4.
The future paper-replay metrics must be reviewed separately from these
already-inspected historical results, including trade-count statistics
and risk. Owner limit changes alone do not make V4 3x live.

## Operational routing added October 12, 2026

`engine/v4_operational.py` implements a separate live-market-data adaptation,
`engine/v4_paper_trader.py` runs it against the persistent T+1 paper ledger,
and `engine/v4_live_strategy.py` supplies V4 entries/exits to the existing
owner-gated autonomous Webull executor. The collector Railway service selects
this profile via `SPY_STRATEGY_PROFILE=v4_quality_3x`. Authentic completed
Webull M1 SPY bars and fresh timestamped option prices, greeks and depth
are mandatory. Failure to obtain entitlements blocks orders; old preview
snapshots are never substituted. The backtest and OPRA-event forward tester
are separate components, not a claim of validated live execution.

## V4 regime-router parity correction (October 13, 2026)

**Important correction:** the initial Oct. 12 PAPER/LIVE operational rollout
used raw frozen `identify_setups` entries without the historical V4/V2
regime router. That was **not fully the archived V4 strategy**. It has been
corrected in `engine/v4_operational.py`: the same completed-minute
`regime_reclaim_v2.route_candidate()` now gates *both* PAPER and guarded LIVE
entries. This enforces the trend-up/down versus range routing, directional
candle body and close location, relative volume, and **as-of session VWAP**
confirmation before an option-quality score can permit 1–3 contracts.
The option selection is ranked using the V2 regime-specific delta target,
spread and observed quote depth. V4's conditional-hold exit logic and
quality scoring remain active.

This fixes the **missing regime decision component**; it does not imply exact
historical replay/live-execution equivalence. The live/PAPER provider sees
Webull production snapshots, different from independent OPRA event-time
quotes in the historical forward-replay research, and the algorithm still
fails closed without current entitled stock-minute and option data. No
additional strategy profit or win rate is being claimed.
