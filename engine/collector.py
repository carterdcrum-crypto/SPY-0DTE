from __future__ import annotations

import csv
import shutil
import sqlite3
from datetime import date
from pathlib import Path
from typing import Iterable, Tuple

from .providers.webull_free import FreeOptionSnapshot


SCHEMA = """
CREATE TABLE IF NOT EXISTS option_snapshots (
    timestamp TEXT NOT NULL,
    received_at TEXT,
    timestamp_quality TEXT,
    feed_delay_seconds REAL,
    option_symbol TEXT NOT NULL,
    expiration TEXT NOT NULL,
    right TEXT NOT NULL,
    strike REAL NOT NULL,
    bid REAL NOT NULL,
    ask REAL NOT NULL,
    bid_size INTEGER NOT NULL DEFAULT 0,
    ask_size INTEGER NOT NULL DEFAULT 0,
    underlying_price REAL NOT NULL,
    minutes_to_expiry REAL NOT NULL,
    volume INTEGER NOT NULL,
    open_interest INTEGER NOT NULL,
    implied_volatility REAL,
    delta REAL,
    gamma REAL,
    theta REAL,
    vega REAL,
    greek_model TEXT,
    source TEXT NOT NULL,
    underlying_timestamp TEXT,
    PRIMARY KEY (timestamp, option_symbol)
);
"""


_MIGRATIONS = {
    "received_at": "TEXT",
    "timestamp_quality": "TEXT",
    "feed_delay_seconds": "REAL",
    "bid_size": "INTEGER NOT NULL DEFAULT 0",
    "ask_size": "INTEGER NOT NULL DEFAULT 0",
    "underlying_timestamp": "TEXT",
}


class StorageCapacityError(OSError):
    """History collection paused to preserve space for execution journals."""


class SnapshotStore:
    """Small zero-cost SQLite store for our self-built options dataset."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self.connection = sqlite3.connect(self.path)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute(SCHEMA)
        self._migrate_if_needed()
        # The LIVE reader accesses the newest complete collector cycle every tick.
        # Indexing receive time avoids a full historical scan on each read.
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_option_snapshots_received_at ON option_snapshots(received_at)"
        )
        self.connection.commit()

    def _migrate_if_needed(self) -> None:
        existing = {
            str(row[1])
            for row in self.connection.execute("PRAGMA table_info(option_snapshots)").fetchall()
        }
        for column, definition in _MIGRATIONS.items():
            if column not in existing:
                self.connection.execute(
                    f"ALTER TABLE option_snapshots ADD COLUMN {column} {definition}"
                )

    def close(self) -> None:
        self.connection.close()

    def _reusable_bytes(self) -> int:
        """SQLite freelist pages can hold new snapshots even before VACUUM."""
        page_size = int(self.connection.execute("PRAGMA page_size").fetchone()[0])
        free_pages = int(self.connection.execute("PRAGMA freelist_count").fetchone()[0])
        return page_size * free_pages

    def prune_old_sessions(self, *, keep_sessions: int = 5, batch_rows: int = 256) -> dict:
        """Keep the five newest observed quote sessions; never touch account ledgers.

        Small commits and WAL truncation avoid a full-size VACUUM scratch copy on
        a nearly full Railway volume. SQLite may retain freed pages for reuse.
        This must be called by the collector only outside market hours.
        """
        if keep_sessions < 1 or batch_rows < 1:
            raise ValueError("retention limits must be positive")
        sessions = [
            str(row[0])
            for row in self.connection.execute(
                "SELECT DISTINCT substr(timestamp, 1, 10) AS session "
                "FROM option_snapshots ORDER BY session DESC LIMIT ?",
                (keep_sessions + 1,),
            )
        ]
        if len(sessions) <= keep_sessions:
            return {"removed_rows": 0, "retained_sessions": sessions, "complete": True,
                    "reusable_bytes": self._reusable_bytes()}

        oldest_retained = sessions[keep_sessions - 1]
        cutoff = oldest_retained + "T"
        removed = 0
        complete = True
        while True:
            # Each batch can create a WAL file before checkpointing. Reserve
            # sufficient physical space for journals shared with this volume.
            if self.path != ":memory:" and shutil.disk_usage(Path(self.path).parent).free < 12 * 1024 * 1024:
                complete = False
                break
            cursor = self.connection.execute(
                "DELETE FROM option_snapshots WHERE rowid IN "
                "(SELECT rowid FROM option_snapshots WHERE timestamp < ? "
                "ORDER BY timestamp LIMIT ?)",
                (cutoff, batch_rows),
            )
            changed = max(0, int(cursor.rowcount))
            self.connection.commit()
            removed += changed
            # Fail conservatively if a concurrent reader prevents truncation.
            # Continuing deletions could otherwise fill the remaining disk via WAL.
            checkpoint = self.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if checkpoint is not None and int(checkpoint[0]) != 0:
                complete = False
                break
            if changed == 0:
                break

        return {"removed_rows": removed, "retained_sessions": sessions[:keep_sessions],
                "complete": complete, "reusable_bytes": self._reusable_bytes()}

    def insert(self, snapshots: Iterable[FreeOptionSnapshot]) -> int:
        rows = []
        for item in snapshots:
            g = item.greeks
            rows.append(
                (
                    item.timestamp.isoformat(),
                    item.received_at.isoformat(),
                    item.timestamp_quality,
                    item.feed_delay_seconds,
                    item.option_symbol,
                    item.expiration.isoformat(),
                    item.right,
                    item.strike,
                    item.bid,
                    item.ask,
                    item.bid_size,
                    item.ask_size,
                    item.underlying_price,
                    item.minutes_to_expiry,
                    item.volume,
                    item.open_interest,
                    None if g is None else g.implied_volatility,
                    None if g is None else g.delta,
                    None if g is None else g.gamma,
                    None if g is None else g.theta_per_day,
                    None if g is None else g.vega_per_vol_point,
                    None if g is None else g.model,
                    item.source,
                    None if item.underlying_timestamp is None else item.underlying_timestamp.isoformat(),
                )
            )
        if not rows:
            return 0
        # Retention frees internal SQLite pages for reuse, but DELETE alone
        # does not necessarily shrink the on-disk database. Never compromise
        # journal space just because the database has free pages.
        low_disk = False
        if self.path != ":memory:":
            physical_free = shutil.disk_usage(Path(self.path).parent).free
            if physical_free < 12 * 1024 * 1024:
                raise StorageCapacityError("Market history paused: less than 12 MiB physical disk free; expand storage")
            low_disk = physical_free < 16 * 1024 * 1024
            if low_disk:
                required_reusable = max(8 * 1024 * 1024, len(rows) * 2048)
                if self._reusable_bytes() < required_reusable:
                    raise StorageCapacityError("Market history paused: insufficient reusable SQLite pages")
                # Checkpoint first so a stale WAL cannot consume the journal reserve.
                checkpoint = self.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                if checkpoint is not None and int(checkpoint[0]) != 0:
                    raise StorageCapacityError("Market history paused: WAL checkpoint busy")
        self.connection.executemany(
            """
            INSERT OR REPLACE INTO option_snapshots (
                timestamp,
                received_at,
                timestamp_quality,
                feed_delay_seconds,
                option_symbol,
                expiration,
                right,
                strike,
                bid,
                ask,
                bid_size,
                ask_size,
                underlying_price,
                minutes_to_expiry,
                volume,
                open_interest,
                implied_volatility,
                delta,
                gamma,
                theta,
                vega,
                greek_model,
                source,
                underlying_timestamp
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            rows,
        )
        self.connection.commit()
        if low_disk:
            # Best effort: SQLite still enforces safe failure if physical space
            # or the freelist runs low on a later cycle.
            self.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        return len(rows)

    def count(self) -> int:
        row = self.connection.execute("SELECT COUNT(*) FROM option_snapshots").fetchone()
        return int(row[0]) if row else 0

    def timestamp_quality_counts(self) -> dict[str, int]:
        rows = self.connection.execute(
            """
            SELECT COALESCE(timestamp_quality, 'legacy_unknown'), COUNT(*)
            FROM option_snapshots
            GROUP BY COALESCE(timestamp_quality, 'legacy_unknown')
            """
        ).fetchall()
        return {str(name): int(count) for name, count in rows}

    def export_csv(self, path: str | Path, *, trade_date: date | None = None) -> int:
        query = "SELECT * FROM option_snapshots"
        params: tuple[object, ...] = ()
        if trade_date is not None:
            query += " WHERE expiration = ?"
            params = (trade_date.isoformat(),)
        query += " ORDER BY timestamp, strike, right"
        cursor = self.connection.execute(query, params)
        names = [column[0] for column in cursor.description]
        rows = cursor.fetchall()
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(names)
            writer.writerows(rows)
        return len(rows)


def collect_and_store(provider, store: SnapshotStore, trade_date: date, *, observed_at=None) -> Tuple[FreeOptionSnapshot, ...]:
    """One collection cycle. Scheduling is intentionally kept outside this function."""

    snapshots = provider.collect_once(trade_date, observed_at=observed_at)
    store.insert(snapshots)
    return snapshots
