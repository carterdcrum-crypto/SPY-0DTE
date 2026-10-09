from __future__ import annotations

import os
import time
from datetime import date, timedelta
from pathlib import Path

from .burst_research import run_burst_stress_directory
from .data import write_canonical_csv
from .providers.databento_history import DatabentoHistoryProvider


def _data_dir() -> Path:
    output_dir = Path(os.getenv("RESEARCH_DATA_DIR", "/data/research"))
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def _output_path(trade_date: date) -> Path:
    return _data_dir() / f"spy_0dte_{trade_date.isoformat()}.csv.gz"


def _is_no_data_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return (
        "symbology_invalid_request" in text
        or "none of the symbols could be resolved" in text
        or "no data found" in text
    )


def _retry_databento(operation, *, label: str):
    attempts = max(1, int(os.getenv("RESEARCH_DATABENTO_RETRIES", "5")))
    base_delay = max(0.0, float(os.getenv("RESEARCH_DATABENTO_RETRY_DELAY_SECONDS", "3")))
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except Exception as exc:
            module = type(exc).__module__
            name = type(exc).__name__
            text = str(exc).lower()
            retryable = (
                module.startswith("databento.")
                and not _is_no_data_error(exc)
                and (
                    "servererror" in name.lower()
                    or "timeout" in name.lower()
                    or "connection" in name.lower()
                    or "gateway timed out" in text
                    or "temporarily unavailable" in text
                )
            )
            if not retryable or attempt >= attempts:
                raise
            delay = min(30.0, base_delay * attempt)
            print(
                f"Databento transient error: label={label} attempt={attempt}/{attempts} "
                f"type={type(exc).__name__} retry_in_seconds={delay:.1f}: {exc}",
                flush=True,
            )
            time.sleep(delay)


def run_smoke_backfill() -> Path:
    raw_date = os.getenv("RESEARCH_SMOKE_DATE")
    if not raw_date:
        raise RuntimeError("RESEARCH_SMOKE_DATE is required")
    trade_date = date.fromisoformat(raw_date)
    output = _output_path(trade_date)

    provider = DatabentoHistoryProvider()
    frames = _retry_databento(
        lambda: provider.fetch_day(trade_date),
        label=f"fetch_day:{trade_date}",
    )
    rows = write_canonical_csv(frames, output)
    size_mb = output.stat().st_size / (1024 * 1024)
    print(
        f"research smoke complete: date={trade_date.isoformat()} "
        f"frames={len(frames)} rows={rows} size_mb={size_mb:.3f} output={output}",
        flush=True,
    )
    if not frames or rows == 0:
        raise RuntimeError("Databento smoke fetch returned no usable SPY 0DTE quotes")
    return output


def run_range_backfill() -> tuple[Path, ...]:
    raw_start = os.getenv("RESEARCH_RANGE_START", "").strip()
    raw_end = os.getenv("RESEARCH_RANGE_END", "").strip()
    if not raw_start or not raw_end:
        raise RuntimeError("RESEARCH_RANGE_START and RESEARCH_RANGE_END are required")

    start = date.fromisoformat(raw_start)
    end = date.fromisoformat(raw_end)
    if end < start:
        raise RuntimeError("RESEARCH_RANGE_END must be on or after RESEARCH_RANGE_START")

    max_days = int(os.getenv("RESEARCH_MAX_DAYS", "10"))
    max_cost = float(os.getenv("RESEARCH_MAX_ESTIMATED_COST_USD", "5.00"))
    if max_days < 1 or max_cost <= 0:
        raise RuntimeError("research backfill limits must be positive")

    provider = DatabentoHistoryProvider()
    completed: list[Path] = []
    estimated_cost = 0.0
    attempted_days = 0
    day = start

    while day <= end and attempted_days < max_days:
        if day.weekday() >= 5:
            day += timedelta(days=1)
            continue

        output = _output_path(day)
        overwrite = os.getenv("RESEARCH_OVERWRITE", "false").lower() in {"1", "true", "yes"}
        if output.exists() and output.stat().st_size > 0 and not overwrite:
            print(f"research backfill skip existing: date={day} output={output}", flush=True)
            completed.append(output)
            day += timedelta(days=1)
            continue

        try:
            estimate = _retry_databento(
                lambda: provider.estimate_day_cost(day),
                label=f"estimate_day_cost:{day}",
            )
        except Exception as exc:
            if _is_no_data_error(exc):
                print(
                    f"research backfill skip no market data: date={day} reason={type(exc).__name__}: {exc}",
                    flush=True,
                )
                day += timedelta(days=1)
                continue
            raise
        projected = estimated_cost + estimate.total_cost_usd
        print(
            f"research cost estimate: date={day} symbols={estimate.option_symbols} "
            f"underlying_usd={estimate.underlying_cost_usd:.4f} "
            f"definitions_usd={estimate.definition_cost_usd:.4f} "
            f"options_usd={estimate.option_cost_usd:.4f} "
            f"day_total_usd={estimate.total_cost_usd:.4f} "
            f"projected_total_usd={projected:.4f}",
            flush=True,
        )
        if projected > max_cost:
            print(
                f"research budget stop: projected_usd={projected:.4f} "
                f"limit_usd={max_cost:.4f} before date={day}",
                flush=True,
            )
            break

        estimated_cost = projected
        attempted_days += 1
        try:
            frames = _retry_databento(
                lambda: provider.fetch_day(day),
                label=f"fetch_day:{day}",
            )
        except Exception as exc:
            if _is_no_data_error(exc):
                print(
                    f"research backfill skip no market data: date={day} reason={type(exc).__name__}: {exc}",
                    flush=True,
                )
                day += timedelta(days=1)
                continue
            raise
        if not frames:
            print(f"research backfill no data: date={day}", flush=True)
            day += timedelta(days=1)
            continue

        rows = write_canonical_csv(frames, output)
        size_mb = output.stat().st_size / (1024 * 1024)
        print(
            f"research backfill day complete: date={day} frames={len(frames)} "
            f"rows={rows} size_mb={size_mb:.3f} output={output}",
            flush=True,
        )
        if rows > 0:
            completed.append(output)
        day += timedelta(days=1)

    print(
        f"research range complete: files={len(completed)} attempted_days={attempted_days} "
        f"estimated_cost_usd={estimated_cost:.4f} start={start} end={end}",
        flush=True,
    )
    if not completed:
        raise RuntimeError("research range backfill produced no usable files")
    return tuple(completed)


def run_burst_validation() -> None:
    data_dir = _data_dir()
    if not any(data_dir.glob("spy_0dte_*.csv*")):
        raise RuntimeError("no canonical research files available for burst validation")

    balances = [
        float(value.strip())
        for value in os.getenv("RESEARCH_BURST_BALANCES", "100,250,500,1000,10000").split(",")
        if value.strip()
    ]
    for starting_cash in balances:
        results = run_burst_stress_directory(
            data_dir,
            starting_cash=starting_cash,
            adverse_ticks=(0, 1, 2),
        )
        for stress in results:
            metrics = stress.result.metrics
            print(
                "burst validation: "
                f"cash={starting_cash:.2f} ticks={stress.adverse_ticks_per_side} "
                f"trades={metrics.trades} ending={metrics.ending_equity:.2f} "
                f"return_pct={metrics.total_return * 100.0:.3f} "
                f"win_pct={metrics.win_rate * 100.0:.2f} "
                f"pf={metrics.profit_factor:.4f} "
                f"avg_trade_pct={metrics.average_trade_return * 100.0:.3f} "
                f"max_dd_pct={metrics.max_drawdown * 100.0:.3f}",
                flush=True,
            )



def run_weekly_cost_estimate() -> None:
    """Metadata-only quote / OHLCV price estimate. NEVER download billable data."""
    from datetime import datetime, time
    from zoneinfo import ZoneInfo
    try:
        import databento as db
    except ImportError:
        print("WEEKLY COST: DATABENTO_SDK_UNAVAILABLE", flush=True)
        return
    day = date.fromisoformat(os.getenv("RESEARCH_COST_DAY", "2026-10-06"))
    start = datetime.combine(day,time(9,30),ZoneInfo("America/New_York")).isoformat()
    end = datetime.combine(day,time(16,0),ZoneInfo("America/New_York")).isoformat()
    client=db.Historical()
    for dataset,schema,symbols,stype in (
        ("EQUS.MINI","ohlcv-1m",["SPY"],None),
        ("OPRA.PILLAR","cmbp-1","SPY.OPT","parent"),
        ("OPRA.PILLAR","cbbo-1s","SPY.OPT","parent"),
        ("OPRA.PILLAR","tcbbo","SPY.OPT","parent"),
        ("OPRA.PILLAR","statistics","SPY.OPT","parent"),
    ):
        label=f"{dataset} {schema}"
        try:
            arguments={"dataset":dataset,"schema":schema,
                       "symbols":symbols,"start":start,"end":end}
            if stype is not None:
                arguments["stype_in"]=stype
            usd=float(client.metadata.get_cost(**arguments))
            print(f"WEEKLY COST: date={day} dataset={dataset} schema={schema} estimated_usd={usd:.4f} metadata_only=true",flush=True)
        except Exception as exc:
            # Avoid logging request parameters or credentials.
            print(f"WEEKLY COST: date={day} label={label} unavailable error_type={type(exc).__name__} metadata_only=true",flush=True)



def run_weekly_feasibility() -> None:
    """Non-P&L buying-power feasibility on EXISTING sampled option NBBO.

    This is explicitly NOT an execution backtest, ORB validation, or an
    option premium return estimate. It just counts market quote snapshots
    where one near-half-delta 0DTE contract might fit the specified budget.
    """
    from collections import Counter
    from zoneinfo import ZoneInfo
    from .burst_research import iter_research_directory
    et=ZoneInfo("America/New_York")
    account_sizes=(300.,1000.,10000.)
    rates=(.005,.01,.02,.25)
    counts=Counter()
    observed_days=set()
    days_with_any=Counter()
    total_snapshots=0
    quote_counts=0
    skipped_market=0
    for frame in iter_research_directory(_data_dir()):
        local=frame.timestamp.astimezone(et)
        if not (local.hour*60+local.minute>=9*60+45 and
                local.hour*60+local.minute<15*60+40):
            continue
        # Treat only snapshots of original archived option symbols, whose
        # expirations were checked during original dataset construction.
        observed_days.add(local.date())
        total_snapshots+=1
        allowed=[
            q for q in frame.options
            if .4<=abs(q.delta)<=.6 and q.ask>q.bid>0
            and (q.ask-q.bid)/((q.ask+q.bid)/2)<=.15
            # Legacy CBBO sampled records often omit actionable quote size,
            # time-causal OI and traded volume. This is a PRICE affordability
            # study ONLY; do not assert executable liquidity from this count.
        ]
        quote_counts+=len(allowed)
        for cash in account_sizes:
            for fraction in rates:
                key=(cash,fraction)
                # Conservative one adverse cent, $.68 per executed entry.
                budget=cash*fraction
                if any(q.ask*100+1+.68<=budget+1e-9 for q in allowed):
                    counts[key]+=1
                    days_with_any[(cash,fraction,local.date())]+=1
    print(f"WEEKLY AFFORDABILITY: sampled_sessions={len(observed_days)} "
          f"observed_option_quote_frames={total_snapshots} "
          f"half_delta_liquid_quote_occurrences={quote_counts} "
          f"no_profit_returns_computed=true quote_size_and_oi_not_verified=true",flush=True)
    for cash in account_sizes:
        for fraction in rates:
            n=counts[(cash,fraction)]
            days=sum(days_with_any[(cash,fraction,d)]>0 for d in observed_days)
            print("WEEKLY AFFORDABILITY: "
                  f"cash={cash:.2f} risk_fraction={fraction:.4f} "
                  f"eligible_snapshots={n} of={total_snapshots} "
                  f"eligible_sessions={days} of={len(observed_days)} "
                  f"uses_sampled_1m_quotes_not_executable=true",flush=True)



def run_weekly_narrow_cost_estimate() -> None:
    """No-charge metadata estimate of true OPRA event quotes for small strike universes.

    Build the list from already-stored prior quote symbols; only use Databento
    metadata.get_cost. Never request actual historical data or purchase credits.
    """
    from datetime import datetime, time
    from zoneinfo import ZoneInfo
    from .burst_research import iter_research_directory
    import databento as db
    day=date.fromisoformat(os.getenv("RESEARCH_COST_DAY","2026-10-06"))
    frame=None
    from .data import load_canonical_path
    for item in load_canonical_path(_output_path(day)):
        local=item.timestamp.astimezone(ZoneInfo("America/New_York"))
        if local.hour>9 or (local.hour==9 and local.minute>=45):
            frame=item
            break
    if frame is None:
        print(f"WEEKLY NARROW: date={day} blocker=no existing quote symbols",flush=True)
        return
    reference=frame.market.spot
    # Existing symbols provide historically listed OCC contracts. This is a
    # universe COST ESTIMATE, not a trade-date filtered selection algorithm.
    options=[q for q in frame.options if abs(abs(q.delta)-.5)<=.30
             and q.right in ("call","put") and abs(q.strike-reference)<=15]
    options=sorted(options,key=lambda q:(abs(q.strike-reference),q.right,q.symbol))
    start=datetime.combine(day,time(9,30),ZoneInfo("America/New_York")).isoformat()
    end=datetime.combine(day,time(16),ZoneInfo("America/New_York")).isoformat()
    client=db.Historical()
    for limit in (12,24,48):
        selected=options[:limit]
        symbols=[]
        for q in selected:
            sym=q.symbol.replace(" ","")
            if sym.startswith("SPY") and len(sym)>3:
                # OPRA uses 6-character padded OCC root.
                sym="SPY".ljust(6)+sym[3:]
            symbols.append(sym)
        if not symbols:
            continue
        try:
            cost=float(client.metadata.get_cost(
                dataset="OPRA.PILLAR",symbols=symbols,stype_in="raw_symbol",
                schema="cmbp-1",start=start,end=end))
            print(f"WEEKLY NARROW: date={day} first_quote_spot={reference:.2f} "
                  f"listed_symbols={len(symbols)} max_universe={limit} "
                  f"cmbp1_usd={cost:.4f} metadata_only=true",flush=True)
        except Exception as exc:
            print(f"WEEKLY NARROW: date={day} listed_symbols={len(symbols)} "
                  f"cost_unavailable_type={type(exc).__name__}",flush=True)


def main() -> None:
    mode = os.getenv("RESEARCH_MODE", "backfill").strip().lower()
    if mode == "macdrsisma":
        from .macd_rsi_sma_tape_experiment import main as indicator_main
        args=[
            "--data-dir",str(_data_dir()),
            "--output-dir",os.getenv("RESEARCH_INDICATOR_OUTPUT",
                str(_data_dir()/"indicator_tape_fusion")),
        ]
        existing_tape=os.getenv("RESEARCH_VERIFIED_SPY_TAPE_CSV","").strip()
        if existing_tape:
            args.extend(["--verified-spy-tape-csv",existing_tape])
        raise SystemExit(indicator_main(args))
    elif mode == "jointprobability":
        from .joint_probability_audit import main as joint_main
        raise SystemExit(joint_main([
            "--data-dir",str(_data_dir()),
            "--output-dir",os.getenv("RESEARCH_JOINT_OUTPUT",
                str(_data_dir()/"joint_probability")),
        ]))
    elif mode == "magnitudeedge":
        from .magnitude_edge_study import main as magnitude_main
        raise SystemExit(magnitude_main([
            "--data-dir",str(_data_dir()),
            "--output-dir",os.getenv("RESEARCH_MAGNITUDE_OUTPUT",
                str(_data_dir()/"magnitude_edge")),
        ]))
    elif mode == "continuationregimegate":
        from .continuation_regime_gate import main as run_regime
        raise SystemExit(run_regime([
            "--data-dir",str(_data_dir()),
            "--output-dir",os.getenv("RESEARCH_REGIME_GATE_OUTPUT",
                str(_data_dir()/"continuation_regime_gate")),
        ]))
    elif mode == "causalrouter":
        from .causal_breakout_router import main as router_main
        raise SystemExit(router_main([
            "--data-dir",str(_data_dir()),
            "--output-dir",os.getenv("RESEARCH_CAUSAL_ROUTER_OUTPUT",
                str(_data_dir()/"causal_breakout_router")),
        ]))
    elif mode == "breakoutfailures":
        from .breakout_failure_audit import main as run_breakout
        raise SystemExit(run_breakout([
            "--data-dir",str(_data_dir()),
            "--output-dir",os.getenv(
                "RESEARCH_BREAKOUT_AUDIT_OUTPUT",
                str(_data_dir()/"breakout_failure_audit")),
        ]))
    elif mode == "freesignals":
        from .free_signal_screen import main as run_free
        raise SystemExit(run_free(["--data-dir",str(_data_dir()),
            "--output-dir",os.getenv("RESEARCH_FREE_SIGNAL_OUTPUT",str(_data_dir()/"free_signal_screen"))]))
    elif mode == "weeklydiagnostics":
        run_weekly_narrow_cost_estimate()
        run_weekly_feasibility()
    elif mode == "weeklynarrowcost":
        run_weekly_narrow_cost_estimate()
    elif mode == "weeklyfeasibility":
        run_weekly_feasibility()
    elif mode == "weeklycost":
        run_weekly_cost_estimate()
    elif mode == "weeklyoptionsaudit":
        import json
        from .weekly_options_data import audit
        print("WEEKLY OPTIONS DATA AUDIT: " + json.dumps(audit(_data_dir()),sort_keys=True),flush=True)
    elif mode == "burst":
        run_burst_validation()
    elif mode == "backfill":
        if os.getenv("RESEARCH_RANGE_START", "").strip():
            run_range_backfill()
        else:
            run_smoke_backfill()
    else:
        raise RuntimeError(f"unsupported RESEARCH_MODE: {mode}")

    if os.getenv("RESEARCH_KEEP_ALIVE", "false").lower() in {"1", "true", "yes"}:
        print("research worker idle; dataset persisted", flush=True)
        while True:
            time.sleep(3600)


if __name__ == "__main__":
    main()
