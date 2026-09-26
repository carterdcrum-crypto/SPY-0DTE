from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


EASTERN = ZoneInfo("America/New_York")
ARM_CONFIRMATION = "ARM LIVE TODAY"


class LiveRiskEnvelopeError(RuntimeError):
    pass


@dataclass(frozen=True)
class LiveRiskEnvelope:
    trading_date: str | None
    daily_loss_limit: float | None
    daily_gain_limit: float | None
    max_account_exposure_pct: float | None
    max_contracts: int | None
    updated_at: str | None

    def armed_today(self, now: datetime | None = None) -> bool:
        current = (now or datetime.now(timezone.utc)).astimezone(EASTERN).date().isoformat()
        return self.trading_date == current and self.is_complete

    @property
    def is_complete(self) -> bool:
        return (
            self.daily_loss_limit is not None
            and self.daily_loss_limit > 0.0
            and self.daily_gain_limit is not None
            and self.daily_gain_limit > 0.0
            and self.max_account_exposure_pct is not None
            and 0.0 < self.max_account_exposure_pct <= 1.0
            and self.max_contracts is not None
            and self.max_contracts >= 1
        )

    def as_dict(self, now: datetime | None = None) -> dict[str, Any]:
        return {
            "trading_date": self.trading_date,
            "armed_today": self.armed_today(now),
            "configured": self.is_complete,
            "daily_loss_limit": self.daily_loss_limit,
            "daily_gain_limit": self.daily_gain_limit,
            "max_account_exposure_pct": self.max_account_exposure_pct,
            "max_contracts": self.max_contracts,
            "max_open_positions": 1,
            "updated_at": self.updated_at,
        }


class LiveRiskEnvelopeStore:
    """Persistent owner-selected live limits.

    A live envelope is intentionally armed for one Eastern trading date only.
    The owner chooses the loss stop, gain stop, capital-at-risk percentage and
    contract ceiling. A new trading date requires a fresh arm action.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS live_risk_envelope (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    trading_date TEXT,
                    daily_loss_limit REAL,
                    daily_gain_limit REAL,
                    max_account_exposure_pct REAL,
                    max_contracts INTEGER,
                    updated_at TEXT
                )
                """
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO live_risk_envelope(id)
                VALUES(1)
                """
            )
            connection.commit()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def snapshot(self) -> LiveRiskEnvelope:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT trading_date, daily_loss_limit, daily_gain_limit,
                       max_account_exposure_pct, max_contracts, updated_at
                FROM live_risk_envelope
                WHERE id = 1
                """
            ).fetchone()
        if row is None:
            return LiveRiskEnvelope(None, None, None, None, None, None)
        return LiveRiskEnvelope(
            trading_date=None if row[0] is None else str(row[0]),
            daily_loss_limit=None if row[1] is None else float(row[1]),
            daily_gain_limit=None if row[2] is None else float(row[2]),
            max_account_exposure_pct=None if row[3] is None else float(row[3]),
            max_contracts=None if row[4] is None else int(row[4]),
            updated_at=None if row[5] is None else str(row[5]),
        )

    def arm(
        self,
        *,
        daily_loss_limit: float,
        daily_gain_limit: float,
        max_account_exposure_pct: float,
        max_contracts: int,
        explicit_confirmation: str,
        now: datetime | None = None,
    ) -> LiveRiskEnvelope:
        if explicit_confirmation != ARM_CONFIRMATION:
            raise LiveRiskEnvelopeError("exact daily live-arm confirmation phrase required")
        if not (0.0 < daily_loss_limit <= 1_000_000.0):
            raise LiveRiskEnvelopeError("daily_loss_limit must be positive")
        if not (0.0 < daily_gain_limit <= 1_000_000.0):
            raise LiveRiskEnvelopeError("daily_gain_limit must be positive")
        if not (0.0 < max_account_exposure_pct <= 1.0):
            raise LiveRiskEnvelopeError("max_account_exposure_pct must be in (0, 1]")
        if max_contracts < 1 or max_contracts > 100:
            raise LiveRiskEnvelopeError("max_contracts must be between 1 and 100")

        current = (now or datetime.now(timezone.utc)).astimezone(EASTERN)
        updated = current.astimezone(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE live_risk_envelope
                SET trading_date = ?, daily_loss_limit = ?, daily_gain_limit = ?,
                    max_account_exposure_pct = ?, max_contracts = ?, updated_at = ?
                WHERE id = 1
                """,
                (
                    current.date().isoformat(),
                    float(daily_loss_limit),
                    float(daily_gain_limit),
                    float(max_account_exposure_pct),
                    int(max_contracts),
                    updated,
                ),
            )
            connection.commit()
        return self.snapshot()

    def disarm(self) -> LiveRiskEnvelope:
        updated = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE live_risk_envelope
                SET trading_date = NULL, updated_at = ?
                WHERE id = 1
                """,
                (updated,),
            )
            connection.commit()
        return self.snapshot()
