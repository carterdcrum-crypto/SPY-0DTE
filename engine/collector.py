from __future__ import annotations

import csv
import sqlite3
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Iterable, Tuple

from .providers.webull_free import FreeOptionSnapshot


SCHEMA = """
CREATE TABLE IF NOT EXISTS option_snapshots (
    timestamp TEXT NOT NULL,
    option_symbol TEXT NOT NULL,
    expiration TEXT NOT NULL,
    right TEXT NOT NULL,
    strike REAL NOT NULL,
    bid REAL NOT NULL,
    ask REAL NOT NULL,
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


class SnapshotStore:
    """Small zero-cost SQLite store for our self-built options dataset."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self.connection = sqlite3.connect(self.path)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute(SCHEMA)
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def insert(self, snapshots: Iterable[FreeOptionSnapshot]) -> int:
        rows = []
        for item in snapshots:
            g = item.greeks
            rows.append(
                (
                    item.timestamp.isoformat(),
                    item.option_symbol,
                    item.expiration.isoformat(),
                    item.right,
                    item.strike,
                    item.bid,
                    item.ask,
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
            INSERT OR REPLACE INTO option_snapshots VALUES
            (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        self.connection.commit()
        return len(rows)

    def count(self) -> int:
        row = self.connection.execute("SELECT COUNT(*) FROM option_snapshots").fetchone()
        return int(row[0]) if row else 0

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
