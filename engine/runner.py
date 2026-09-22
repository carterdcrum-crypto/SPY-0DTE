from __future__ import annotations

import os
import signal
import sys
import time
from dataclasses import dataclass
from datetime import datetime, time as dt_time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .collector import SnapshotStore, collect_and_store
from .providers.webull_free import WebullFreeDataProvider

EASTERN = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class RunnerConfig:
    interval_seconds: int = 60
    database_path: str = "data/spy_0dte.sqlite3"
    market_open: dt_time = dt_time(9, 30)
    market_close: dt_time = dt_time(16, 0)

    @classmethod
    def from_env(cls) -> "RunnerConfig":
        interval = int(os.getenv("COLLECT_INTERVAL_SECONDS", "60"))
        if interval < 30:
            raise ValueError("COLLECT_INTERVAL_SECONDS must be at least 30")
        return cls(
            interval_seconds=interval,
            database_path=os.getenv("SNAPSHOT_DB_PATH", "data/spy_0dte.sqlite3"),
        )


def is_market_session(now: datetime, config: RunnerConfig) -> bool:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    eastern = now.astimezone(EASTERN)
    if eastern.weekday() >= 5:
        return False
    clock = eastern.timetz().replace(tzinfo=None)
    return config.market_open <= clock < config.market_close


def seconds_until_next_cycle(now: datetime, config: RunnerConfig) -> int:
    if is_market_session(now, config):
        return config.interval_seconds
    eastern = now.astimezone(EASTERN)
    return 300 if eastern.weekday() < 5 else 900


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"missing required environment variable: {name}")
    return value


def run() -> None:
    config = RunnerConfig.from_env()
    app_key = _required_env("WEBULL_APP_KEY")
    app_secret = _required_env("WEBULL_APP_SECRET")

    db_path = Path(config.database_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    provider = WebullFreeDataProvider(app_key=app_key, app_secret=app_secret)
    store = SnapshotStore(db_path)

    stopping = False

    def stop_handler(signum, frame):  # noqa: ANN001, ARG001
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)

    print(
        f"collector started mode=sandbox_delayed interval={config.interval_seconds}s "
        f"db={db_path}",
        flush=True,
    )

    try:
        while not stopping:
            now = datetime.now(timezone.utc)
            if is_market_session(now, config):
                trade_date = now.astimezone(EASTERN).date()
                try:
                    snapshots = collect_and_store(provider, store, trade_date, observed_at=now)
                    print(
                        f"snapshot_cycle ts={now.isoformat()} rows={len(snapshots)} total={store.count()}",
                        flush=True,
                    )
                except Exception as exc:  # keep collector alive; failures are visible in logs
                    print(
                        f"snapshot_cycle_error ts={now.isoformat()} type={type(exc).__name__} error={exc}",
                        file=sys.stderr,
                        flush=True,
                    )

            sleep_for = seconds_until_next_cycle(datetime.now(timezone.utc), config)
            deadline = time.monotonic() + sleep_for
            while not stopping and time.monotonic() < deadline:
                time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
    finally:
        store.close()
        print("collector stopped", flush=True)


if __name__ == "__main__":
    run()
