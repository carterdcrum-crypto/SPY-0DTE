"""Durable standing authorization and write-ahead journal for live execution.

Only broker-confirmed cumulative fills create inventory. A submission with an
unknown outcome keeps its original client id across restarts and blocks entry.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .broker import OptionOrderRequest
from .live_risk import EASTERN, LiveRiskEnvelope

TERMINAL = frozenset({"FILLED", "CANCELLED", "FAILED"})
ARM_AUTONOMY = "ENABLE AUTONOMOUS LIVE"


def autonomy_db_path() -> str:
    return os.environ.get("LIVE_AUTONOMY_DB_PATH", "").strip() or str(
        Path(os.environ.get("CONTROL_DB_PATH", "/data/spy_control.sqlite")).with_name("spy_live_autonomy.sqlite")
    )


def utc_text(now: datetime) -> str:
    if now.tzinfo is None:
        raise ValueError("timezone-aware timestamp required")
    return now.astimezone(timezone.utc).isoformat()


class LiveAutonomyStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS live_authorization (
                    id INTEGER PRIMARY KEY CHECK(id=1), enabled INTEGER NOT NULL,
                    owner TEXT NOT NULL, account_id TEXT NOT NULL, limits TEXT NOT NULL,
                    updated_at TEXT NOT NULL, generation INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS live_journal (
                    client_id TEXT PRIMARY KEY, signal_key TEXT UNIQUE NOT NULL,
                    position_id TEXT NOT NULL, symbol TEXT NOT NULL, request TEXT NOT NULL,
                    status TEXT NOT NULL, filled INTEGER NOT NULL DEFAULT 0,
                    average_price REAL NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL, cancel_at TEXT, reason TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS live_days (
                    trading_date TEXT PRIMARY KEY, start_equity REAL NOT NULL,
                    high_watermark REAL NOT NULL, halt_reason TEXT
                );
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        try:
            with db:
                yield db
        finally:
            db.close()

    def policy(self) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute("SELECT * FROM live_authorization WHERE id=1").fetchone()
        if row is None:
            return {"enabled": False, "configured": False, "account_id": None, "generation": 0}
        return {**dict(row), "enabled": bool(row["enabled"]), "configured": True, "limits": json.loads(row["limits"])}

    def enable(self, *, owner: str, limits: LiveRiskEnvelope, confirmation: str,
               now: datetime, account_id: str = "") -> None:
        if confirmation != ARM_AUTONOMY or not owner or owner == "preview":
            raise ValueError("authenticated owner and explicit live authorization required")
        values = (limits.daily_loss_limit, limits.daily_gain_limit, limits.max_account_exposure_pct, limits.max_contracts)
        if not limits.is_complete or any(not math.isfinite(float(v)) for v in values):
            raise ValueError("complete finite live limits required")
        if limits.max_contracts > 100 or limits.daily_loss_limit > 1_000_000 or limits.daily_gain_limit > 1_000_000:
            raise ValueError("live limits exceed supported bounds")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT * FROM live_authorization WHERE id=1").fetchone()
            if old and old["account_id"]:
                if account_id and account_id != old["account_id"]:
                    raise ValueError("live authorization is pinned to a different account")
                account_id = old["account_id"]
            db.execute("""INSERT INTO live_authorization VALUES(1,1,?,?,?,?,1)
                ON CONFLICT(id) DO UPDATE SET enabled=1, owner=excluded.owner,
                account_id=excluded.account_id, limits=excluded.limits,
                updated_at=excluded.updated_at, generation=generation+1""",
                (owner, account_id, json.dumps(asdict(limits)), utc_text(now)))

    def disable(self, now: datetime) -> None:
        with self.connect() as db:
            db.execute("UPDATE live_authorization SET enabled=0, updated_at=?, generation=generation+1 WHERE id=1", (utc_text(now),))

    def pin_account(self, account_id: str) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT account_id FROM live_authorization WHERE id=1").fetchone()
            if row is None or (row[0] and row[0] != account_id):
                raise ValueError("live account changed; existing authorization cannot be transferred")
            db.execute("UPDATE live_authorization SET account_id=? WHERE id=1", (account_id,))

    def envelope(self, now: datetime) -> LiveRiskEnvelope | None:
        policy = self.policy()
        if not policy["configured"]:
            return None
        values = dict(policy["limits"])
        values["trading_date"] = now.astimezone(EASTERN).date().isoformat()
        return LiveRiskEnvelope(**values)

    def orders(self, *, pending_only: bool = False) -> list[dict[str, Any]]:
        with self.connect() as db:
            where = " WHERE status NOT IN ('FILLED','CANCELLED','FAILED')" if pending_only else ""
            rows = db.execute("SELECT * FROM live_journal"+where+" ORDER BY created_at, rowid").fetchall()
        return [{**dict(row), "request": json.loads(row["request"])} for row in rows]

    def positions(self) -> list[dict[str, Any]]:
        groups: dict[str, dict[str, Any]] = {}
        for row in self.orders():
            order = row["request"]
            if order["side"] == "BUY":
                groups[row["position_id"]] = {
                    "position_id": row["position_id"], "symbol": row["symbol"],
                    "quantity": row["filled"], "average_price": row["average_price"],
                    "opened_at": row["created_at"], "request": order,
                    "exit_requested": False,
                }
            else:
                position = groups.get(row["position_id"])
                if position is None:
                    raise ValueError("exit journal has no corresponding entry")
                position["quantity"] -= row["filled"]
                position["exit_requested"] = True
        if any(p["quantity"] < 0 for p in groups.values()):
            raise ValueError("broker fills exceed owned quantity")
        return [p for p in groups.values() if p["quantity"] > 0]

    def reserve(self, order: OptionOrderRequest, *, symbol: str, signal_key: str,
                reason: str, now: datetime, generation: int, position_id: str | None = None) -> bool:
        """Commit intent BEFORE any network write. Never reuse a spent signal."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            policy = db.execute("SELECT * FROM live_authorization WHERE id=1").fetchone()
            if policy is None or policy["account_id"] != order.account_id:
                raise ValueError("order account is not authorized")
            if order.side == "BUY" and (not policy["enabled"] or policy["generation"] != generation):
                return False
            if db.execute("SELECT 1 FROM live_journal WHERE status NOT IN ('FILLED','CANCELLED','FAILED')").fetchone():
                return False
            cursor = db.execute("""INSERT OR IGNORE INTO live_journal
                (client_id,signal_key,position_id,symbol,request,status,created_at,updated_at,reason)
                VALUES(?,?,?,?,?,'UNKNOWN',?,?,?)""",
                (order.client_order_id, signal_key, position_id or order.client_order_id, symbol,
                 json.dumps(asdict(order)), utc_text(now), utc_text(now), reason))
            return cursor.rowcount == 1

    def reconcile(self, client_id: str, *, status: str, filled: int, average_price: float, now: datetime) -> None:
        if status not in TERMINAL | {"PENDING", "SUBMITTED", "PARTIAL_FILLED"}:
            raise ValueError("unknown broker order status")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT * FROM live_journal WHERE client_id=?", (client_id,)).fetchone()
            if old is None:
                raise ValueError("unknown journal order")
            quantity = json.loads(old["request"])["quantity"]
            if isinstance(filled, bool) or int(filled) != filled or not old["filled"] <= filled <= quantity:
                raise ValueError("invalid or regressing cumulative broker fill")
            if not math.isfinite(average_price) or average_price < 0 or (filled and average_price <= 0):
                raise ValueError("invalid broker average fill price")
            if status == "FILLED" and filled != quantity:
                raise ValueError("FILLED status requires the entire quantity")
            if old["status"] in TERMINAL and status != old["status"]:
                raise ValueError("terminal broker order changed state")
            db.execute("UPDATE live_journal SET status=?, filled=?, average_price=?, updated_at=? WHERE client_id=?",
                       (status, filled, average_price, utc_text(now), client_id))

    def mark_cancel(self, client_id: str, now: datetime) -> None:
        with self.connect() as db:
            db.execute("UPDATE live_journal SET cancel_at=? WHERE client_id=?", (utc_text(now), client_id))

    def daily(self, now: datetime, equity: float, pnl: float, limits: LiveRiskEnvelope) -> dict[str, Any]:
        day = now.astimezone(EASTERN).date().isoformat()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT OR IGNORE INTO live_days VALUES(?,?,?,NULL)", (day, max(0.01, equity-pnl), equity))
            reason = "daily_loss_stop_reached" if pnl <= -limits.daily_loss_limit else (
                "daily_gain_stop_reached" if pnl >= limits.daily_gain_limit else None)
            db.execute("UPDATE live_days SET high_watermark=MAX(high_watermark,?), halt_reason=COALESCE(halt_reason,?) WHERE trading_date=?", (equity, reason, day))
            return dict(db.execute("SELECT * FROM live_days WHERE trading_date=?", (day,)).fetchone())
