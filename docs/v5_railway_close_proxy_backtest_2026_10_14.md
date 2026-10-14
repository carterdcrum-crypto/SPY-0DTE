# Railway archival V5 research replay — 2026-10-14

**Executed** on existing Railway `spy-0dte-research` service using
`python -m engine.v5_archive_proxy --data-dir /data/research`
and the existing persistent /data volume. Full generated machine-readable
`report.json`, `trades.csv`, and `daily_equity.csv` are retained at
`/data/research/v5_archive_2026_10_14/` on the Railway service.

**Evidence:** research Railway deployment
`2646ee20-c2f6-4e42-97a0-fa422606270f`, deploy log
`RAILWAY_V5_CLOSE_PROXY_RESULT` — actual 128 historical archive sessions
2026-04-01 through 2026-10-06, initial $1,000 for each independent arm.
The V4 3x arm exactly reproduced the earlier published model.

| Arm | Ending cash equity | First session ≥$5,000 | Net profit | Scored trades | Win rate | Profit factor | Max EOD DD (USD; % peak) |
|---|---:|---:|---:|---:|---:|---:|---:|
| **V4 original quality 3x** | **$5,109.50** | 121 | $4,109.50 | 146 | 53.42% | 2.544 | $238.90; 4.81% |
| **V5 close-only current proxy** | $1,514.10 | Never | $514.10 | 6 | 50.00% | 4.766 | $136.50; 8.27% |
| **V5 close-only challenger proxy** | **$6,165.00** | **54** | $5,165.00 | 72 | 55.56% | 3.241 | $569.60; 8.46% |
| **V5 close-only unrestricted research STRESS ONLY** | $507,706.50 | 4 | $506,706.50 | 90 | 54.44% | 2.017 | **$168,898.50; 92.93%** |

Additional model diagnostics:

- V4 max contracts/trade 3; challenger max 21; current V5 max 6;
  unrestricted stress max **7,350** — not actionable without genuine NBBO
  depth, price impact and real fill proof.
- Original V4 extra $2 per-contract cost stress net profit: $3,579.50.
  Current V5 close proxy: $488.10. Challenger close proxy: $4,605.00.
  Unrestricted: $415,696.50, **not executable**; such price-impact-free
  liquidation assumptions invalidate real-world returns.
- **None of the 87 observed rolling 42-session windows reached 5x** for
  V4, current V5, or challenger V5. 34 of 87 for the unrestricted stress,
  but 20 of 87 lost at least 50% in 42 sessions, and its 7,350-contract
  orders lack verified liquidity. These overlapping windows are descriptive
  and must not be sold as probabilities.
- Intraday maximum drawdown is unverified. The above drawdowns measure
  **end-of-day cash account equity only**, which understates interim risk.
- All scored proxy exits used **later observed sampled option bids**, $0.01
  per share adverse slippage on each side, $0.65 per contract per side,
  100-share option contract multiplier, and T+1 proceeds settlement.
  Stop execution itself is not verified as executable at that quote.

## Essential limitations

1. **NOT a full V5 test.** The Railway archive has only SPY timestamped
   *closes* and as-of *volume_ratio*, not authentic SPY minute
   **high, low or raw volume**, nor quote bid/ask depth or executable event
   NBBO. The new replay uses explicitly labelled **close-only proxies**
   for momentum, failed-range and compression instead of fabricating candles.
   Genuine QuantConnect V5 remains blocked pending OHLCV/quote depth.
2. All 128 archive dates were **previously studied repeatedly**, so this
   result is in-sample, not an independent out-of-sample or forward result;
   comparing the four arms can overfit this very data.
3. Option market quotes came from minute CBBO bucket observations, not
   guaranteed same-time executable event NBBO. Actual market impact, 0DTE
   stop gaps, option settlement timing, partial fills, commissions and
   intraminute losses could change or reverse reported performance.
4. The challenger reached $5k on **session 54**, NOT within the target
   **42 trading sessions**. No guarded arm met the 2-month target in this
   archive. Unrestricted variant's session 4 is **not credible executable
   evidence** given quote-depth absence and extreme drawdowns.
5. No autonomous broker trades, paid options data purchases or production
   V4 policy changes occurred. LIVE retains original V4. The research
   service's original forward-watch startup was restored afterward.

**Decision:** V5 challenger proxy warrants future genuine data validation,
but does **not** beat the two-month target or qualify for LIVE promotion.
