from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .paper_broker import next_business_day


@dataclass(frozen=True)
class PaperAccountSnapshot:
    starting_cash: float
    settled_cash: float
    unsettled_cash: float
    realized_pnl: float
    open_positions: int
    trade_count: int
    updated_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "starting_cash": self.starting_cash,
            "settled_cash": self.settled_cash,
            "unsettled_cash": self.unsettled_cash,
            "realized_pnl": self.realized_pnl,
            "open_positions": self.open_positions,
            "trade_count": self.trade_count,
            "updated_at": self.updated_at,
        }


_POSITION_MIGRATIONS = {
    "opened_at": "TEXT",
    "entry_spot": "REAL",
    "strategy": "TEXT",
    "stop_price": "REAL",
    "target_price": "REAL",
    "max_hold_seconds": "INTEGER",
}

_TRADE_MIGRATIONS = {
    "strategy": "TEXT",
    "reason": "TEXT",
}


class PaperAccountStore:
    """Persistent cash-account ledger for paper execution.

    Prices are dollars per option contract, including the 100x option multiplier.
    Sale proceeds settle T+1 and therefore cannot be recycled immediately. The
    store is intentionally separate from market-history data so a paper reset
    never destroys research data.
    """

    def __init__(self, path: str | Path, *, default_starting_cash: float = 1000.0) -> None:
        if default_starting_cash <= 0:
            raise ValueError("default_starting_cash must be positive")
        self.path = str(path)
        self.default_starting_cash = float(default_starting_cash)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _migrate_columns(
        connection: sqlite3.Connection,
        table: str,
        migrations: dict[str, str],
    ) -> None:
        existing = {
            str(row[1])
            for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        for column, definition in migrations.items():
            if column not in existing:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS paper_account (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    starting_cash REAL NOT NULL,
                    settled_cash REAL NOT NULL,
                    realized_pnl REAL NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS paper_positions (
                    symbol TEXT PRIMARY KEY,
                    quantity INTEGER NOT NULL,
                    average_cost REAL NOT NULL,
                    opened_at TEXT,
                    entry_spot REAL,
                    strategy TEXT,
                    stop_price REAL,
                    target_price REAL,
                    max_hold_seconds INTEGER
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS paper_settlements (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    settle_date TEXT NOT NULL,
                    amount REAL NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS paper_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
                    quantity INTEGER NOT NULL,
                    fill_price REAL NOT NULL,
                    realized_pnl REAL NOT NULL DEFAULT 0,
                    strategy TEXT,
                    reason TEXT
                )
                """
            )
            self._migrate_columns(connection, "paper_positions", _POSITION_MIGRATIONS)
            self._migrate_columns(connection, "paper_trades", _TRADE_MIGRATIONS)

            row = connection.execute("SELECT id FROM paper_account WHERE id = 1").fetchone()
            if row is None:
                now = datetime.now(timezone.utc).isoformat()
                connection.execute(
                    """
                    INSERT INTO paper_account(id, starting_cash, settled_cash, realized_pnl, updated_at)
                    VALUES(1, ?, ?, 0, ?)
                    """,
                    (self.default_starting_cash, self.default_starting_cash, now),
                )
            connection.commit()

    def reset(self, starting_cash: float) -> PaperAccountSnapshot:
        if starting_cash <= 0:
            raise ValueError("starting_cash must be positive")
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute("DELETE FROM paper_positions")
            connection.execute("DELETE FROM paper_settlements")
            connection.execute("DELETE FROM paper_trades")
            connection.execute(
                """
                UPDATE paper_account
                SET starting_cash = ?, settled_cash = ?, realized_pnl = 0, updated_at = ?
                WHERE id = 1
                """,
                (float(starting_cash), float(starting_cash), now),
            )
            connection.commit()
        return self.snapshot()

    def settle_due(self, as_of: date) -> float:
        """Release T+1 proceeds that have reached their settlement date."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(SUM(amount), 0) AS value FROM paper_settlements WHERE settle_date <= ?",
                (as_of.isoformat(),),
            ).fetchone()
            released = float(row["value"] if row else 0.0)
            if released > 0:
                connection.execute(
                    "DELETE FROM paper_settlements WHERE settle_date <= ?",
                    (as_of.isoformat(),),
                )
                connection.execute(
                    """
                    UPDATE paper_account
                    SET settled_cash = settled_cash + ?, updated_at = ?
                    WHERE id = 1
                    """,
                    (released, now),
                )
            connection.commit()
        return released

    def open_long(
        self,
        *,
        symbol: str,
        quantity: int,
        fill_price: float,
        opened_at: datetime,
        entry_spot: float,
        strategy: str,
        stop_price: float,
        target_price: float,
        max_hold_seconds: int,
        reason: str,
    ) -> dict[str, Any]:
        """Persist a conservative long-option paper fill.

        ``fill_price`` is the all-in dollar cost per contract, including modeled
        fees/slippage. The debit must be fully covered by settled cash.
        """
        if not symbol or quantity <= 0 or fill_price <= 0:
            raise ValueError("invalid paper buy")
        if stop_price < 0 or target_price <= 0 or max_hold_seconds <= 0:
            raise ValueError("invalid paper exit controls")
        if opened_at.tzinfo is None:
            raise ValueError("opened_at must be timezone-aware")

        cost = float(fill_price) * int(quantity)
        timestamp = opened_at.astimezone(timezone.utc).isoformat()
        with self._connect() as connection:
            account = connection.execute(
                "SELECT settled_cash FROM paper_account WHERE id = 1"
            ).fetchone()
            if account is None:
                raise RuntimeError("paper account is not initialized")
            settled_cash = float(account["settled_cash"])
            if cost > settled_cash + 1e-9:
                raise ValueError("insufficient settled cash")

            current = connection.execute(
                "SELECT quantity, average_cost FROM paper_positions WHERE symbol = ?",
                (symbol,),
            ).fetchone()
            if current is None:
                new_quantity = int(quantity)
                new_average = float(fill_price)
                connection.execute(
                    """
                    INSERT INTO paper_positions(
                        symbol, quantity, average_cost, opened_at, entry_spot,
                        strategy, stop_price, target_price, max_hold_seconds
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        symbol,
                        new_quantity,
                        new_average,
                        timestamp,
                        float(entry_spot),
                        strategy,
                        float(stop_price),
                        float(target_price),
                        int(max_hold_seconds),
                    ),
                )
            else:
                old_quantity = int(current["quantity"])
                old_average = float(current["average_cost"])
                new_quantity = old_quantity + int(quantity)
                new_average = (
                    old_average * old_quantity + float(fill_price) * int(quantity)
                ) / new_quantity
                connection.execute(
                    """
                    UPDATE paper_positions
                    SET quantity = ?, average_cost = ?, strategy = ?,
                        stop_price = ?, target_price = ?, max_hold_seconds = ?
                    WHERE symbol = ?
                    """,
                    (
                        new_quantity,
                        new_average,
                        strategy,
                        float(stop_price),
                        float(target_price),
                        int(max_hold_seconds),
                        symbol,
                    ),
                )

            connection.execute(
                "UPDATE paper_account SET settled_cash = settled_cash - ?, updated_at = ? WHERE id = 1",
                (cost, timestamp),
            )
            connection.execute(
                """
                INSERT INTO paper_trades(
                    timestamp, symbol, side, quantity, fill_price, realized_pnl, strategy, reason
                ) VALUES (?, ?, 'BUY', ?, ?, 0, ?, ?)
                """,
                (timestamp, symbol, int(quantity), float(fill_price), strategy, reason),
            )
            connection.commit()

        return self.position(symbol) or {}

    def close_long(
        self,
        *,
        symbol: str,
        quantity: int,
        fill_price: float,
        closed_at: datetime,
        trade_date: date,
        reason: str,
    ) -> dict[str, Any]:
        """Close a long paper position and place proceeds into T+1 settlement."""
        if not symbol or quantity <= 0 or fill_price < 0:
            raise ValueError("invalid paper sell")
        if closed_at.tzinfo is None:
            raise ValueError("closed_at must be timezone-aware")

        timestamp = closed_at.astimezone(timezone.utc).isoformat()
        with self._connect() as connection:
            current = connection.execute(
                "SELECT * FROM paper_positions WHERE symbol = ?",
                (symbol,),
            ).fetchone()
            if current is None or int(current["quantity"]) < int(quantity):
                raise ValueError("paper position unavailable")

            average_cost = float(current["average_cost"])
            realized = (float(fill_price) - average_cost) * int(quantity)
            proceeds = float(fill_price) * int(quantity)
            remaining = int(current["quantity"]) - int(quantity)
            strategy = str(current["strategy"] or "paper")

            if remaining == 0:
                connection.execute("DELETE FROM paper_positions WHERE symbol = ?", (symbol,))
            else:
                connection.execute(
                    "UPDATE paper_positions SET quantity = ? WHERE symbol = ?",
                    (remaining, symbol),
                )

            connection.execute(
                "INSERT INTO paper_settlements(settle_date, amount) VALUES (?, ?)",
                (next_business_day(trade_date).isoformat(), proceeds),
            )
            connection.execute(
                """
                UPDATE paper_account
                SET realized_pnl = realized_pnl + ?, updated_at = ?
                WHERE id = 1
                """,
                (realized, timestamp),
            )
            connection.execute(
                """
                INSERT INTO paper_trades(
                    timestamp, symbol, side, quantity, fill_price, realized_pnl, strategy, reason
                ) VALUES (?, ?, 'SELL', ?, ?, ?, ?, ?)
                """,
                (
                    timestamp,
                    symbol,
                    int(quantity),
                    float(fill_price),
                    realized,
                    strategy,
                    reason,
                ),
            )
            connection.commit()

        return {
            "symbol": symbol,
            "quantity": int(quantity),
            "fill_price": float(fill_price),
            "realized_pnl": realized,
            "settlement_date": next_business_day(trade_date).isoformat(),
            "reason": reason,
        }

    def snapshot(self) -> PaperAccountSnapshot:
        with self._connect() as connection:
            account = connection.execute(
                "SELECT starting_cash, settled_cash, realized_pnl, updated_at FROM paper_account WHERE id = 1"
            ).fetchone()
            unsettled = connection.execute(
                "SELECT COALESCE(SUM(amount), 0) AS value FROM paper_settlements"
            ).fetchone()
            positions = connection.execute(
                "SELECT COUNT(*) AS value FROM paper_positions WHERE quantity > 0"
            ).fetchone()
            trades = connection.execute(
                "SELECT COUNT(*) AS value FROM paper_trades"
            ).fetchone()

        if account is None:
            raise RuntimeError("paper account is not initialized")
        return PaperAccountSnapshot(
            starting_cash=float(account["starting_cash"]),
            settled_cash=float(account["settled_cash"]),
            unsettled_cash=float(unsettled["value"] if unsettled else 0.0),
            realized_pnl=float(account["realized_pnl"]),
            open_positions=int(positions["value"] if positions else 0),
            trade_count=int(trades["value"] if trades else 0),
            updated_at=str(account["updated_at"]),
        )

    def position(self, symbol: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM paper_positions WHERE symbol = ?",
                (symbol,),
            ).fetchone()
        if row is None:
            return None
        return self._position_dict(row)

    @staticmethod
    def _position_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "symbol": str(row["symbol"]),
            "quantity": int(row["quantity"]),
            "average_cost": float(row["average_cost"]),
            "opened_at": None if row["opened_at"] is None else str(row["opened_at"]),
            "entry_spot": None if row["entry_spot"] is None else float(row["entry_spot"]),
            "strategy": None if row["strategy"] is None else str(row["strategy"]),
            "stop_price": None if row["stop_price"] is None else float(row["stop_price"]),
            "target_price": None if row["target_price"] is None else float(row["target_price"]),
            "max_hold_seconds": None
            if row["max_hold_seconds"] is None
            else int(row["max_hold_seconds"]),
        }

    def positions(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM paper_positions WHERE quantity > 0 ORDER BY symbol"
            ).fetchall()
        return [self._position_dict(row) for row in rows]

    def recent_trades(self, limit: int = 20) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 100))
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT timestamp, symbol, side, quantity, fill_price, realized_pnl,
                       strategy, reason
                FROM paper_trades
                ORDER BY id DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        return [
            {
                "timestamp": str(row["timestamp"]),
                "symbol": str(row["symbol"]),
                "side": str(row["side"]),
                "quantity": int(row["quantity"]),
                "fill_price": float(row["fill_price"]),
                "realized_pnl": float(row["realized_pnl"]),
                "strategy": None if row["strategy"] is None else str(row["strategy"]),
                "reason": None if row["reason"] is None else str(row["reason"]),
            }
            for row in rows
        ]
