from datetime import date, datetime, time, timezone

from engine.collector_runner import CollectorRunnerSettings, is_collection_window
from engine.session_calendar import session_bounds, session_close, session_status


def test_holidays_and_special_new_year_observance():
    assert session_close(date(2026, 11, 26)) is None
    assert session_close(date(2026, 7, 3)) is None
    assert session_close(date(2026, 4, 3)) is None
    assert session_close(date(2027, 12, 31)) == time(16)
    assert session_close(date(2028, 1, 3)) == time(16)


def test_early_close_reduces_exit_window_but_preserves_delayed_collection_tail():
    day = date(2026, 11, 27)
    assert session_bounds(day, time(9, 45), time(16))[1].hour == 13
    settings = CollectorRunnerSettings(market_close=time(16, 15))
    assert is_collection_window(datetime(2026, 11, 27, 18, 14, tzinfo=timezone.utc), settings)
    assert not is_collection_window(datetime(2026, 11, 27, 18, 15, tzinfo=timezone.utc), settings)


def test_next_session_handles_weekend_and_daylight_saving():
    status = session_status(datetime(2026, 10, 31, 20, tzinfo=timezone.utc), time(9, 45), time(16))
    assert status["next_session_at"] == "2026-11-02T09:45:00-05:00"


def test_unknown_calendar_year_fails_closed():
    assert session_close(date(2029, 1, 2)) is None
    status = session_status(datetime(2029, 1, 2, 15, tzinfo=timezone.utc), time(9, 45), time(16))
    assert status["state"] == "CALENDAR_UNAVAILABLE"
    assert status["next_session_at"] is None


def test_emergency_closures_can_be_added(monkeypatch):
    monkeypatch.setenv("MARKET_EXTRA_CLOSED_DATES", "2026-09-28")
    assert session_close(date(2026, 9, 28)) is None
