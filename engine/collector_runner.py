from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Mapping
from zoneinfo import ZoneInfo

from .collector import SnapshotStore, StorageCapacityError, collect_and_store
from .market_data_factory import provider_from_env, provider_source
from .runtime import WorkerContext
from .session_calendar import session_bounds

EASTERN = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class CollectorRunnerSettings:
    db_path: Path = Path("/data/spy_0dte.sqlite")
    interval_seconds: int = 2
    idle_sleep_seconds: int = 300
    market_open: time = time(9, 30)
    market_close: time = time(16, 0)
    maximum_backoff_seconds: int = 120


def _parse_hhmm(value: str) -> time:
    hour_text, minute_text = value.split(":", 1)
    hour = int(hour_text)
    minute = int(minute_text)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("time must be HH:MM")
    return time(hour, minute)


def _positive_int(value: str, *, name: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise ValueError(f"{name} must be positive")
    return parsed


def settings_from_env(env: Mapping[str, str] | None = None) -> CollectorRunnerSettings:
    values = os.environ if env is None else env
    return CollectorRunnerSettings(
        db_path=Path(values.get("COLLECTOR_DB_PATH", "/data/spy_0dte.sqlite")),
        interval_seconds=_positive_int(
            values.get("COLLECTOR_INTERVAL_SECONDS", "2"),
            name="COLLECTOR_INTERVAL_SECONDS",
        ),
        idle_sleep_seconds=_positive_int(
            values.get("COLLECTOR_IDLE_SLEEP_SECONDS", "300"),
            name="COLLECTOR_IDLE_SLEEP_SECONDS",
        ),
        market_open=_parse_hhmm(values.get("COLLECTOR_MARKET_OPEN_ET", "09:30")),
        market_close=_parse_hhmm(values.get("COLLECTOR_MARKET_CLOSE_ET", "16:00")),
        maximum_backoff_seconds=_positive_int(
            values.get("COLLECTOR_MAX_BACKOFF_SECONDS", "120"),
            name="COLLECTOR_MAX_BACKOFF_SECONDS",
        ),
    )


def is_collection_window(now: datetime, settings: CollectorRunnerSettings) -> bool:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    eastern = now.astimezone(EASTERN)
    bounds = session_bounds(eastern.date(), settings.market_open, settings.market_close, collection=True)
    return bounds is not None and bounds[0] <= eastern < bounds[1]


def _backoff_seconds(settings: CollectorRunnerSettings, consecutive_failures: int) -> int:
    if consecutive_failures <= 0:
        return settings.interval_seconds
    multiplier = 2 ** min(consecutive_failures, 6)
    return min(settings.maximum_backoff_seconds, settings.interval_seconds * multiplier)


def _safe_provider_error_fields(exc: Exception) -> str:
    """Expose numeric HTTP/provider codes without leaking SDK request details.

    Never log exception text, headers, URLs, response bodies, or credentials.
    SDKs use different names for these fields, so inspect only known scalar
    attributes and strictly constrain their printable form.
    """
    import re

    fields = []
    for name in ("status_code", "http_status", "http_status_code", "code", "error_code"):
        try:
            value = getattr(exc, name, None)
        except Exception:
            continue
        if isinstance(value, int) and not isinstance(value, bool):
            safe = str(value)
        elif isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,32}", value):
            safe = value
        else:
            continue
        fields.append(f"{name}={safe}")
    return " ".join(fields) if fields else "provider_code=unavailable"


def run_forever(context: WorkerContext | None = None) -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    log = logging.getLogger("spy0dte.collector")

    # Broker SDK internals can include signed request headers in exception logs.
    # Suppress them so credentials/signatures never spill into Railway logs.
    for logger_name in ("webull", "webull.core", "webull.core.client"):
        logging.getLogger(logger_name).setLevel(logging.CRITICAL)

    settings = settings_from_env()
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    provider = provider_from_env()
    source = provider_source(provider)
    store = SnapshotStore(settings.db_path)
    # Keep the broker-held contract in the collector's single production
    # quote batch even after SPY moves away from the entry strike.
    live_positions_store = None
    paper_positions_store = None
    if source == "webull_production":
        from .live_autonomy_store import LiveAutonomyStore, autonomy_db_path
        live_positions_store = LiveAutonomyStore(autonomy_db_path())
        # PAPER and LIVE subscribe through this ONE collector. Keep both
        # simulated and broker-held contracts quoted for reliable exits.
        try:
            from .paper_account import PaperAccountStore
            from .paper_autotrader import _paper_db_path, _paper_starting_cash
            paper_positions_store = PaperAccountStore(
                _paper_db_path(), default_starting_cash=_paper_starting_cash()
            )
        except (OSError, ValueError, RuntimeError) as exc:
            log.warning("paper exit-quote pinning unavailable type=%s", type(exc).__name__)
    consecutive_failures = 0
    last_error = None
    last_retention_date = None
    last_storage_reported_failures = -100

    log.info(
        "collector started source=%s db=%s interval=%ss window=%s-%s ET",
        source,
        settings.db_path,
        settings.interval_seconds,
        settings.market_open.strftime("%H:%M"),
        settings.market_close.strftime("%H:%M"),
    )

    context = context or WorkerContext()
    context.heartbeat()
    try:
        while not context.stop.is_set():
            now = datetime.now(timezone.utc)
            collecting = is_collection_window(now, settings)
            # Run once each calendar day outside the trading window, never
            # during a live quote cycle. Failed/incomplete cleanup retries
            # on the next idle poll without touching position/account ledgers.
            if not collecting and last_retention_date != now.astimezone(EASTERN).date():
                try:
                    # Quote history is disposable after the trading date:
                    # retain two recent sessions, never live/paper ledgers.
                    result = store.prune_old_sessions(keep_sessions=2)
                    log.info(
                        "collector retention removed_rows=%s kept_dates=%s reusable_bytes=%s complete=%s",
                        result["removed_rows"],
                        ",".join(result["retained_sessions"]),
                        result["reusable_bytes"],
                        result["complete"],
                    )
                    if result["complete"]:
                        last_retention_date = now.astimezone(EASTERN).date()
                except Exception as exc:
                    # Never let maintenance terminate the collector or expose
                    # provider credentials through exception messages.
                    log.warning("collector retention deferred type=%s", type(exc).__name__)
            if collecting:
                trade_date = now.astimezone(EASTERN).date()
                try:
                    if live_positions_store is not None:
                        actual_provider = getattr(provider, "provider", provider)
                        if hasattr(actual_provider, "pinned_symbols"):
                            pinned = {p["symbol"] for p in live_positions_store.positions()}
                            if paper_positions_store is not None:
                                try:
                                    pinned.update(p["symbol"] for p in paper_positions_store.positions())
                                except Exception as exc:
                                    log.warning("paper quote pinning deferred type=%s", type(exc).__name__)
                            actual_provider.pinned_symbols = pinned
                    snapshots = collect_and_store(
                        provider,
                        store,
                        trade_date,
                        observed_at=now,
                    )
                    consecutive_failures = 0
                    last_error = None
                    max_delay = max(
                        (float(item.feed_delay_seconds) for item in snapshots),
                        default=float("nan"),
                    )
                    log.info(
                        "collection complete trade_date=%s rows=%d total_rows=%d max_feed_delay=%.3fs",
                        trade_date.isoformat(),
                        len(snapshots),
                        store.count(),
                        max_delay,
                    )
                    sleep_seconds = settings.interval_seconds
                except Exception as exc:
                    # Transient rate/network failures slow the collector instead of
                    # hammering the provider or terminating the service.
                    consecutive_failures += 1
                    last_error = type(exc).__name__
                    sleep_seconds = _backoff_seconds(settings, consecutive_failures)
                    if isinstance(exc, StorageCapacityError):
                        # Emergency recovery only touches OLD option-quote
                        # snapshots. One short bounded pass per failure,
                        # never any paper/live accounts, broker positions,
                        # order journaling, credentials or current session.
                        # Reuse freed database pages when the volume is near
                        # capacity. Never force VACUUM on a near-full volume.
                        try:
                            recovered=store.prune_old_sessions(
                                keep_sessions=2, batch_rows=256, max_batches=24)
                            if recovered["removed_rows"]:
                                log.warning(
                                    "collector recovered old quote capacity removed_rows=%d "
                                    "reusable_mb=%.2f complete=%s",
                                    recovered["removed_rows"],
                                    recovered["reusable_bytes"]/(1024*1024),
                                    recovered["complete"])
                        except Exception as cleanup_exc:
                            log.warning("collector recovery deferred type=%s",
                                        type(cleanup_exc).__name__)
                    if last_error == "StorageCapacityError" and (
                        consecutive_failures == 1 or
                        consecutive_failures - last_storage_reported_failures >= 12
                    ):
                        # Disk telemetry is non-sensitive; no connection keys,
                        # broker positions, account IDs, or payloads.
                        import shutil
                        disk = shutil.disk_usage(settings.db_path.parent)
                        db = settings.db_path
                        wal = Path(str(db) + "-wal")
                        log.warning(
                            "collector disk pressure free_mb=%.2f total_mb=%.2f "
                            "db_mb=%.2f wal_mb=%.2f reusable_mb=%.2f",
                            disk.free / (1024*1024), disk.total/(1024*1024),
                            db.stat().st_size / (1024*1024) if db.exists() else 0,
                            wal.stat().st_size / (1024*1024) if wal.exists() else 0,
                            store._reusable_bytes() / (1024*1024),
                        )
                        last_storage_reported_failures=consecutive_failures
                    log.warning(
                        "collection cycle failed source=%s type=%s %s retry_in=%ss failures=%d",
                        source,
                        type(exc).__name__,
                        _safe_provider_error_fields(exc),
                        sleep_seconds,
                        consecutive_failures,
                    )
            else:
                consecutive_failures = 0
                last_error = None
                sleep_seconds = settings.idle_sleep_seconds

            context.heartbeat(
                state="RECOVERING" if consecutive_failures else (
                    "RUNNING" if is_collection_window(now, settings) else "IDLE"
                ),
                collection_failures=consecutive_failures,
                last_error=last_error,
                retry_in_seconds=sleep_seconds if consecutive_failures else None,
            )
            context.wait(sleep_seconds)
    finally:
        store.close()


def main() -> None:
    run_forever()


if __name__ == "__main__":
    main()
