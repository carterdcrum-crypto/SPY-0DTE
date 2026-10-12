# Explicitly budgeted Databento historical event acquisition

This optional independent component can fetch genuine historical SPY
one-minute OHLCV (EQUS.MINI) and OPRA CMBP-1 consolidated quote events,
then stage the data through the strict forward importer and compare
frozen V1 against V2 on new trading days.

Default: DOES NOT CALL THE VENDOR OR SPEND MONEY.

Command after explicit consent to the estimated data purchase:

    python -m engine.forward_databento_acquire \
      --day 2026-10-13 --execute --max-usd 1.00 \
      --raw-dir /data/research/forward_raw \
      --event-dir /data/research/forward_events \
      --legacy-dir /data/research \
      --output-dir /data/research/regime_v2_comparison

Do NOT run the command merely because a DATABENTO_API_KEY variable
is configured: Databento OPRA access is separately licensed, and
even historical pay-as-you-go API calls can incur charges. Only
--execute plus positive --max-usd enables data downloads. The
vendor's metadata cost estimates are checked for SPY OHLCV,
OPRA instrument definitions, then chosen OPRA CMBP-1 contracts.
The underlying+definition download may already incur their
estimated costs if the later option estimate exceeds the ceiling.
The spending limit is based on estimates, NOT a guaranteed vendor
billing cap. Confirm actual costs in the Databento portal.

Contract acquisition includes 0DTE SPY strikes across the actual
completed session's SPY close-price range plus a 1%/5-dollar
margin; it never selects contracts based on option future P&L,
but this strike range and a max-160-symbol guard may omit useful
contracts. If the source cannot provide explicit OCC mappings,
real event receive timestamps or a consolidated OPRA event,
it refuses to convert the feed.

The 500K event/140MB input caps are designed for small
Railway volumes. Large event days need streaming/object storage
rather than changing the record cap unconditionally.

No automatic purchases, real orders, Webull submission,
unverified backfill promotion or reoptimization are enabled.
Historical forward replay is after-session, not guaranteed
streaming intraday paper fills.
