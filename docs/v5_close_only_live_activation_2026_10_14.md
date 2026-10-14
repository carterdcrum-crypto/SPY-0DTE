# V5 CLOSE-ONLY UNRESTRICTED: USER-SELECTED LIVE + PAPER (2026-10-14)

The user requested production selection of the exact
`v5_close_proxy_unrestricted_RESEARCH_ONLY` challenger, not the
risk-capped `v5_close_proxy_challenger`. This release ports the **close-only
challenger signals, all-settled-cash desired quantity, 35% observed-premium
hard stop, 25% observed-premium trail, close reversal and 18-minute holding
limit** to genuine market-data readers. No OHLCV is fabricated; signals use
actual completed closes and as-of volume ratios. All option prices must
come from Webull's timestamped production bid/ask and genuine displayed
bid/ask sizes. Exit orders are routed through existing LIVE single-writer
journal, reconciler and owner activation, never through paper mock fills.

## Production switching

- `LIVE_STRATEGY_PROFILE=v5_close_only_unrestricted_research` selects
  `V5CloseOnlyLiveStrategy` as the user-directed live **signal policy**.
- `PAPER_STRATEGY_PROFILE=v5_close_only_unrestricted_research` selects
  `V5CloseOnlyPaperTrader`, using the existing persistent T+1 paper ledger.
- `SPY_STRATEGY_PROFILE=v4_original_3x` remains the original expected
  system profile for legacy operations; neither V4 logic nor existing
  orders are deleted. Switch the above two profile vars to
  `v4_original_3x` to roll back future entries.
- All V5 and V4 positions keep their original exit logic after a profile
  switch; open-position reconciliation remains mandatory.

**CRITICAL: "unrestricted" refers to the archival research arm's desire
to spend all settled cash, not permission to bypass risk systems.** Final
LIVE quantity is independently bounded by the account's OWNER-APPROVED
max contracts, percent equity exposure, current remaining daily-loss
allowance, actual broker settled buying power, and the verified current
option bid/ask sizes. This means the LIVE account may purchase far fewer
contracts than the theoretical unrestricted archive. These mandatory
guards are never disabled. Existing owner LIVE auto-trade switch remains
the only entry authority; setting the strategy profile does not enable it.

## The $507,706 archival illusion

Starting with $1,000, the retrospective **not executable** replay ended at
**$507,706.50** (net profit **$506,706.50**) because it multiplied observed
minute-bucket option price changes by purchases of up to **7,350 contracts
on ONE trade**, allowed rapid full-account compounding, and assumed those
thousands of contracts could all buy at a sampled ask and sell at a
sampled bid with just one-cent per-share slippage. There was no proof of
NBBO available size, price impact, reliable exercise/exits or
sub-minute stops. This is NOT a valid forecast of live profits.
The modeled worst end-of-day cash-equity drawdown was **$168,898.50
(92.93% from peak)**. Twenty of 87 overlapping 42-session historical
windows halved the account. The model is neither independently
out-of-sample nor prospective validated. Those windows are descriptive,
not statistically independent or probability estimates.

Risk of a long 0DTE contract = all of its premium, regardless of a
35% trigger stop. A filled limit buy and submitted bid-priced exit can
still fail, especially near expiration; stale quotes must block entry.
The delayed market feed previously measured around 15 minutes old.
**No stale prices may authorize a real-money order.**
