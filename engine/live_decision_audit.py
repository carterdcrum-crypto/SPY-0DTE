"""Sanitized, durable LIVE decision audit for missed-signal diagnosis.

No broker credentials, order payloads, or account identifiers are recorded.
A failed audit must never disrupt order reconciliation or risk enforcement.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def record_decision(values: dict[str, Any]) -> None:
    path = Path(os.environ.get("LIVE_DECISION_AUDIT_DB_PATH", "/data/spy_live_decisions.sqlite"))
    path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    # Keep a bounded decision history; 20k observations ~= 2.5 full sessions
    # at three-second cadence. No owner identity or monetary balances.
    with sqlite3.connect(path, timeout=2) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS live_decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                strategy TEXT NOT NULL,
                state TEXT NOT NULL,
                reason TEXT NOT NULL,
                data_blocker TEXT,
                feed_age_seconds REAL,
                feed_delay_seconds REAL,
                quote_source TEXT,
                signal_detected INTEGER NOT NULL
            )"""
        )
        market = values.get("market") or {}
        conn.execute(
            """INSERT INTO live_decisions(
                 timestamp,strategy,state,reason,data_blocker,
                 feed_age_seconds,feed_delay_seconds,quote_source,signal_detected
               ) VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                now, str(values.get("strategy") or "unselected")[:90],
                str(values.get("state") or "unknown")[:90],
                str(values.get("reason") or "no reason returned")[:300],
                str(values.get("data_blocker") or "")[:200],
                market.get("data_age_seconds"),
                market.get("feed_delay_seconds"),
                str(market.get("source") or "")[:50],
                int(bool(values.get("last_signal") or values.get("signal") or
                         values.get("entry_alert") or
                         values.get("state") in ("SIGNAL", "ORDER_SUBMITTED"))),
            ),
        )
        if conn.execute("SELECT MAX(id) FROM live_decisions").fetchone()[0] % 200 == 0:
            conn.execute(
                """DELETE FROM live_decisions WHERE id <= (
                    SELECT COALESCE(MAX(id), 0) - 20000 FROM live_decisions
                )"""
            )


def recent_decisions(limit: int = 100) -> list[dict[str, Any]]:
    path = Path(os.environ.get("LIVE_DECISION_AUDIT_DB_PATH", "/data/spy_live_decisions.sqlite"))
    if not path.exists():
        return []
    with sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=2) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT timestamp,strategy,state,reason,data_blocker,
                      feed_age_seconds,feed_delay_seconds,quote_source,signal_detected
               FROM live_decisions ORDER BY id DESC LIMIT ?""",
            (max(1, min(int(limit), 500)),),
        ).fetchall()
    return [dict(row) for row in rows]
