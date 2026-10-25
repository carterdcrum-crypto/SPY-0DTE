# SPY 0DTE — $867,017.60 V5 Research-Model Parity Audit

**Research variant:** `full_cash_with_dd_throttle` in
`engine/v5_conditional_capital_study.py` on
`research/v5-conditional-capital-2026-10-25` (archived study commit
`974052c7aa9c3d04a667ef04a12c6b4f1da969ef`).

**Historical result:** $1,000 to **$867,017.60 modeled equity**, 117
modeled winning-or-losing positions, **54.70% win rate**, **32.30% maximum
end-of-day percentage drawdown**, across 128 previously inspected sessions
from 2026-04-01 through 2026-10-06. No independent holdout.

## Which exact *rules* production implements

- **Three entries:** identical `close_proxy_signals(...,challenger=True)`
  implementation, momentum ignition, liquidity reversal and volatility
  expansion, never the separate experimental predictive weighting.
- **Close timing:** signal after the 36th completed SPY minute, next completed
  entry-confirmation minute; minimum **37** consecutive real minutes. Prior
  55-/57-bar startup gates were extra restrictions and were corrected.
- **Spread / greeks:** calls/puts selected at absolute delta 0.40–0.60,
  maximum 3% bid/ask spread, IV/RV <= 1.5.
- **Premium:** long 0DTE only; entry asks plus $0.01 per share and $0.65
  modeled fee per contract, exit bids minus corresponding modeled costs.
- **Exit:** 35% hard premium stop, 25% from-premium-peak trailing stop,
  SPY close-proxy reversal after 10 minutes, 18-minute maximum model hold,
  session liquidation before expiration.
- **Cash:** entire long-option debit is at risk. Buy only with already-settled
  cash, never same-day unsettled sale proceeds or borrowed money.
- **Sizing:** use 100% of *session-opening* cash/equity risk budget when the
  prior-session end-of-day peak drawdown is <2%; 70% from 2–<5%; 40% from
  5–<10%; 15% from 10% onward. Budget is bounded by settled buying power.
  Peak and session opening marks persist through process restarts.
- **Trade pacing:** one position at a time with no extra 180-second post-exit
  cooldown for V5. V4 keeps its legacy cooldown. Pending/unknown broker order
  reconciliation and per-signal idempotency are NOT disabled.
- **Final execution:** LIVE and PAPER share the same signal and sizing policy;
  LIVE also rechecks fresh settled buying power, timestamped options quote,
  full ask/cash debit and BOTH displayed bid/ask sizes at order submission.
  Both streams preserve their existing user-controlled enable switches.

## What cannot honestly be made identical

The $867,017.60 replay is NOT an executable reconstruction:

1. Historical archived data have sampled/minute option CBBO prices, not
   event-time option NBBO and displayed executable bid/ask size. The replay
   assumed as many as **11,226 contracts in one trade** without proving that
   size could execute. LIVE and PAPER **must cap orders to real displayed size**.
2. Historical SPY close/relative-volume snapshots omit genuine full high/low
   and volume candles. Live SPY candles are fetched from Webull, and realized
   volatility is recomputed from them. Thus identical code can produce
   different signals or IV/RV gate outcomes on actual observations.
3. A historical sampled-minute quote search (up to two minutes after signal)
   cannot always match current authenticated option-market snapshots to the
   second. Multiple same-minute setups may resolve to a different choice.
4. Research has true modeled daily close equity; operational drawdown peak
   tracks **last observed flat-account equity per session**. Trades with
   unknown or delayed exits, gaps in live broker equity, or external account
   cash flows can change the measurement. It does not claim otherwise.
5. Research models discrete fills at sampled bid/ask prices. Actual Webull
   limit-order fills, liquidity impact, slippage and intraday drawdown can
   materially differ, including total premium loss.

**Consequently, only the signal, exposure and exit policy are being aligned.
A guarantee of identical trades, risk or $867,017.60 in the live/paper system
would be false.** This historical return is extremely speculative and not a
reasonable prediction of future account growth.

This audit does not activate trading or authorize a Webull order. The
autonomous LIVE owner switch and paper switch remain separate and unchanged.
