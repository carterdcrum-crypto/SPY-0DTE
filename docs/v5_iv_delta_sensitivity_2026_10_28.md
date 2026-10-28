# V5 IV/RV versus absolute-delta screen: archived 128-session sensitivity — 2026-10-28

**Executed**, not projected. Railway research deployment `2ad6e2c7-253a-45b7-86eb-e476f7332059`; logged `V5_IV_DELTA_RESULT` lines; full JSON result preserved at `/data/research/v5_iv_delta_sweep_2026_10_28/report.json` on the standalone Railway research volume. Source: `engine/v5_iv_delta_sensitivity.py`, branch `research/v5-iv-delta-sensitivity-2026-10-28`. **Main/live/paper trading not changed.**

Data: 128 previously studied archived sessions **2026-04-01–2026-10-06**, $1,000 hypothetical starting balance, same V5 close-proxy momentum ignition/liquidity reversal/volatility expansion. Baseline entry-selection events were checked **on every session** against frozen `proxy_candidates` and $867,017.60 matched precisely. Only max IV/RV and absolute-delta screening ranges were varied.

| IV/RV ceiling | Absolute delta range | End equity ($) | Scored positions | Win % | Profit factor | True max EOD drawdown % | Hypothetical max contracts in one trade |
|---|---|---:|---:|---:|---:|---:|---:|
| **1.5 baseline** | **0.40–0.60** | **867,017.60** | **117** | **54.70** | **3.268** | **32.30** | **11,226** |
| 2.0 | 0.40–0.60 | 25,497.40 | 406 | 42.86 | 1.234 | 63.44 | 301 |
| 2.5 | 0.40–0.60 | 16,790.20 | 630 | 43.49 | 1.153 | 68.38 | 141 |
| 1.5 | 0.35–0.65 | **930,011.50** | **121** | **54.55** | **3.548** | **32.31** | **12,042** |
| 1.5 | 0.30–0.70 | 904,630.50 | 121 | 54.55 | 3.526 | 34.08 | 11,714 |
| 2.0 | 0.35–0.65 | 32,067.80 | 418 | 42.82 | 1.265 | 65.39 | 337 |
| 2.0 | 0.30–0.70 | 32,044.50 | 415 | 42.89 | 1.266 | 65.78 | 337 |
| 2.5 | 0.35–0.65 | 23,352.50 | 639 | 42.88 | 1.189 | 68.73 | 217 |
| 2.5 | 0.30–0.70 | 29,939.50 | 634 | 42.90 | 1.208 | 69.82 | 278 |

## Interpretation

- Opening IV/RV from 1.5 to 2.0 or 2.5 creates considerably more sampled candidate opportunities, **but much worse compounded performance and risk** in this *already-inspected* archive. There is no evidence from this experiment to relax the IV ceiling in live trading.
- Widening absolute delta modestly to **0.35–0.65 while retaining IV/RV <=1.5** improved ending hypothetical equity relative to baseline, but only **four more scored positions** and negligible drawdown difference; this is far too small and contaminated by repeated in-sample parameter searches to establish a new edge.
- The very large hypothetical ending balances are **not a reliable prediction of executable returns**: archive lacks event-time bid/ask SIZE, SPY true minute OHLCV and realistic capacity/impact, and it sometimes models **12,042 contracts in one fill**. The returns are sensitive to compounded speculative size and suffer path-dependence.
- The baseline's **32.30% true peak-to-trough EOD drawdown** is a different measure from its smaller worst single-day percentage decline; EOD is NOT intraday max drawdown.

## Precisely held constant

Same chronological 128 sessions, same V5 three close-proxy setups, first qualifying sampled quote after signal, sampled option ask+1 cent/share entry and bid-1 cent/share exit plus $0.65/contract per side, 3% spread limit, 35% hard stop, 25% trailing stop, SPY reversal after 10 minutes, 18-minute max hold, EOD drawdown throttle at 100/70/40/15%, one position at a time, settled cash only with next-session sale settlement. Each variant uses a fresh independent $1,000 simulated account.

## Caveats & decision

This is an *in-sample close-only proxy*, not true V5 OHLCV/NBBO backtesting or independent out-of-sample. No purchases or brokerage orders. Keep **IV/RV <=1.5** in production. At most test **delta 0.35–0.65 in an isolated paper/forward research arm** and require timestamped executable quotes, depth, slippage and genuinely unseen sessions before promotion.
