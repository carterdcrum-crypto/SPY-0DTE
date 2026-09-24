from __future__ import annotations

import logging
import os
import time as time_module
from dataclasses import dataclass
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Mapping
from zoneinfo import ZoneInfo

from .collector import SnapshotStore, collect_and_store
from .market_data_factory import provider_from_env, provider_source

EASTERN = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class CollectorRunnerSettings:
    db_path: Path = Path("/data/spy_0dte.sqlite")
    interval_seconds: int = 2
    idle_sleep_seconds: int = 300
    market_open: time = time(9, 45)
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
        market_open=_parse_hhmm(values.get("COLLECTOR_MARKET_OPEN_ET", "09:45")),
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
    if eastern.weekday() >= 5:
        return False
    clock = eastern.timetz().replace(tzinfo=None)
    return settings.market_open <= clock < settings.market_close


def _backoff_seconds(settings: CollectorRunnerSettings, consecutive_failures: int) -> int:
    if consecutive_failures <= 0:
        return settings.interval_seconds
    multiplier = 2 ** min(consecutive_failures, 6)
    return min(settings.maximum_backoff_seconds, settings.interval_seconds * multiplier)


def run_forever() -> None:
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
    consecutive_failures = 0

    log.info(
        "collector started source=%s db=%s interval=%ss window=%s-%s ET",
        source,
        settings.db_path,
        settings.interval_seconds,
        settings.market_open.strftime("%H:%M"),
        settings.market_close.strftime("%H:%M"),
    )

    try:
        while True:
            now = datetime.now(timezone.utc)
            if is_collection_window(now, settings):
                trade_date = now.astimezone(EASTERN).date()
                try:
                    snapshots = collect_and_store(
                        provider,
                        store,
                        trade_date,
                        observed_at=now,
                    )
                    consecutive_failures = 0
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
                    sleep_seconds = _backoff_seconds(settings, consecutive_failures)
                    log.warning(
                        "collection cycle failed source=%s type=%s retry_in=%ss failures=%d",
                        source,
                        type(exc).__name__,
                        sleep_seconds,
                        consecutive_failures,
                    )
            else:
                consecutive_failures = 0
                sleep_seconds = settings.idle_sleep_seconds

            time_module.sleep(sleep_seconds)
    finally:
        store.close()


def main() -> None:
    run_forever()


if __name__ == "__main__":
    main()
