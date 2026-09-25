from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


DEFAULT_AUDIT_HORIZONS = (30, 60, 120, 300)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class PendingExitAuditMark:
    audit_id: int
    symbol: str
    reason: str
    exited_at: datetime
    horizon_seconds: int
    quantity: int
    actual_exit_value: float
    actual_realized_pnl: float

    @property
    def due_at(self) -> datetime:
        return self.exited_at + timedelta(seconds=self.horizon_seconds)


class ExitAuditStore:
    """Persistent counterfactual tracker for model-driven PAPER exits.

    The live paper ledger records what actually happened. This side ledger asks a
    separate research question: after a model-driven exit, what would the same
    contract have been worth if we had kept holding for another 30/60/120/300
    seconds? It never changes cash, positions, orders, or risk decisions.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
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
                CREATE TABLE IF NOT EXISTS paper_exit_audits (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    exited_at TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    actual_exit_value REAL NOT NULL,
                    actual_realized_pnl REAL NOT NULL,
                    strategy TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS paper_exit_audit_marks (
                    audit_id INTEGER NOT NULL,
                    horizon_seconds INTEGER NOT NULL,
                    observed_at TEXT NOT NULL,
                    hypothetical_exit_value REAL NOT NULL,
                    pnl_delta_vs_actual REAL NOT NULL,
                    hypothetical_realized_pnl REAL NOT NULL,
                    PRIMARY KEY (audit_id, horizon_seconds),
                    FOREIGN KEY (audit_id) REFERENCES paper_exit_audits(id)
                )
                """
            )
            connection.commit()

    def record_exit(
        self,
        *,
        symbol: str,
        reason: str,
        exited_at: datetime,
        quantity: int,
        actual_exit_value: float,
        actual_realized_pnl: float,
        strategy: str | None,
    ) -> int:
        when = _as_utc(exited_at).isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO paper_exit_audits(
                    symbol, reason, exited_at, quantity, actual_exit_value,
                    actual_realized_pnl, strategy
                ) VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    symbol,
                    reason,
                    when,
                    int(quantity),
                    float(actual_exit_value),
                    float(actual_realized_pnl),
                    strategy,
                ),
            )
            connection.commit()
            return int(cursor.lastrowid)

    def pending_marks(
        self,
        as_of: datetime,
        *,
        horizons: Iterable[int] = DEFAULT_AUDIT_HORIZONS,
        limit: int = 100,
    ) -> tuple[PendingExitAuditMark, ...]:
        now = _as_utc(as_of)
        horizon_values = tuple(sorted({int(value) for value in horizons if int(value) > 0}))
        if not horizon_values:
            return ()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, symbol, reason, exited_at, quantity,
                       actual_exit_value, actual_realized_pnl
                FROM paper_exit_audits
                ORDER BY id DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
            existing = {
                (int(row["audit_id"]), int(row["horizon_seconds"]))
                for row in connection.execute(
                    "SELECT audit_id, horizon_seconds FROM paper_exit_audit_marks"
                ).fetchall()
            }

        pending: list[PendingExitAuditMark] = []
        for row in rows:
            exited_at = datetime.fromisoformat(str(row["exited_at"]))
            exited_at = _as_utc(exited_at)
            for horizon in horizon_values:
                key = (int(row["id"]), horizon)
                if key in existing:
                    continue
                item = PendingExitAuditMark(
                    audit_id=int(row["id"]),
                    symbol=str(row["symbol"]),
                    reason=str(row["reason"]),
                    exited_at=exited_at,
                    horizon_seconds=horizon,
                    quantity=int(row["quantity"]),
                    actual_exit_value=float(row["actual_exit_value"]),
                    actual_realized_pnl=float(row["actual_realized_pnl"]),
                )
                if item.due_at <= now:
                    pending.append(item)
        return tuple(pending)

    def record_mark(
        self,
        item: PendingExitAuditMark,
        *,
        observed_at: datetime,
        hypothetical_exit_value: float,
    ) -> dict[str, Any]:
        value = max(0.0, float(hypothetical_exit_value))
        delta = (value - item.actual_exit_value) * item.quantity
        hypothetical_pnl = item.actual_realized_pnl + delta
        observed = _as_utc(observed_at).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO paper_exit_audit_marks(
                    audit_id, horizon_seconds, observed_at,
                    hypothetical_exit_value, pnl_delta_vs_actual,
                    hypothetical_realized_pnl
                ) VALUES(?, ?, ?, ?, ?, ?)
                """,
                (
                    item.audit_id,
                    item.horizon_seconds,
                    observed,
                    value,
                    delta,
                    hypothetical_pnl,
                ),
            )
            connection.commit()
        return {
            "audit_id": item.audit_id,
            "symbol": item.symbol,
            "reason": item.reason,
            "horizon_seconds": item.horizon_seconds,
            "actual_exit_value": item.actual_exit_value,
            "hypothetical_exit_value": value,
            "pnl_delta_vs_actual": delta,
            "actual_realized_pnl": item.actual_realized_pnl,
            "hypothetical_realized_pnl": hypothetical_pnl,
            "observed_at": observed,
        }

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT a.id, a.symbol, a.reason, a.exited_at, a.quantity,
                       a.actual_exit_value, a.actual_realized_pnl, a.strategy,
                       m.horizon_seconds, m.observed_at,
                       m.hypothetical_exit_value, m.pnl_delta_vs_actual,
                       m.hypothetical_realized_pnl
                FROM paper_exit_audits a
                LEFT JOIN paper_exit_audit_marks m ON m.audit_id = a.id
                ORDER BY a.id DESC, m.horizon_seconds ASC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        return [dict(row) for row in rows]
