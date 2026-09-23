from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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


class PaperAccountStore:
    """Persistent cash-account ledger for paper execution.

    Prices are dollars per option contract, including the 100x option multiplier.
    The store intentionally keeps paper state separate from market-history data so
    resetting a simulation never destroys collected research data.
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
                    average_cost REAL NOT NULL
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
                    realized_pnl REAL NOT NULL DEFAULT 0
                )
                """
            )
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

    def positions(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT symbol, quantity, average_cost FROM paper_positions ORDER BY symbol"
            ).fetchall()
        return [
            {
                "symbol": str(row["symbol"]),
                "quantity": int(row["quantity"]),
                "average_cost": float(row["average_cost"]),
            }
            for row in rows
        ]

    def recent_trades(self, limit: int = 20) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 100))
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT timestamp, symbol, side, quantity, fill_price, realized_pnl
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
            }
            for row in rows
        ]
