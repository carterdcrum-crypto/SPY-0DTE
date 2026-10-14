# V4 adaptive-compounding challenger — 2026-10-14

The original `v4_original_3x` baseline and its 128-session archival result are
retained unchanged. This patch changes **PAPER/SHADOW position sizing only**:
entry and exit rules stay frozen V4. A shared quantitative sizing helper permits
more than three contracts as verified equity grows, but applies full-premium
cash risk, <=35% position exposure, 25% accrued-profit reserve, 2%/4% drawdown
throttles, owner/paper maximum quantity, and executable bid/ask depth.

The `v4_quality_sizing` historical simulation now includes two extra arms:
`v4_adaptive_compound_guarded` and
`v4_unrestricted_cash_stress_RESEARCH_ONLY`. The latter may spend *all*
settled cash on a single 0DTE long-option entry and is **never** used to
produce orders or paper autonomous actions.

**Promotion gate:** LIVE continues using exactly the original V4 3x position
sizing until a separately authorized operator sets
`V4_ADAPTIVE_LIVE_VALIDATED=true` after independent out-of-sample and
forward-paper validation. This flag is intentionally absent in Railway
production. The current owner Auto Trade toggle, broker authorization,
daily stops, 35% risk cap, and reconciled order flow remain unchanged.

**Not validated:** Prior reused 128-session CBBO-minute research is not
independent out-of-sample evidence. True event-time bid/ask depth, market-data
entitlements and slippage-aware forward fills remain outstanding. No new
profitability, time-to-$5,000, risk-of-ruin, or live win-rate claims are made.
Consecutive 0DTE premium losses can exhaust a small account despite stops.
