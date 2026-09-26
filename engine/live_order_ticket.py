from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .broker import OptionOrderRequest


CONFIRMATION_PHRASE = "CONFIRM THIS LIVE ORDER"


class LiveOrderAuthorizationError(RuntimeError):
    pass


@dataclass(frozen=True)
class LiveOrderAuthorization:
    ticket_id: str
    order_fingerprint: str
    user_subject: str
    consumed_at: str


def order_fingerprint(order: OptionOrderRequest) -> str:
    payload = {
        "account_id": order.account_id,
        "client_order_id": order.client_order_id,
        "underlying": order.underlying.upper(),
        "strike_price": round(float(order.strike_price), 6),
        "expiration_date": order.expiration_date,
        "option_type": order.option_type,
        "side": order.side,
        "position_intent": order.position_intent,
        "quantity": int(order.quantity),
        "limit_price": round(float(order.limit_price), 4),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class LiveOrderTicketStore:
    """Persistent, exact-order, short-lived authorization tickets.

    Tickets are deliberately single-use and bound to the authenticated user and
    the exact order payload. The raw ticket secret is never stored. A ticket is
    consumed atomically before a production submit attempt so network retries
    cannot accidentally duplicate an order.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS live_order_tickets (
                    ticket_id TEXT PRIMARY KEY,
                    secret_hash TEXT NOT NULL,
                    order_fingerprint TEXT NOT NULL,
                    user_subject TEXT NOT NULL,
                    issued_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    consumed_at TEXT
                )
                """
            )
            connection.commit()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def issue(
        self,
        order: OptionOrderRequest,
        *,
        user_subject: str,
        explicit_confirmation: str,
        ttl_seconds: int = 45,
    ) -> str:
        if explicit_confirmation != CONFIRMATION_PHRASE:
            raise LiveOrderAuthorizationError("exact live-order confirmation phrase required")
        if not user_subject.strip():
            raise LiveOrderAuthorizationError("authenticated user subject is required")
        if ttl_seconds < 5 or ttl_seconds > 120:
            raise ValueError("ttl_seconds must be between 5 and 120")

        now = datetime.now(timezone.utc)
        expires = now + timedelta(seconds=ttl_seconds)
        ticket_id = secrets.token_urlsafe(12)
        secret = secrets.token_urlsafe(32)
        secret_hash = hashlib.sha256(secret.encode("utf-8")).hexdigest()

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO live_order_tickets(
                    ticket_id, secret_hash, order_fingerprint, user_subject,
                    issued_at, expires_at, consumed_at
                ) VALUES(?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    ticket_id,
                    secret_hash,
                    order_fingerprint(order),
                    user_subject,
                    now.isoformat(),
                    expires.isoformat(),
                ),
            )
            connection.commit()
        return f"{ticket_id}.{secret}"

    def consume(
        self,
        token: str,
        order: OptionOrderRequest,
        *,
        user_subject: str,
    ) -> LiveOrderAuthorization:
        try:
            ticket_id, secret = token.split(".", 1)
        except ValueError as exc:
            raise LiveOrderAuthorizationError("invalid live-order ticket") from exc
        if not ticket_id or not secret:
            raise LiveOrderAuthorizationError("invalid live-order ticket")

        supplied_hash = hashlib.sha256(secret.encode("utf-8")).hexdigest()
        expected_fingerprint = order_fingerprint(order)
        now = datetime.now(timezone.utc)

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT secret_hash, order_fingerprint, user_subject,
                       expires_at, consumed_at
                FROM live_order_tickets
                WHERE ticket_id = ?
                """,
                (ticket_id,),
            ).fetchone()
            if row is None:
                raise LiveOrderAuthorizationError("live-order ticket does not exist")

            stored_hash, stored_fingerprint, stored_subject, expires_at, consumed_at = row
            if consumed_at is not None:
                raise LiveOrderAuthorizationError("live-order ticket has already been used")
            if not hmac.compare_digest(str(stored_hash), supplied_hash):
                raise LiveOrderAuthorizationError("invalid live-order ticket secret")
            if not hmac.compare_digest(str(stored_fingerprint), expected_fingerprint):
                raise LiveOrderAuthorizationError("live-order ticket does not match this exact order")
            if not hmac.compare_digest(str(stored_subject), user_subject):
                raise LiveOrderAuthorizationError("live-order ticket belongs to a different user")

            expiry = datetime.fromisoformat(str(expires_at))
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            if now >= expiry.astimezone(timezone.utc):
                raise LiveOrderAuthorizationError("live-order ticket has expired")

            consumed_iso = now.isoformat()
            connection.execute(
                "UPDATE live_order_tickets SET consumed_at = ? WHERE ticket_id = ?",
                (consumed_iso, ticket_id),
            )
            connection.commit()
            return LiveOrderAuthorization(
                ticket_id=ticket_id,
                order_fingerprint=expected_fingerprint,
                user_subject=user_subject,
                consumed_at=consumed_iso,
            )
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
