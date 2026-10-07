from __future__ import annotations

import os
import time
from datetime import date, timedelta
from pathlib import Path

from .burst_research import run_burst_stress_directory
from .data import write_canonical_csv
from .frontier_research import pareto_frontier, run_regime_frontier
from .providers.databento_history import DatabentoHistoryProvider
from .regime_long_option import run_regime_stress_directory


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


def _is_transient_databento_error(exc: Exception) -> bool:
    module = type(exc).__module__
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    return (
        module.startswith("databento.")
        and not _is_no_data_error(exc)
        and (
            "servererror" in name
            or "timeout" in name
            or "connection" in name
            or "gateway timed out" in text
            or "temporarily unavailable" in text
            or text.startswith("502 ")
            or text.startswith("503 ")
            or text.startswith("504 ")
        )
    )


def _retry_databento(operation, *, label: str):
    attempts = max(1, int(os.getenv("RESEARCH_DATABENTO_RETRIES", "5")))
    base_delay = max(0.0, float(os.getenv("RESEARCH_DATABENTO_RETRY_DELAY_SECONDS", "3")))
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except Exception as exc:
            retryable = _is_transient_databento_error(exc)
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
            if _is_transient_databento_error(exc):
                print(
                    f"research backfill defer transient Databento failure: "
                    f"date={day} stage=estimate type={type(exc).__name__}: {exc}",
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
            if _is_transient_databento_error(exc):
                print(
                    f"research backfill defer transient Databento failure: "
                    f"date={day} stage=fetch type={type(exc).__name__}: {exc}",
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


def run_regime_validation() -> None:
    data_dir = _data_dir()
    if not any(data_dir.glob("spy_0dte_*.csv*")):
        raise RuntimeError("no canonical research files available for regime validation")

    balances = [
        float(value.strip())
        for value in os.getenv("RESEARCH_REGIME_BALANCES", "100,250,500,1000,10000").split(",")
        if value.strip()
    ]
    for starting_cash in balances:
        results = run_regime_stress_directory(
            data_dir,
            starting_cash=starting_cash,
            adverse_ticks=(0, 1, 2),
        )
        for stress in results:
            metrics = stress.result.metrics
            ratio = (
                metrics.total_return / metrics.max_drawdown
                if metrics.max_drawdown > 0
                else (float("inf") if metrics.total_return > 0 else 0.0)
            )
            print(
                "regime validation: "
                f"cash={starting_cash:.2f} ticks={stress.adverse_ticks_per_side} "
                f"trades={metrics.trades} ending={metrics.ending_equity:.2f} "
                f"return_pct={metrics.total_return * 100.0:.3f} "
                f"max_dd_pct={metrics.max_drawdown * 100.0:.3f} "
                f"return_dd={ratio:.4f} "
                f"win_pct={metrics.win_rate * 100.0:.2f} "
                f"pf={metrics.profit_factor:.4f} "
                f"avg_trade_pct={metrics.average_trade_return * 100.0:.3f}",
                flush=True,
            )


def run_frontier_validation() -> None:
    data_dir = _data_dir()
    if not any(data_dir.glob("spy_0dte_*.csv*")):
        raise RuntimeError("no canonical research files available for frontier validation")

    balances = [
        float(value.strip())
        for value in os.getenv("RESEARCH_FRONTIER_BALANCES", "500").split(",")
        if value.strip()
    ]
    budgets = [
        float(value.strip())
        for value in os.getenv("RESEARCH_FRONTIER_DD_BUDGETS", "0.05,0.10,0.15,0.20").split(",")
        if value.strip()
    ]
    for starting_cash in balances:
        points = run_regime_frontier(
            data_dir,
            starting_cash=starting_cash,
            drawdown_budgets=budgets,
        )
        for point in points:
            print(
                "frontier candidate: "
                f"cash={starting_cash:.2f} name={point.candidate} "
                f"dd_budget={point.drawdown_budget:.3f} accepted={int(point.accepted)} "
                f"score={point.score:.6f} "
                f"ret1_pct={point.one_tick_return * 100.0:.3f} "
                f"dd1_pct={point.one_tick_drawdown * 100.0:.3f} "
                f"ret2_pct={point.two_tick_return * 100.0:.3f} "
                f"dd2_pct={point.two_tick_drawdown * 100.0:.3f} "
                f"reason={point.reason or 'accepted'}",
                flush=True,
            )
        for point in pareto_frontier(points):
            print(
                "frontier pareto: "
                f"cash={starting_cash:.2f} name={point.candidate} "
                f"dd_budget={point.drawdown_budget:.3f} "
                f"ret1_pct={point.one_tick_return * 100.0:.3f} "
                f"dd1_pct={point.one_tick_drawdown * 100.0:.3f} "
                f"score={point.score:.6f}",
                flush=True,
            )


def main() -> None:
    mode = os.getenv("RESEARCH_MODE", "backfill").strip().lower()
    if mode == "burst":
        run_burst_validation()
    elif mode == "regime":
        run_regime_validation()
    elif mode == "frontier":
        run_frontier_validation()
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
