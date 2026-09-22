from datetime import datetime, timezone

import pytest

from engine.collector_runner import (
    CollectorRunnerSettings,
    _parse_hhmm,
    is_collection_window,
    settings_from_env,
)


def test_collection_window_uses_eastern_market_time():
    settings = CollectorRunnerSettings()
    # 2026-09-22 is a Tuesday; 14:00 UTC is 10:00 ET during daylight time.
    assert is_collection_window(datetime(2026, 9, 22, 14, 0, tzinfo=timezone.utc), settings)
    # 13:44 UTC is 09:44 ET, one minute before the delayed-data window opens.
    assert not is_collection_window(datetime(2026, 9, 22, 13, 44, tzinfo=timezone.utc), settings)
    # Saturday must never collect.
    assert not is_collection_window(datetime(2026, 9, 26, 14, 0, tzinfo=timezone.utc), settings)


def test_settings_are_configurable_without_credentials():
    settings = settings_from_env(
        {
            "COLLECTOR_DB_PATH": "/tmp/test.sqlite",
            "COLLECTOR_INTERVAL_SECONDS": "120",
            "COLLECTOR_IDLE_SLEEP_SECONDS": "600",
            "COLLECTOR_MARKET_OPEN_ET": "10:00",
            "COLLECTOR_MARKET_CLOSE_ET": "15:30",
        }
    )
    assert str(settings.db_path) == "/tmp/test.sqlite"
    assert settings.interval_seconds == 120
    assert settings.idle_sleep_seconds == 600
    assert settings.market_open == _parse_hhmm("10:00")
    assert settings.market_close == _parse_hhmm("15:30")


def test_invalid_runner_values_fail_closed():
    with pytest.raises(ValueError):
        settings_from_env({"COLLECTOR_INTERVAL_SECONDS": "0"})
    with pytest.raises(ValueError):
        _parse_hhmm("25:00")
