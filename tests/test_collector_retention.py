from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from engine.collector import SnapshotStore, StorageCapacityError

MIB = 1024 * 1024


def _row(store: SnapshotStore, session: str, tag: int) -> None:
    store.connection.execute(
        """INSERT INTO option_snapshots (
        timestamp, received_at, option_symbol, expiration, right, strike,
        bid, ask, underlying_price, minutes_to_expiry, volume, open_interest, source
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (f"{session}T15:30:00+00:00", f"{session}T15:30:00+00:00",
         f"SPY-C-{tag}", session, "C", 700, 1, 1.1, 700, 30, 100, 500, "test"),
    )


def _snapshot() -> SimpleNamespace:
    now = datetime(2026, 10, 23, 14, 30, tzinfo=timezone.utc)
    return SimpleNamespace(
        timestamp=now, received_at=now, timestamp_quality="observed",
        feed_delay_seconds=0.0, option_symbol="SPY-20261023-C", expiration=date(2026, 10, 23),
        right="C", strike=700, bid=1, ask=1.2, bid_size=50, ask_size=50,
        underlying_price=700, minutes_to_expiry=380, volume=100, open_interest=200,
        greeks=None, source="test", underlying_timestamp=now,
    )


def test_prune_keeps_only_five_newest_observed_sessions(tmp_path):
    ledger = tmp_path / "spy_live_autonomy.sqlite"
    ledger.write_bytes(b"do not alter account records")
    store = SnapshotStore(tmp_path / "collector.sqlite")
    days = ["2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15",
            "2026-10-16", "2026-10-19", "2026-10-20"]
    for i, day in enumerate(days):
        _row(store, day, i)
    store.connection.commit()

    result = store.prune_old_sessions(keep_sessions=5, batch_rows=1)
    actual = [r[0][:10] for r in store.connection.execute(
        "SELECT timestamp FROM option_snapshots ORDER BY timestamp"
    )]
    assert actual == days[-5:]
    assert result["removed_rows"] == 2
    assert result["complete"] is True
    assert store.prune_old_sessions()["removed_rows"] == 0
    assert ledger.read_bytes() == b"do not alter account records"
    store.close()


def test_under_five_days_keeps_history(tmp_path):
    store = SnapshotStore(tmp_path / "collector.sqlite")
    _row(store, "2026-10-21", 1)
    _row(store, "2026-10-22", 2)
    store.connection.commit()
    assert store.prune_old_sessions()["removed_rows"] == 0
    assert store.count() == 2
    store.close()


def test_reject_invalid_retention_arguments(tmp_path):
    store = SnapshotStore(tmp_path / "collector.sqlite")
    with pytest.raises(ValueError):
        store.prune_old_sessions(keep_sessions=0)
    with pytest.raises(ValueError):
        store.prune_old_sessions(batch_rows=0)
    store.close()


def test_low_disk_requires_reusable_pages_and_journal_reserve(tmp_path, monkeypatch):
    store = SnapshotStore(tmp_path / "collector.sqlite")
    monkeypatch.setattr(store, "_reusable_bytes", lambda: 20 * MIB)
    monkeypatch.setattr(
        "engine.collector.shutil.disk_usage", lambda _: SimpleNamespace(free=11 * MIB)
    )
    with pytest.raises(StorageCapacityError):
        store.insert([_snapshot()])

    monkeypatch.setattr(
        "engine.collector.shutil.disk_usage", lambda _: SimpleNamespace(free=13 * MIB)
    )
    monkeypatch.setattr(store, "_reusable_bytes", lambda: 0)
    with pytest.raises(StorageCapacityError):
        store.insert([_snapshot()])

    monkeypatch.setattr(store, "_reusable_bytes", lambda: 20 * MIB)
    assert store.insert([_snapshot()]) == 1
    assert store.count() == 1
    store.close()
