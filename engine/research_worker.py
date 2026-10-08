from __future__ import annotations

import os
import time
from datetime import date, timedelta
from pathlib import Path

from .burst_research import run_burst_stress_directory
from .data import write_canonical_csv
from .event_study import run_causal_event_study
from .event_alpha import EventAlphaConfig, run_event_alpha_stress_directory
from .rocket_vault import RocketVaultConfig, run_rocket_vault_stress_directory
from .adaptive_vault import AdaptiveVaultConfig, run_adaptive_vault_stress_directory
from .inverse_research import run_inverted_adaptive_comparison
from .frontier_research import pareto_frontier, run_regime_frontier
from .metrics import daily_account_metrics
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
    raw_start = os.getenv("RESEARCH_FRONTIER_START", "").strip()
    raw_end = os.getenv("RESEARCH_FRONTIER_END", "").strip()
    start_date = date.fromisoformat(raw_start) if raw_start else None
    end_date = date.fromisoformat(raw_end) if raw_end else None

    for starting_cash in balances:
        points = run_regime_frontier(
            data_dir,
            starting_cash=starting_cash,
            drawdown_budgets=budgets,
            start_date=start_date,
            end_date=end_date,
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


def run_event_study_validation() -> None:
    data_dir = _data_dir()
    if not any(data_dir.glob("spy_0dte_*.csv*")):
        raise RuntimeError("no canonical research files available for event study")

    raw_start = os.getenv("RESEARCH_EVENT_START", "").strip()
    raw_end = os.getenv("RESEARCH_EVENT_END", "").strip()
    start_date = date.fromisoformat(raw_start) if raw_start else None
    end_date = date.fromisoformat(raw_end) if raw_end else None

    summaries = run_causal_event_study(
        data_dir,
        start_date=start_date,
        end_date=end_date,
    )
    if not summaries:
        raise RuntimeError("event study produced no observations")

    for summary in summaries:
        print(
            "event study: "
            f"event={summary.event} direction={summary.direction} "
            f"horizon={summary.horizon_minutes} n={summary.observations} "
            f"mean_bps={summary.mean_bps:.3f} "
            f"median_bps={summary.median_bps:.3f} "
            f"win_pct={summary.win_rate * 100.0:.2f} "
            f"p10_bps={summary.p10_bps:.3f} "
            f"p90_bps={summary.p90_bps:.3f} "
            f"mean_to_p10={summary.mean_to_p10:.4f}",
            flush=True,
        )


def run_event_alpha_validation() -> None:
    data_dir = _data_dir()
    if not any(data_dir.glob("spy_0dte_*.csv*")):
        raise RuntimeError("no canonical research files available for event-alpha validation")

    raw_start = os.getenv("RESEARCH_EVENT_ALPHA_START", "").strip()
    raw_end = os.getenv("RESEARCH_EVENT_ALPHA_END", "").strip()
    raw_trade_start = os.getenv("RESEARCH_EVENT_ALPHA_TRADE_START", "").strip()
    start_date = date.fromisoformat(raw_start) if raw_start else None
    end_date = date.fromisoformat(raw_end) if raw_end else None
    trade_start_date = date.fromisoformat(raw_trade_start) if raw_trade_start else None
    balances = [
        float(value.strip())
        for value in os.getenv("RESEARCH_EVENT_ALPHA_BALANCES", "500,1000,10000").split(",")
        if value.strip()
    ]
    holds = [
        int(value.strip())
        for value in os.getenv("RESEARCH_EVENT_ALPHA_HOLDS", "15,30").split(",")
        if value.strip()
    ]

    for hold_minutes in holds:
        config = EventAlphaConfig(
            hold_minutes=hold_minutes,
            minimum_minutes_to_close=max(25.0, float(hold_minutes + 5)),
        )
        for starting_cash in balances:
            results = run_event_alpha_stress_directory(
                data_dir,
                starting_cash=starting_cash,
                config=config,
                adverse_ticks=(0, 1, 2),
                start_date=start_date,
                end_date=end_date,
                trade_start_date=trade_start_date,
            )
            for stress in results:
                metrics = stress.result.metrics
                ratio = (
                    metrics.total_return / metrics.max_drawdown
                    if metrics.max_drawdown > 0
                    else (float("inf") if metrics.total_return > 0 else 0.0)
                )
                print(
                    "event alpha: "
                    f"hold={hold_minutes} cash={starting_cash:.2f} "
                    f"ticks={stress.adverse_ticks_per_side} trades={metrics.trades} "
                    f"ending={metrics.ending_equity:.2f} "
                    f"return_pct={metrics.total_return * 100.0:.3f} "
                    f"max_dd_pct={metrics.max_drawdown * 100.0:.3f} "
                    f"return_dd={ratio:.4f} "
                    f"win_pct={metrics.win_rate * 100.0:.2f} "
                    f"pf={metrics.profit_factor:.4f} "
                    f"avg_trade_pct={metrics.average_trade_return * 100.0:.3f}",
                    flush=True,
                )
                daily_start = trade_start_date or start_date
                daily = daily_account_metrics(
                    stress.result.equity_curve,
                    start_date=daily_start,
                    end_date=end_date,
                )
                trades_per_day = metrics.trades / daily.days if daily.days else 0.0
                print(
                    "event alpha daily: "
                    f"hold={hold_minutes} cash={starting_cash:.2f} "
                    f"ticks={stress.adverse_ticks_per_side} days={daily.days} "
                    f"mean_pct={daily.mean_return * 100.0:.3f} "
                    f"median_pct={daily.median_return * 100.0:.3f} "
                    f"geo_pct={daily.geometric_return * 100.0:.3f} "
                    f"positive_pct={daily.positive_day_rate * 100.0:.2f} "
                    f"hit5_pct={daily.hit_5_rate * 100.0:.2f} "
                    f"hit10_pct={daily.hit_10_rate * 100.0:.2f} "
                    f"hit15_pct={daily.hit_15_rate * 100.0:.2f} "
                    f"hit20_pct={daily.hit_20_rate * 100.0:.2f} "
                    f"hit25_pct={daily.hit_25_rate * 100.0:.2f} "
                    f"best_pct={daily.best_day_return * 100.0:.3f} "
                    f"worst_pct={daily.worst_day_return * 100.0:.3f} "
                    f"cvar99_loss_pct={daily.daily_loss_cvar_99 * 100.0:.3f} "
                    f"trades_per_day={trades_per_day:.3f}",
                    flush=True,
                )



def run_rocket_vault_validation() -> None:
    """Research-only compare aggressive sizing and locked-profit reserve.

    Uses the same date-scoped historical quotes and warmup-only dates as
    event-alpha. A separate mode avoids activating changes in live execution.
    """
    data_dir = _data_dir()
    if not any(data_dir.glob("spy_0dte_*.csv*")):
        raise RuntimeError("no canonical research files available for rocket-vault validation")

    raw_start = os.getenv("RESEARCH_EVENT_ALPHA_START", "").strip()
    raw_end = os.getenv("RESEARCH_EVENT_ALPHA_END", "").strip()
    raw_trade_start = os.getenv("RESEARCH_EVENT_ALPHA_TRADE_START", "").strip()
    start_date = date.fromisoformat(raw_start) if raw_start else None
    end_date = date.fromisoformat(raw_end) if raw_end else None
    trade_start_date = date.fromisoformat(raw_trade_start) if raw_trade_start else None
    balances = [
        float(value.strip())
        for value in os.getenv("RESEARCH_ROCKET_VAULT_BALANCES", "300,1000").split(",")
        if value.strip()
    ]
    holds = [
        int(value.strip())
        for value in os.getenv("RESEARCH_ROCKET_VAULT_HOLDS", "15").split(",")
        if value.strip()
    ]
    vault = RocketVaultConfig()
    for hold in holds:
        event = EventAlphaConfig(
            hold_minutes=hold,
            minimum_minutes_to_close=max(25.0, float(hold + 5)),
        )
        for balance in balances:
            runs = run_rocket_vault_stress_directory(
                data_dir,
                starting_cash=balance,
                event_config=event,
                vault_config=vault,
                adverse_ticks=(0, 1, 2),
                start_date=start_date,
                end_date=end_date,
                trade_start_date=trade_start_date,
            )
            for stress in runs:
                metrics = stress.result.metrics
                daily = daily_account_metrics(
                    stress.result.equity_curve,
                    start_date=trade_start_date or start_date,
                    end_date=end_date,
                )
                print(
                    "rocket vault: "
                    f"hold={hold} cash={balance:.2f} ticks={stress.adverse_ticks_per_side} "
                    f"trades={metrics.trades} ending={metrics.ending_equity:.2f} "
                    f"return_pct={metrics.total_return * 100:.3f} "
                    f"max_dd_pct={metrics.max_drawdown * 100:.3f} "
                    f"win_pct={metrics.win_rate * 100:.2f} "
                    f"locked_profit={stress.locked_profit:.2f} "
                    f"realized_peak={stress.realized_high_watermark:.2f} "
                    f"daily_geo_pct={daily.geometric_return * 100:.3f} "
                    f"positive_days_pct={daily.positive_day_rate * 100:.2f} "
                    f"days={daily.days}", flush=True,
                )


def run_adaptive_vault_validation() -> None:
    """Run one predeclared adaptive-risk policy against date-scoped real quotes.

    This isolated mode never updates the live executor or paper account.
    It deliberately reuses the earlier 5-session evaluation dates by default.
    """
    data_dir = _data_dir()
    if not any(data_dir.glob("spy_0dte_*.csv*")):
        raise RuntimeError("no canonical research files available for adaptive-vault validation")
    # Override only when explicitly requesting a larger development window;
    # otherwise reuse Rocket Vault's existing warmup / trade date split.
    all_dates = os.getenv("RESEARCH_ADAPTIVE_ALL_DATES", "").lower() in {"true", "yes", "1"}
    if all_dates:
        raw_start = raw_end = raw_trade_start = ""
    else:
        raw_start = os.getenv("RESEARCH_ADAPTIVE_START", "").strip() or os.getenv("RESEARCH_EVENT_ALPHA_START", "").strip()
        raw_end = os.getenv("RESEARCH_ADAPTIVE_END", "").strip() or os.getenv("RESEARCH_EVENT_ALPHA_END", "").strip()
        raw_trade_start = (
            os.getenv("RESEARCH_ADAPTIVE_TRADE_START", "").strip()
            or os.getenv("RESEARCH_EVENT_ALPHA_TRADE_START", "").strip()
        )
    datasets = sorted(data_dir.glob("spy_0dte_*.csv*"))
    available_dates = sorted({item.name[9:19] for item in datasets})
    print(
        f"adaptive vault coverage: files={len(datasets)} "
        f"distinct_sessions={len(available_dates)} "
        f"earliest={available_dates[0] if available_dates else 'none'} "
        f"latest={available_dates[-1] if available_dates else 'none'} "
        f"all_dates={all_dates}", flush=True,
    )
    start_date = date.fromisoformat(raw_start) if raw_start else None
    end_date = date.fromisoformat(raw_end) if raw_end else None
    trade_start_date = date.fromisoformat(raw_trade_start) if raw_trade_start else None
    balances = [
        float(value.strip())
        for value in os.getenv("RESEARCH_ADAPTIVE_BALANCES", "300,1000").split(",")
        if value.strip()
    ]
    holds = [
        int(value.strip())
        for value in os.getenv("RESEARCH_ADAPTIVE_HOLDS", "15").split(",")
        if value.strip()
    ]
    adaptive = AdaptiveVaultConfig()
    for hold in holds:
        event = EventAlphaConfig(
            hold_minutes=hold,
            minimum_minutes_to_close=max(25.0, float(hold + 5)),
        )
        for balance in balances:
            runs = run_adaptive_vault_stress_directory(
                data_dir,
                starting_cash=balance,
                event_config=event,
                adaptive_config=adaptive,
                adverse_ticks=(0, 1, 2),
                start_date=start_date,
                end_date=end_date,
                trade_start_date=trade_start_date,
            )
            for stress in runs:
                metrics = stress.result.metrics
                daily = daily_account_metrics(
                    stress.result.equity_curve,
                    start_date=trade_start_date or start_date,
                    end_date=end_date,
                )
                ratio = (
                    metrics.total_return / metrics.max_drawdown
                    if metrics.max_drawdown > 0 else 0.0
                )
                print(
                    "adaptive vault: "
                    f"hold={hold} cash={balance:.2f} ticks={stress.adverse_ticks_per_side} "
                    f"trades={metrics.trades} ending={metrics.ending_equity:.2f} "
                    f"return_pct={metrics.total_return * 100:.3f} "
                    f"max_dd_pct={metrics.max_drawdown * 100:.3f} "
                    f"return_dd={ratio:.3f} win_pct={metrics.win_rate * 100:.2f} "
                    f"pf={metrics.profit_factor:.3f} "
                    f"locked_profit={stress.locked_profit:.2f} "
                    f"realized_peak={stress.realized_high_watermark:.2f} "
                    f"final_exposure_pct={stress.final_exposure_fraction * 100:.1f} "
                    f"loss_streak={stress.losing_streak} "
                    f"daily_geo_pct={daily.geometric_return * 100:.3f} "
                    f"positive_days_pct={daily.positive_day_rate * 100:.2f} "
                    f"days={daily.days}", flush=True,
                )



def run_inverse_validation() -> None:
    """Reverse actual CALL orders into matching PUT purchases on the same event.

    Frozen policy and dates mirror the 20-session adaptive vault stress run.
    This is a new independently re-simulated account, not arithmetic negation.
    """
    root = _data_dir()
    start = os.getenv("RESEARCH_ADAPTIVE_START", "2026-09-01").strip()
    end = os.getenv("RESEARCH_ADAPTIVE_END", "2026-10-06").strip()
    trade_start = os.getenv("RESEARCH_ADAPTIVE_TRADE_START", "2026-09-09").strip()
    day_start = date.fromisoformat(start) if start else None
    day_end = date.fromisoformat(end) if end else None
    trade_day = date.fromisoformat(trade_start) if trade_start else None
    balances = [float(s) for s in os.getenv("RESEARCH_ADAPTIVE_BALANCES","300,1000").split(",") if s.strip()]
    event = EventAlphaConfig(hold_minutes=15)
    for balance in balances:
        comparisons = run_inverted_adaptive_comparison(
            root, starting_cash=balance, event_config=event,
            adaptive_config=AdaptiveVaultConfig(), adverse_ticks=(0,1,2),
            start_date=day_start, end_date=day_end, trade_start_date=trade_day,
        )
        for comparison in comparisons:
            a=comparison.original.metrics
            b=comparison.inverted.metrics
            print(
                "call put inversion: "
                f"cash={balance:.2f} ticks={comparison.adverse_ticks_per_side} "
                f"original_ending={a.ending_equity:.2f} original_return_pct={a.total_return*100:.3f} "
                f"original_trades={a.trades} "
                f"inverted_ending={b.ending_equity:.2f} inverted_return_pct={b.total_return*100:.3f} "
                f"inverted_trades={b.trades} inverted_max_dd_pct={b.max_drawdown*100:.3f} "
                f"inverted_win_pct={b.win_rate*100:.3f} "
                f"skipped_unavailable_or_unaffordable_put_signals={comparison.inverse_entries_without_put}",
                flush=True,
            )


def main() -> None:
    mode = os.getenv("RESEARCH_MODE", "backfill").strip().lower()
    if mode == "inverse":
        run_inverse_validation()
    elif mode == "adaptivevault":
        run_adaptive_vault_validation()
    elif mode == "rocketvault":
        run_rocket_vault_validation()
    elif mode == "burst":
        run_burst_validation()
    elif mode == "eventstudy":
        run_event_study_validation()
    elif mode == "eventalpha":
        run_event_alpha_validation()
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
