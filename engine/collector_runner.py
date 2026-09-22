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
from .providers.webull_free import WebullFreeDataProvider

EASTERN = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class CollectorRunnerSettings:
    db_path: Path = Path("/data/spy_0dte.sqlite")
    interval_seconds: int = 60
    idle_sleep_seconds: int = 300
    market_open: time = time(9, 45)
    market_close: time = time(16, 0)


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
            values.get("COLLECTOR_INTERVAL_SECONDS", "60"),
            name="COLLECTOR_INTERVAL_SECONDS",
        ),
        idle_sleep_seconds=_positive_int(
            values.get("COLLECTOR_IDLE_SLEEP_SECONDS", "300"),
            name="COLLECTOR_IDLE_SLEEP_SECONDS",
        ),
        market_open=_parse_hhmm(values.get("COLLECTOR_MARKET_OPEN_ET", "09:45")),
        market_close=_parse_hhmm(values.get("COLLECTOR_MARKET_CLOSE_ET", "16:00")),
    )


def is_collection_window(now: datetime, settings: CollectorRunnerSettings) -> bool:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    eastern = now.astimezone(EASTERN)
    if eastern.weekday() >= 5:
        return False
    clock = eastern.timetz().replace(tzinfo=None)
    return settings.market_open <= clock < settings.market_close


def _credentials_from_env() -> tuple[str, str]:
    app_key = os.environ.get("WEBULL_APP_KEY", "").strip()
    app_secret = os.environ.get("WEBULL_APP_SECRET", "").strip()
    missing = [
        name
        for name, value in (
            ("WEBULL_APP_KEY", app_key),
            ("WEBULL_APP_SECRET", app_secret),
        )
        if not value
    ]
    if missing:
        raise RuntimeError("missing required secret environment variables: " + ", ".join(missing))
    return app_key, app_secret


def run_forever() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    log = logging.getLogger("spy0dte.collector")

    settings = settings_from_env()
    app_key, app_secret = _credentials_from_env()

    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    provider = WebullFreeDataProvider(app_key=app_key, app_secret=app_secret)
    store = SnapshotStore(settings.db_path)

    log.info(
        "collector started source=webull_sandbox_delayed db=%s interval=%ss window=%s-%s ET",
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
                    log.info(
                        "collection complete trade_date=%s rows=%d total_rows=%d",
                        trade_date.isoformat(),
                        len(snapshots),
                        store.count(),
                    )
                except Exception:
                    # Do not terminate the service for a transient broker/data error.
                    # Secrets are never interpolated into this log message.
                    log.exception("collection cycle failed")
                sleep_seconds = settings.interval_seconds
            else:
                sleep_seconds = settings.idle_sleep_seconds

            time_module.sleep(sleep_seconds)
    finally:
        store.close()


def main() -> None:
    run_forever()


if __name__ == "__main__":
    main()
