# Forward event-market-data pipeline and Railway readiness

Railway now probes /ready for web-server liveness and retains /health as
a separately fail-closed market-worker state check. An HTTP 200 response
from /ready means the service is online, NOT that options data is fresh.
Existing Webull SANDBOX delayed snapshots are never accepted as OPRA NBBO.

The next data step is strict independent OHLCV and OPRA event ingestion:

    python -m engine.forward_event_ingest --trade-date 2026-10-13 \
      --raw-dir /data/research/forward_raw \
      --event-dir /data/research/forward_events \
      --legacy-dir /data/research \
      --output-dir /data/research/regime_v2_comparison --compare

Required independently obtained CSV sources for each session D:

    raw_spy_bars_D.csv:
      ts_start,open,high,low,close,volume
    raw_opra_events_D.csv:
      ts_recv,symbol,schema,publisher_id,bid_px_00,ask_px_00,
      bid_sz_00,ask_sz_00

Instead of the Databento-native bid_px_00/ask_px_00/bid_sz_00/ask_sz_00
names, bid/ask/bid_size/ask_size are also accepted. Source timestamps
are timezone-aware ISO8601 or nanoseconds since epoch from Databento.
The ts_start column is the start of the completed SPY one-minute bar.
The ts_recv column is the actual OPRA event capture timestamp,
NEVER CBBO-1m bucket start or the polling time of Webull snapshots.

Event schema must be cmbp-1 (and publisher_id 30 consolidated NBBO)
or tcbbo (NBBO at actual trade events, potentially sparse).
The importer discards invalid option quotes, unknown model-greeks,
out-of-session data and 0DTE mismatches. SPY relative volume uses 20
prior completed bars; annualized RV uses up to 30 completed minute
returns, with no synthetic defaults. Research Black-Scholes IV and
delta use the actual option bid/ask and latest completed SPY bar.
The research model is an approximation and not a real tradable fill.

Atomic paired-session CSVs are validated by the existing strict
loader and source file SHA256 values recorded in a provenance manifest.
The manifest explicitly warns CSV checksums do NOT prove market-data
authenticity or vendor licensing. Quote and bar event sources must be
verified independently.

The --compare flag invokes the frozen V1 / V2 / adaptive V2
comparison only on untouched later sessions. No live broker orders,
subscriptions, or historical purchases are initiated.

IMPORTANT BLOCKER: The current Railway environment has a Webull
SANDBOX/DELAYED collector and a Databento API key variable name.
Neither proves the account holds rights to live OPRA or purchased
high-granularity historical CMBP-1. Databento live OPRA is subscription
priced and historical event data can incur charges; this pipeline
will not silently purchase or relabel insufficient snapshots.
Until independently observed input data exists, the comparator
must report blocked rather than inventing positive performance.
