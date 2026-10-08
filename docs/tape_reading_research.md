# Tape-confirmed SPY 0DTE entries — research-only

## What the tape actually tells us

The **underlying SPY stock's** time-and-sales provides individually
timestamped executions and share quantities. Ask-side executed trades are
typically buyer-aggressive; bid-side executed trades are typically
seller-aggressive. For an equity venue's native Databento MBP-1 schema, a
`B` aggressor-side trade is buy-initiated and `A` is sell-initiated.
For an equity trade reported with `N`/unknown, infer side **only if
the contemporaneous pre-trade NBBO is provided**: prints at the ask are
classified buy, at the bid sell; mid-spread executions remain unknown.

**Do not interpret OPRA options `side=N` as buyer/seller volume.**
OPRA does not disseminate trade aggressor side; proper inference
requires trade-time NBBO (TCBBO), with known limitations from complex
orders, midpoint prints, late reports, spreads and crossed quotes.
For the initial version use **SPY underlying equity prints**, not OPRA.

### Causal signal recipe — predeclared, not optimized

1. Either the existing 1-minute bullish breakout/acceleration detector
   or a **fresh eight-minute bullish high / bearish low break** triggers
   a candidate. For the independent structure variant, the price trend must
   exceed 2 basis points in the relevant direction. All price structure
   comparisons use only **preceding** frames, not the current frame in
   its own reference window. The original no-tape event study remains weak.
2. TapeReader considers only timestamped prints **observed** before the
   decision and within the last **30 seconds**; last print must be
   no older than **5 seconds**. Source-latency >2 seconds fails closed.
3. Require at least **12 prints, 1,500 SPY shares**, and a tight
   contemporaneous SPY equity spread (<=3 basis points).
4. Calculate volume-weighted aggressive flow:

   `signed_flow = (buyer_aggressive_shares - seller_aggressive_shares) / all_shares`

   Neutral/unknown prints **contribute to the denominator** but not the
   numerator. Require absolute flow >= 0.32.
5. Confirm that price moved the same direction as signed flow by at least
   1 basis point over this observed tape window. Divergence or no price
   movement is a **possible absorption** event, so **do not trade**.
   Skip if price has moved >12 basis points (avoid chasing).
6. Buyer-confirmed event buys an actual quoted SPY **CALL**;
   seller-confirmed event buys an actual quoted SPY **PUT**.
   If the tape is missing/stale/ambiguous, **do nothing**.
   The next-frame option fill must pass the 25%-of-equity premium budget,
   100%-of-start-of-day *cumulative* gross premium allowance, options
   bid/ask spread, and cash-account settlement checks.
7. Existing profit ratchet/trailing exits and the tested optional
   option-bid stop and session/week entry pauses remain in force.

These thresholds are hypotheses for research. Sweeps, iceberg orders,
order-book absorption, hidden liquidity and spoofing **cannot be
identified reliably from time-and-sales alone**. Distinguishing them
requires richer depth/event data, quality checks, and validation.
One-minute bars are not tape. Short-term order-flow imbalance has
empirical links to price changes, but these are not established net of
our options spreads or proof of 100% weekly SPY 0DTE gains.

## What data is actually available

**Existing research archive:** EQUS.MINI `ohlcv-1m` underlying SPY
minute bars and OPRA.PILLAR `cbbo-1m` option quotes over 128 sessions.
**Not available from those files:** individual prints, event/receive
timestamps, true market-side volume, depth and book cancellations.
**Therefore no real tape-confirmed backtest performance can be computed
until a genuine tick dataset is added.**

For future historical replay, the loader accepts date-scoped SPY
stock trade sidecars `spy_tape_YYYY-MM-DD.csv.gz` with individual rows
and fields:

`event_time,observed_at,price,shares,aggressor,bid,ask,dataset,schema,symbol`

All timestamps must be explicit timezone-aware ISO-8601 timestamps;
`observed_at` must be no earlier than `event_time`. The row must
represent the **underlying SPY equity trade**, not an option execution,
and the permitted schema must represent genuine individual executions
(`mbp-1`, `tbbo`, `tcbbo` or `trades`). Bid/ask must describe a
pre-trade quote if provided. `B` maps buy aggressor and `A` maps sell
aggressor in Databento's equity direct-feed convention; `N` is unknown.
The loader does not accept 1m OHLCV or aggregated CBBO as tape.

**Before acquiring tick data**, estimate Databento EQUS.MINI/SPY
historical cost using the provider's cost-estimate API and ask for
approval before buying/downloading. Single venue data is not equivalent
to the consolidated national tape. Evaluate venue coverage, duplicate
prints, odd lots and out-of-sequence events, quote delays, halts, spread
classification, and market open/close. The tested 1-minute option quotes
are also too coarse to validate a sub-second/2-second production entry:
a stricter replay must add actual time-aligned options quotes with
observed timestamps and next-observable-fill slippage.

### Executing the audit

On **research-only** Railway service, set `RESEARCH_MODE=tapeaudit`.
It first counts historical minute files and genuine
`spy_tape_*.csv(.gz)` sidecars and, if absent, prints
`TAPE NOT BACKTESTABLE` **without returning fabricated P&L**.
If sidecars exist, it runs the tape-aware quarter-allocation backtest.
No live order or Webull key is used or changed.

## Validation protocol

Predefine parameters **before** testing, use earlier 2026 sessions for
development and new unseen sessions for validation, run consecutive
five-session weeks, count weekly doubling occurrences, and report
median/worst return and max mark-to-market drawdown. Compare
baseline call, inverse put, random-direction/permuted-sign controls,
and tape-confirmed candidate flow. Apply one/two adverse ticks and
realistic fees, spread crossing, missing quote analysis, fill
latency and cash settlement. A win rate alone is insufficient. Stop if
the 25%-per-trade / 100%-per-day limits ever fail.

**Do not turn on autonomous live trading based solely on the synthetic
unit tests or any subsequently optimized research-window results.**

## References

- Databento documentation: MBP-1 trade-side, top-of-book events:
  https://www.databento.tech/docs/schemas-and-data-formats/mbp-1
- Databento OPRA documentation: options trade aggressor side unavailable:
  https://databento.com/docs/knowledge-base/datasets/opra-pillar
- Databento equity options guide: TCBBO NBBO-at-trade inference:
  https://databento.com/docs/examples/options/equity-options-introduction
- Cont, Kukanov & Stoikov (2014), *The Price Impact of Order Book Events*:
  https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1712822

## Completed data audit, October 8, 2026

The existing Railway **research-only** persistent volume contained
**128 daily 1-minute market/option files and ZERO genuine individual
SPY trade tape sidecars**. The tape research worker correctly emitted
`TAPE NOT BACKTESTABLE` and **no tape-enhanced P&L**.
The only completed P&L tests remain the separate **non-tape** 25/100
allocation studies in `docs/quarter_risk_research.md`, in which none
of the tested variants achieved +100% in a five-trading-day block.

Python unit tests use **synthetic prints only to test code behavior**
(chronological safety, buy/sell inference, possible absorption rejection,
fresh high / low entries, and rejection when tape is unavailable).
These tests are **not historical tape backtests** and cannot establish
an economic edge.

Before collecting new tick data, quantify the proposed vendor costs
and licensing restrictions; no historical tick purchases were made.
