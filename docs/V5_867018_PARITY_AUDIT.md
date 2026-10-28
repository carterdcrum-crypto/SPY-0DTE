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


## 2026-10-28 owner-selected live/paper delta variant

**Production was intentionally changed after the archived $867,017.60 run.**
The original archive/backtest remains immutable at IV/RV <=1.5 and absolute
delta 0.40–0.60. Current owner-selected V5 LIVE and PAPER use **IV/RV <=1.5
and abs(delta) 0.35–0.65**, without changing the full-cash EOD drawdown budget,
spread, fresh executable depth, account-cash, exit or V5 version lock.

This is an **experimental variation**, NOT exact reproduction of the $867K
archive. A separate nine-arm, repeatedly in-sample close-only historical
sweep reported $930,011.50 modeled ending equity for the 0.35–0.65 variant
versus $867,017.60 at baseline (121 versus 117 positions); **these quantities
are not realizable-fill evidence** and could be overfit. The analysis is on
`research/v5-iv-delta-sensitivity-2026-10-28`, documented in
`docs/v5_iv_delta_sensitivity_2026_10_28.md` on that research branch.


## 2026-10-28 owner-selected bid/ask spread gate variant

Current V5 paper and live options entry gating uses **up to 5% bid/ask
spread** (previously 3%), while keeping IV/RV <=1.5, absolute delta
0.35–0.65, verified <=3-second quote timestamps, real bid/ask depth,
settled-cash limitation, EOD drawdown throttling and the 18-minute exit.

The original $867K archived replay remains explicitly associated with
0.40–0.60 delta and 3% spread, and the later $930K archived variant with
0.35–0.65 delta and 3% spread; neither number is a verified live outcome
under the new 5% spread rule.

An in-sample 128-session **close-only proxy** study on branch
`research/v5-max-spread-sensitivity-2026-10-28` reported $1,808,494.60
hypothetical ending equity at 5% from $1,000, **but implicitly assumed a
single 25,612-contract fill with no actual displayed executable depth or
market impact**. It is not proof of a live edge. Increasing acceptable
option spreads can materially increase slippage and fill/exit risk.


## 2026-10-28 owner-selected looser liquidity-reversal entry

Production PAPER and LIVE now evaluate **liquidity_reversal_CLOSE_PROXY**
against the prior **15 closes** (previously 20), and admit reversal
efficiency **<0.75** (previously <0.60). The signal still requires the previous
close to have broken its prior range and the signal close to recross that
range in the opposite direction, with at least 1.25 observed as-of RVOL.
Momentum ignition and volatility expansion conditions are unchanged.
Historical `close_proxy_signals(...,challenger=True)` without override
preserves the original reversal parameters and archived baseline.

The research-only 128-session close-only archive at 5% max sampled spread,
IV/RV <=1.5 and absolute delta 0.35–0.65 recorded **$2,055,376.50**
hypothetical ending equity for looser liquidity reversal, versus
**$1,808,494.60** for the three-signal baseline, starting at $1,000.
Simulated positions: 125 vs 121; wins: 55.20% vs 53.72%;
true EOD max drawdown: **39.11% vs 32.24%**. The more permissive signal
**increased historical drawdown**. Sampled archive data lacks executable
NBBO size/market impact and the looser variant assumed up to **29,107 option
contracts filled at once**. These returns are not tradable forecasts or
evidence of robust out-of-sample advantage. Source branch:
`research/v5-signal-variations-2026-10-28`.
