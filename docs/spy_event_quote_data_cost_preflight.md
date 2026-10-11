# SPY 0DTE actual event-quote data sourcing: executed free preflight (2026-10-11)

## Result and scope
Run on the actual persistent Railway research volume with the configured
Databento API key, using **only metadata.get_cost (free) and existing local
file inventory**. No downloads, subscription changes, orders, or data charges.
Deployment: `5ddafb58-0490-41b5-9d44-e2a3ea2e11e4`.
GitHub Actions Python preflight and regression tests passed.

The research volume has **128 legacy one-minute sampled SPY option sessions**
from 2026-04-01 through 2026-10-06. Strict inventory found:
- Full historical SPY OHLCV source files: **0**.
- Real event-observed SPY 0DTE option NBBO files: **0**.
- Historical point-in-time all-SPY-expirations put/call cumulative volume files: **0**.
- Retained vendor raw DBN/parquet artifacts: **0**.
- Genuine fully matched backtest sessions: **0**.

No untestable trades have been scored as zero-return trades or as profits.

## Real account-priced Databento cost metadata

Databento `OPRA.PILLAR` parent `SPY.OPT`, one full US market session.
To calculate the **exact original full-chain SPY put/call volume ratio
without future volume leakage**, purchase SPY-wide `ohlcv-1m` (option
volume known after the minute ends), **NOT** only a subset of 0DTE symbols.
Event-observed candidate fill quotes can come from selected option
`cmbp-1` symbol histories (OCC 21-character, space-padded root).
The historical underlying comes from `EQUS.MINI ohlcv-1m` for SPY.

| Session | All-SPY contract volume in 1m bins | 12 candidate event-quote symbols | SPY OHLCV 1m | Combined preliminary estimate |
|---|---:|---:|---:|---:|
| 2026-04-01 | $7.2801 | $0.3516 | $0.0002 | **$7.6319** |
| 2026-07-08 | $6.2611 | $0.4167 | $0.0002 | **$6.6780** |
| 2026-10-06 | $5.8572 | $0.1605 | $0.0002 | **$6.0179** |

A naive 20-session extrapolation of the above limited quote-universe
scope is **~$135.52 before additional required statistics/definitions**.
The precise 20 sessions and their actual quote symbol coverage must be
quoted separately before ordering; this is NOT a guaranteed ceiling.
A three-sample-session pilot at these exact sample dates is quoted
**~$20.33**, still not an approved purchase.

Additional vendor cost checks:
- **All SPY option CMBP-1 events**, not merely 12 selected symbols:
  $22.2609, $24.3741, $10.5116 per sample session.
- **All SPY option trades** instead of aggregated minute volume:
  $18.6120, $21.1918, $19.8037 per sample session.
- **Trade-time consolidated BBO for 12 symbols (TCBBO)**:
  $7.8606, $11.1168, $14.3406, more expensive than CMBP-1 for
  these particular vendor-selected quotes; it is also not continuous NBBO.

Historical trade-vol bars report per-minute volume by contract; the
earliest as-of PCR read is after that minute has fully completed.
Full-chain coverage, cancel/correction semantics and quote timestamps
must still be validated, plus time-of-day OI/definitions and as-of
Greeks. Twelve quotes per session are an **upper bound on a low-cost
candidate budget scope**, not proof of sufficient market coverage
or a valid trade universe selected without future knowledge.

## Decision and guardrails

Do not start any historical streaming or batch request unless the
account owner authorizes an explicit dollar-or-credit spending ceiling.
Databento's cost estimate does not show remaining credit balance;
the account billing page displays it.

**Suggested next paid stage:** a budget-capped **three-session pilot**
with full-chain 1m SPY volume and genuine event-time option quotes, plus
underlying bars and time-of-day contract definitions/statistics.
First validate as-of chronology, actual quote bid/ask/size and source
completeness; then implement the frozen 20% trail / +100% majority sale
comparison on those real events. Only enlarge to at least 20
chronologically selected untouched sessions if a pilot passes coverage
and approved budget. Report no valid full strategy P&L otherwise.

Do not enable the live-trade switch, change trading risk controls,
convert minute CBBO buckets into event timestamps, substitute a
partial-chain put/call ratio, or manufacture nonexistent results.

## Reproduction
```bash
python -m pytest -q tests/test_spy_event_data_preflight.py
python -m engine.spy_event_data_preflight \
  --data-dir /data/research \
  --output /data/research/event_nbbo_price_audit.json
```
Data inventory and vendor price report:
`research/results/spy_event_data_metadata_preflight_2026_10_11.json`.
Only cost metadata and filenames are committed to GitHub; historical
vendor market-data bytes stay on the user's persistent research volume.
