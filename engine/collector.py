from __future__ import annotations

import csv
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
    PRIMARY KEY (timestamp, option_symbol)
);
"""


_MIGRATIONS = {
    "received_at": "TEXT",
    "timestamp_quality": "TEXT",
    "feed_delay_seconds": "REAL",
    "bid_size": "INTEGER NOT NULL DEFAULT 0",
    "ask_size": "INTEGER NOT NULL DEFAULT 0",
}


class SnapshotStore:
    """Small zero-cost SQLite store for our self-built options dataset."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self.connection = sqlite3.connect(self.path)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute(SCHEMA)
        self._migrate_if_needed()
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
                )
            )
        if not rows:
            return 0
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
                source
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            rows,
        )
        self.connection.commit()
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
