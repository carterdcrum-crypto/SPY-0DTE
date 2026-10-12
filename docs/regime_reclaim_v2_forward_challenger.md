# Regime Reclaim V2: predeclared causal challenger

**Status: isolated experiment.** V1 remains the selected frozen SPY 0DTE
forward-test algorithm. Neither PAPER, SHADOW, LIVE, Webull nor Android trading
is changed. **No verified new V2 forward returns yet.**

V2 lives in engine/regime_reclaim_v2.py. It compares THREE arms on the same
new data: V1 frozen; V2 router + price confirmation + option execution ranking
with unchanged V1 exits; and the same V2 entries with adaptive trailing exits.

## New V2 rules (predeclared; never tuned on 128 old inspected sessions)

All candidate trend and stretch signals originate from the exact locked V1
signal function. No new breakout or range-failure signals are introduced.
At the completed one-minute signal bar:

- **Regime**: ten-bar EMA21 slope abs >=2 bp and 15-bar price-path
  efficiency >=0.25 implies trend (slope sign gives direction);
  otherwise abs slope <=6 bp and efficiency <0.45 implies range.
  Otherwise the regime is uncertain (no trade). Trend has precedence.
- **Router**: trend_reclaim only with trend direction; stretch_reclaim
  only in range. Does not mix mean-reversion with strong trends.
- **Candlestick**: directional candle body >=20% of its full range
  in trend, >=25% in range. Favorable closing location in the
  upper 40% for calls or lower 40% for puts.
- **RVOL**: >=0.90 for trend; >=0.85 for range.
- **VWAP**: session-to-date typical-price * volume, using completed
  OHLCV one-minute bars only. Trend trades must be on its proper
  side. Range trades need prior momentum into the stretch and
  a new close converging toward as-of VWAP.
- **Contract**: at the first admissible event-observed 0DTE
  quote timestamp 1-2 minutes AFTER the signal, allow delta
  0.40-0.60, bid/ask depth >=1, spread <=3.5%, option IV / SPY
  as-of realized volatility <=1.5. Rank contracts observed at
  that SAME first time by
  10*spread_fraction + 0.6*|absolute_delta - target| + .08/sqrt(min_depth).
  Delta target .55 for trend, .45 for range. These numbers are
  uncalibrated heuristics, not claimed predictive edges.
  **Never wait for a later better quote** after a failing first observation.
- **Adaptive exit arm only**: base full-position 30% trailing,
  permanently tighten to 20% after at least two completed bars
  against EMA8 with adverse last-bar movement, and not before
  entry+2 minutes. At each observed option quote use only
  SPY bars that were already available. Independent 35% original
  premium hard stop and 10-minute max hold always remain.
  No fixed take profit, no runner, no scale-outs.

Both strategies: one option contract/position at a time, $1,000
initial settled cash, T+1 proceeds (no same-day recycling),
bid exit, ask entry, 0.65 fee per side, $0.01/share slippage
and separately reported extra $2 per scored round trip friction.

## Run same-session V1 / V2 comparison

    python -m pytest -q tests/test_regime_reclaim_v2.py tests/test_frozen_regime_forward.py
    python -m engine.regime_reclaim_v2 \
      --event-dir /data/research/forward_events \
      --legacy-dir /data/research \
      --output-dir /data/research/regime_v2_comparison

Output includes comparison_manifest.json plus three pairs of decision and
paper-trade CSVs. For each arm: modeled trades, scored versus unscored,
win rate, average scored return, P&L, profit factor, friction stress
and maximum modeled **end-of-day** drawdown.

Event-time data schema and provenance requirements remain in
docs/frozen_regime_prospective_replay.md. Both arms MUST read genuinely
new paired minute OHLCV and independently observed option event NBBO
sessions later than the last historically inspected archive date.
Missing paired event data gives BLOCKED_NO_NEW_EVENT_SESSIONS,
never fake profit or a 0%-win rate. Missing future executable
bid leaves premium spent/reserved, and P&L labeled incomplete.

V2 is only a candidate, not a demonstrated improvement: extra
filtering may decrease trade count and money earned. A useful
promotion check needs multiple untouched weeks and sufficiently
many actual independently observed trades, stress for extra fees
and slippage, regime-balanced returns and max drawdown, compared
against the original V1 control on the very same new sessions.
No broker orders or market-data purchases are made.
