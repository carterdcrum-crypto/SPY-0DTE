# Historical Regime Reclaim no-max-hold comparison — October 12, 2026

**Executed on Railway research volume, 128 previously inspected SPY 0DTE sessions (April 1–October 6, 2026)**.
This is **not a new out-of-sample backtest** or proof of broker-executable
performance. Source is archival Databento **CBBO-1m minute buckets** with SPY
completed closes, not true event-observed OPRA NBBO; intrabar stop fills,
slippage, volume and drawdown remain uncertain. V2 regime/candle/VWAP entries
cannot be tested from this old close-only archive; the comparison isolates
**the exact original frozen V1 entries** while changing only the exit logic.
This study uses $1,000 starting settled cash, one contract/position at a time,
T+1 settlement (no intraday proceeds recycling), 0.65 per-side fee and 0.01
per-option-share slippage.

| Policy | Ending modeled cash | Modeled net profit | Scored trades | Win % | **Max EOD cash drawdown** |
|---|---:|---:|---:|---:|---:|
| Original V1: 10-minute limit, 30% trail | **$2,942.20** | **+$1,942.20** | 146 | 52.05% | **$165.00** |
| Remove timer: unchanged 30% trail | $1,857.65 | +$857.65 | 129 (+1 unknown) | 38.76% | $314.90 |
| **Remove timer + tighten to 20% on two-bar EMA8 structural deterioration** | **$3,044.00** | **+$2,044.00** | 140 | 42.14% | **$162.00** |

Modeled end-of-day cash drawdown is peak-to-trough equity based on cash +
next-day settlement proceeds; it is NOT intraday peak-to-trough option
premium drawdown and should not be interpreted as a guaranteed loss ceiling.
The no-time-limit 30% trail arm had **one exit unscorable due to missing
required final quote**. That position's debit was retained/unavailable,
not refunded or marked profitable; consequently even the $1,857.65 equity
estimate is a conservative reserve scenario, not a verified realized result.

The exact original baseline reproduces our prior 146 trades and +$1,942.20
history, providing a useful consistency check. Simply removing the timer
WITHOUT an adaptive trail reduced net modeled profit by $1,084.55 and
increased maximum observed end-of-day cash drawdown by $149.90. Removing
the timer **with** an adaptive trail showed +$101.80 higher profit and $3
lower maximum EOD drawdown versus baseline, but only on this
repeatedly inspected dataset, so the tiny apparent improvement can be
noise or selection bias.

**Selection:** V3 no-max-time with adaptive trailing is an experiment,
not an automatic LIVE trading change. The frozen V1 and V2 remain controls
and new (event-time) data is needed before promotion. No real broker
orders or new Databento data purchases were made.

The raw machine-readable Railway report is archived in
[regime_no_max_hold_128_sessions.json](regime_no_max_hold_128_sessions.json).
Percent drawdown in that *first* raw report was computed with the overall
maximum equity denominator, whereas the correct drawdown percentage
should use the peak preceding the worst trough; refer to **dollar
drawdown values**, which are correctly calculated. The implementation
has since corrected the percentage denominator for subsequent runs.
