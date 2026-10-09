# SPY 0DTE: reproducible 15-minute ORB vs VWAP continuation

**Scope:** independent research program. No changes to the Android app, paper
account, Webull connection, live executor, or strategy auto-trade toggle.
This code **cannot submit broker orders**.

**Question:** Do executable same-day SPY options produce strong, repeatable
**ACCOUNT** growth after costs? Weekly **+100%** is a stringent hypothesis
to challenge, not a target used to fit thresholds and not a promise.

## Executed research audit (2026-10-08)

Existing Railway research storage was previously verified to contain 128
daily `spy_0dte_YYYY-MM-DD.csv.gz` files, whose data came from SPY
`EQUS.MINI ohlcv-1m` plus SPY option `OPRA.PILLAR cbbo-1m`.
The **existing canonical output does not preserve each full SPY
open/high/low/volume or option quote event-observation timestamps, sizes**.
Consequently, none of those files passes the strict adapter for a
genuine first-15-minute OHLC range and **event-time executable option fills**.
The CLI prints an explicit `DATA_BLOCKED` status. The Railway research-only audit on October 8 returned **128 legacy files, 0 genuine OHLCV input files, 0 event-time option-chain files, and 0 matched usable sessions**. Synthetic unit
test fills **must not** be misreported as historical performance.

We have **not** downloaded new licensed historical market data, incurred
data charges, opened the holdout, or claimed real profitability.
Data requirements must be satisfied before running a valid experiment.
Raw Databento 1-minute SPY OHLCV *may be available from an earlier vendor
purchase*, but the existing research storage has only **lossy canonical**
close/volume-ratio fields. Recreate real bar/quote data from authorized raw
records if available; **never invent high/low/volume, NBBO timestamps,
or option premiums**.

## Data adapter: exact input contract

Create per-session files (plain CSV or .gz) in one directory, for example
`/data/research/weekly_raw/`. Both formats MUST be genuine historical data.

**SPY 1-minute OHLCV bars**
`spy_bars_2026-10-08.csv`:
```csv
ts_start,open,high,low,close,volume
2026-10-08T09:30:00-04:00,701.01,701.08,700.95,701.04,125600
```
The row is shown **only as a schema example, not an actual market record**.
An OHLCV source timestamp is the **bar start**; it is observed only after
one full minute (9:31:00 ET in example). Values above must be replaced by
licensed, verified records. No backtester looks at the entire bar at 9:30.

**Actual event-observed SPY 0DTE NBBO chain quotes**
`spy_option_quotes_2026-10-08.csv`:
```csv
observed_at,symbol,expiration,right,strike,bid,ask,delta,bid_size,ask_size,volume,open_interest,schema
2026-10-08T09:31:04-04:00,SPY261008C00700000,2026-10-08,call,700,1.95,1.99,0.50,9,7,1200,34000,cmbp-1
```
This is **only a format example, not a true quote**. `observed_at` must
reflect *genuine event reception/availability*; minute/second-sampled
Databento `cbbo-1m` and `cbbo-1s` bucket `ts_event` timestamps
**cannot** be relabeled as quote observation times. Supported source
tags: `cmbp-1`, `tcbbo`, `nbbo-event` (or `test-fixture` in tests).
If using TCBBO/trade-time snapshots, preserve the observed quote
timestamp and ensure the associated quote was available *before* the
decision; no reconstructed forward-looking option NBBO.

The OCC symbol, explicit expiry date, right and strike **must agree**.
Only expiry equal to the actual current session date is eligible.
The requested actual 0DTE listing must have existed that morning:
do not backfill made-up contracts. `delta` must be an observed/as-of
quote-time Greek or reproducible contemporaneous IV calculation, **never a
later-day Greek**. Same-day `volume` and `open_interest` must be available
at the quote timestamp; if not, retain genuinely prior published OI or
zero, not end-of-day values. `bid_size/ask_size` are actual contemporaneous
displayed sizes. Verify NBBO feed licensing and coverage.

A source converter can export these CSVs from a licensed vendor; the
**strict CSV importer** lives in `engine.weekly_options_data`. No
API key or paid download is performed by this project.

## Frozen strategy definitions (exactly 9 configurations)

Each strategy uses the same opening range: all 15 **completed** bars from
**9:30:00 through 9:44:59 ET**, requiring positive volume and complete
coverage. The range is fixed after 9:45, and signals are generated at
the close of later, completed 1-minute bars.

1. `orb_simple`: enter a same-day CALL if current closed SPY bar finishes
   above the opening-range **high** and the prior completed close was at
   or below it; invert for a PUT breaking below the opening-range **low**.
   No VWAP or volume filter.
2. `orb_filtered`: same fresh breakout; price must align with the
   **intraday volume-weighted bar-typical-price VWAP proxy** on the
   correct side of it, and the completed bar's volume / **mean of the
   same minute-of-day across at least three previous sessions** must be
   >= **1.25**. No current or future session volume enters the reference.
   The bar proxy is NOT transaction-level VWAP; upgrade to actual
   underlying trade VWAP when tick-level data becomes available.
3. `vwap_reclaim`: above/below opening range and aligned with the
   appropriate 5-minute SPY price slope; the prior completed bar probes
   and closes beyond the VWAP proxy, and the current completed bar
   reclaims it with a confirming directional candle. The bar must be
   completed before signal generation.

All no-trade conditions include: incomplete opening 15 minutes, no
qualifying signal, missing/stale quote, too-wide spread, no eligible
0DTE contract, model delta outside .40–.60 absolute, invalid bid/ask,
quote size below 1 contract, open interest below 25, insufficient
settled cash or premium allocation, daily loss pause, and too close to
cutoff. Deterministic selection: minimum distance from 0.50 absolute
delta, then smaller relative spread, then strike nearer the **observed
SPY spot**, then lexical OCC symbol.

**Three per-order exposure values**, all strategies:
**0.5%, 1%, 2% of current account equity**, whole 100-share multiplier
contracts only. No naked shorts, averaging down, martingale or leverage.

**Cash-account accounting:** entries debit available settled cash;
exits create receivables and cash proceeds settle on the next NYSE
trading session. Fees are debited once per leg, and
`equity = initial_cash + cumulative_realized_pnl + unrealized_pnl`
must reconcile at each observed timestamp. Counts for signal order
attempts, actual buy/sell fills, canceled orders and *completed* round
trip trades are separate. Account return and option premium return
are separate fields. No option P&L comes from SPY candle direction.

**Execution:** choose the quote no older than **75 seconds** at signal
time; submit a quantity-capped simulated buy at the **first quote
observed after the completed signal timestamp plus 2 seconds**
(within 90 seconds); fill at ask plus **1 adverse option tick**.
Re-verify option liquidity, cash, and original debit limit on the
execution quote. No synthetic mid fills. Exit on observed option bid
at 35% adverse premium move or 65% favorable premium move, or after
20 minutes, whichever first triggers. Exit at the **next quote plus
latency**, bid minus 1 adverse tick. A stop signal is **not a
guaranteed fill**. No stops are assumed from ambiguous SPY bar
high/low. Unexecuted close orders are counted and the remaining 0DTE
premium is conservatively written down to **zero recovery**; this
scenario is labeled as missing exit liquidity, not a real bid fill.
Fee model: **$0.68 per contract per executed side**, covering
estimated commission + regulatory fee, then explicit doubled-fee stress.
Fee schedule is a research assumption; replace with actual broker costs
before any financial claim.

**Close policy:** stop opening new trades at least 3 min before
`exchange_close - 15 min`; force exit before that cutoff if a
real quote exists. Calendar uses `America/New_York`,
DST, NYSE holiday sessions and early close schedule from the existing
`engine.session_calendar`. A true 1 p.m. early-close session uses a
12:45 p.m. ET research cutoff. If the connected broker enforces an
**earlier** expiration deadline, override the cutoff or refuse trades.
Historical quote-stream stalls are marked stale, making option
intraday drawdown potentially conservative/unreliable. These are not
guaranteed executable stops at the cutoff.

## Commands

Requires Python 3.12+. No API credentials for offline CSVs.

```bash
python -m pip install -e '.[dev]'

# Does NOT purchase data. Explains missing source fields.
python -m engine.weekly_options_cli --mode audit \
  --data-dir /data/research

# Once complete genuine event-quote and bar files are supplied:
python -m engine.weekly_options_cli --mode development \
  --data-dir /data/research/weekly_raw \
  --output-dir research_results/weekly_options_run01 \
  --cash 10000 \
  --train-sessions 40 --validation-sessions 10 \
  --test-sessions 10 --holdout-sessions 20 --purge-sessions 1

# RUN EXACTLY ONCE after reviewing/fixing strategy parameters.
# Frozen source-code hash and first holdout access marker block
# retroactive redefinitions and repeated holdout tuning.
python -m engine.weekly_options_cli --mode holdout \
  --data-dir /data/research/weekly_raw \
  --output-dir research_results/weekly_options_run01 \
  --cash 10000

python -m pytest -q tests/test_weekly_options_experiment.py
```

The development stage writes `sealed_design.json` and
`development_all_candidates.csv` for **all nine tested configurations**
across expanding chronological walk-forward train/validation/test folds,
with one session purge gaps and an untouched final 20-session holdout.
The strategy ranking uses **validation-only risk-adjusted growth** with
a 3-trade minimum; **not** the +100% target and not outer test results.
There is no tuned threshold search. If insufficient data, the CLI
fails rather than shrinking windows/cherry-picking weeks.

The holdout stage is **blocked** before development seals parameters.
Once run, it writes `holdout_accessed.once` *before reading holdout
data* and refuses a second access with unchanged output directory.
It evaluates **all nine frozen configurations**, including losers and
no-trade cases. Only the champion selected on earlier validation receives
the predeclared execution stresses: **2 ticks adverse each side,
2x per-side fees, and 62-second entry latency**.
Reports include `holdout_all_candidates_and_stress.csv`,
`holdout_regimes.csv`, `holdout_bootstrap.json`, and individual
`*-weekly.csv`, `*-trades.csv`, `*-equity.csv` and
`*-intraday-marks.csv` complete account ledgers. All **eligible ISO
weeks**, including weeks with no trades, count as observations; sessions
missing from the verified calendar are a data-quality failure, not
silently excluded. Market regimes are descriptively classified by
underlying intraday range and return, **not used to reselect a winner**.

Weekly bootstrap is a fixed-seed **500-path circular moving-block
bootstrap with 2-week blocks**. Reports 95% geometric weekly return and
weekly path max-drawdown intervals, but cannot validate regime
stationarity or rare-tail risks from a small number of weeks.

**Hypothesis rejection:** do not affirm +100%-per-week with low
drawdown unless fresh holdout evidence supports it; the code treats
lack of evidence as **NOT_SUPPORTED**. Even seemingly strong positive
holdout results are preliminary, not a promised future return.
Smaller accounts (e.g. $300) often cannot buy one SPY near-.50-delta
0DTE option under a 0.5%, 1% or 2% premium cap; **zero trades is the
correct result**, not fractional options or synthetic fills.

## References

- Databento OHLCV: `ts_event` is **start** of bar:
  https://www.databento.tech/docs/schemas-and-data-formats/ohlcv
- Databento NBBO schemas / trade quotes: https://www.databento.tech/docs/schemas-and-data-formats
- Databento OPRA data: https://databento.com/docs/knowledge-base/datasets/opra-pillar
- NYSE market calendar and early closes:
  https://www.nyse.com/trade/hours-calendars

This report is updated once a complete, licensed dataset is
available and the **single** untouched holdout is evaluated.

### Input completeness and holdout caveats

A candidate training/validation/test/holdout session needs >=95% of
exchange-session OHLCV minute bars, event-time NBBO updates spanning
>=50% of session minutes, and opening/near-closing quote coverage. The
predeclared first/last exchange trading dates must be supplied on both
development and holdout commands. All verified open exchange sessions
within that interval must have both full input files; missing data
cannot be quietly dropped. Incomplete inputs **fail**, not count as
no-trade wins.

**Historical contamination caveat:** Earlier versions of this project
have already studied portions of April–October 2026. Any final interval
drawn from that known archive is *sealed for this particular new study*
but **not historically untouched by the broader project**. True
independent confirmation will require additional, later market data
collected after the entry rules and parameters are frozen. The CLI's
access-once guard is a reproducibility safeguard, not a guarantee that
another human or project never viewed the dates.

If genuine raw quotes are unavailable, no amount of parameter tuning
or synthetic SPY price conversion resolves this blocker. Performance
should remain **NOT EVALUABLE**, not zero return, until input fidelity
and coverage requirements are met.
