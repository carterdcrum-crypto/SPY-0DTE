from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from cryptography.fernet import Fernet, InvalidToken
from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token
from pydantic import BaseModel, Field

from .cadence import MarketCadencePolicy
from .collector_runner import run_forever

log = logging.getLogger("spy0dte.api")


class TradingMode(str, Enum):
    SHADOW = "SHADOW"
    PAPER = "PAPER"
    LIVE = "LIVE"


@dataclass(frozen=True)
class UserIdentity:
    subject: str
    email: str


class ModeRequest(BaseModel):
    mode: TradingMode
    confirmation: str | None = None


class WebullCredentialRequest(BaseModel):
    environment: str = Field(pattern="^(sandbox|production)$")
    app_key: str = Field(min_length=8, max_length=512)
    app_secret: str = Field(min_length=8, max_length=2048)


class ControlStore:
    """Persistent control-plane state kept separate from market-history data."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS app_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS broker_credentials (
                    environment TEXT PRIMARY KEY,
                    app_key BLOB NOT NULL,
                    app_secret BLOB NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.commit()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def get_mode(self) -> TradingMode:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM app_state WHERE key = 'trading_mode'"
            ).fetchone()
        if row is None:
            return TradingMode.PAPER
        try:
            return TradingMode(str(row[0]))
        except ValueError:
            return TradingMode.SHADOW

    def set_mode(self, mode: TradingMode) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO app_state(key, value, updated_at)
                VALUES('trading_mode', ?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                """,
                (mode.value, now),
            )
            connection.commit()

    def save_webull_credentials(
        self,
        environment: str,
        app_key: str,
        app_secret: str,
        cipher: Fernet,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        encrypted_key = cipher.encrypt(app_key.encode("utf-8"))
        encrypted_secret = cipher.encrypt(app_secret.encode("utf-8"))
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO broker_credentials(environment, app_key, app_secret, updated_at)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(environment) DO UPDATE SET
                    app_key=excluded.app_key,
                    app_secret=excluded.app_secret,
                    updated_at=excluded.updated_at
                """,
                (environment, encrypted_key, encrypted_secret, now),
            )
            connection.commit()

    def broker_profile(self, environment: str) -> Mapping[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT updated_at FROM broker_credentials WHERE environment = ?",
                (environment,),
            ).fetchone()
        return {
            "environment": environment,
            "configured": row is not None,
            "updated_at": None if row is None else str(row[0]),
        }

    def load_webull_credentials(self, environment: str, cipher: Fernet) -> tuple[str, str] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT app_key, app_secret FROM broker_credentials WHERE environment = ?",
                (environment,),
            ).fetchone()
        if row is None:
            return None
        try:
            return (
                cipher.decrypt(bytes(row[0])).decode("utf-8"),
                cipher.decrypt(bytes(row[1])).decode("utf-8"),
            )
        except InvalidToken as exc:
            raise RuntimeError("stored broker credentials cannot be decrypted") from exc


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _control_store() -> ControlStore:
    return ControlStore(os.environ.get("CONTROL_DB_PATH", "/data/spy_control.sqlite"))


def _cipher() -> Fernet:
    key = os.environ.get("APP_ENCRYPTION_KEY", "").strip()
    if not key:
        raise HTTPException(status_code=503, detail="credential encryption is not configured")
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=503, detail="credential encryption key is invalid") from exc


def _allowed_emails() -> set[str]:
    raw = os.environ.get("APP_ALLOWED_EMAILS", "")
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def _verify_google_bearer(authorization: str | None) -> UserIdentity:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing Google ID token")
    token = authorization.split(" ", 1)[1].strip()
    client_id = os.environ.get("GOOGLE_WEB_CLIENT_ID", "").strip()
    if not client_id:
        raise HTTPException(status_code=503, detail="Google sign-in is not configured")

    try:
        claims = id_token.verify_oauth2_token(token, GoogleRequest(), client_id)
    except Exception as exc:
        raise HTTPException(status_code=401, detail="invalid Google ID token") from exc

    email = str(claims.get("email", "")).lower()
    subject = str(claims.get("sub", ""))
    if not email or not subject or not claims.get("email_verified", False):
        raise HTTPException(status_code=403, detail="verified Google account required")

    allowed = _allowed_emails()
    if not allowed:
        raise HTTPException(status_code=503, detail="owner Google account allowlist is not configured")
    if email not in allowed:
        raise HTTPException(status_code=403, detail="Google account is not authorized for this app")
    return UserIdentity(subject=subject, email=email)


def require_user(authorization: str | None = Header(default=None)) -> UserIdentity:
    return _verify_google_bearer(authorization)


def _market_status() -> dict[str, Any]:
    path = Path(os.environ.get("COLLECTOR_DB_PATH", "/data/spy_0dte.sqlite"))
    empty = {
        "rows": 0,
        "last_market_timestamp": None,
        "last_received_at": None,
        "data_age_seconds": None,
        "feed_delay_seconds": None,
        "spot": None,
        "option_symbol": None,
        "bid": None,
        "ask": None,
        "timestamp_quality": None,
    }
    if not path.exists():
        return empty

    try:
        with sqlite3.connect(str(path), timeout=2.0) as connection:
            count_row = connection.execute("SELECT COUNT(*) FROM option_snapshots").fetchone()
            latest = connection.execute(
                """
                SELECT timestamp, received_at, feed_delay_seconds, underlying_price,
                       option_symbol, bid, ask, timestamp_quality
                FROM option_snapshots
                ORDER BY COALESCE(received_at, timestamp) DESC
                LIMIT 1
                """
            ).fetchone()
    except sqlite3.Error:
        log.exception("failed reading collector database")
        return empty

    result = dict(empty)
    result["rows"] = int(count_row[0]) if count_row else 0
    if latest is None:
        return result

    market_ts, received_at, delay, spot, symbol, bid, ask, quality = latest
    result.update(
        {
            "last_market_timestamp": market_ts,
            "last_received_at": received_at,
            "feed_delay_seconds": None if delay is None else float(delay),
            "spot": None if spot is None else float(spot),
            "option_symbol": symbol,
            "bid": None if bid is None else float(bid),
            "ask": None if ask is None else float(ask),
            "timestamp_quality": quality,
        }
    )
    if received_at:
        try:
            parsed = datetime.fromisoformat(str(received_at))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            result["data_age_seconds"] = max(
                0.0,
                (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds(),
            )
        except ValueError:
            pass
    return result


def _live_gate(store: ControlStore) -> dict[str, Any]:
    reasons: list[str] = []
    if not _env_bool("ALLOW_LIVE_ORDERS", False):
        reasons.append("live_orders_disabled")
    if not _env_bool("LIVE_ORDER_EXECUTOR_READY", False):
        reasons.append("live_order_executor_not_ready")
    if os.environ.get("WEBULL_ENVIRONMENT", "sandbox").strip().lower() != "production":
        reasons.append("webull_environment_not_production")
    if not store.broker_profile("production")["configured"]:
        reasons.append("production_webull_credentials_missing")
    return {"ready": not reasons, "reasons": reasons}


def status_payload() -> dict[str, Any]:
    store = _control_store()
    cadence = MarketCadencePolicy()
    return {
        "service": "SPY-0DTE",
        "server_time": datetime.now(timezone.utc).isoformat(),
        "mode": store.get_mode().value,
        "research_only": not _env_bool("LIVE_ORDER_EXECUTOR_READY", False),
        "cadence": {
            "engine_tick_seconds": cadence.decision_tick_seconds,
            "sandbox_active_option_refresh_seconds": cadence.sandbox_active_option_refresh_seconds,
            "production_active_option_refresh_seconds": cadence.production_active_option_refresh_seconds,
            "full_chain_refresh_seconds": cadence.full_chain_refresh_seconds,
        },
        "market": _market_status(),
        "broker": {
            "sandbox": store.broker_profile("sandbox"),
            "production": store.broker_profile("production"),
        },
        "live_gate": _live_gate(store),
        "decision": {
            "state": "RESEARCH_ONLY",
            "reason": "paper/shadow decision loop is being connected to the mobile control plane",
        },
    }


def _collector_thread_target() -> None:
    try:
        run_forever()
    except Exception:
        log.exception("collector thread stopped")


@asynccontextmanager
async def lifespan(_: FastAPI):
    if _env_bool("START_COLLECTOR_IN_API", True):
        thread = threading.Thread(
            target=_collector_thread_target,
            name="spy0dte-collector",
            daemon=True,
        )
        thread.start()
    yield


app = FastAPI(
    title="SPY 0DTE Control API",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/v1/status")
def status(_: UserIdentity = Depends(require_user)) -> dict[str, Any]:
    return status_payload()


@app.post("/v1/mode")
def set_mode(request: ModeRequest, _: UserIdentity = Depends(require_user)) -> dict[str, Any]:
    store = _control_store()
    if request.mode is TradingMode.LIVE:
        gate = _live_gate(store)
        if not gate["ready"]:
            raise HTTPException(status_code=409, detail={"message": "live mode is locked", **gate})
        if request.confirmation != "ENABLE LIVE TRADING":
            raise HTTPException(status_code=400, detail="explicit live-trading confirmation required")
    store.set_mode(request.mode)
    return {"mode": request.mode.value, "live_gate": _live_gate(store)}


@app.get("/v1/broker/webull")
def webull_profiles(_: UserIdentity = Depends(require_user)) -> dict[str, Any]:
    store = _control_store()
    return {
        "sandbox": store.broker_profile("sandbox"),
        "production": store.broker_profile("production"),
    }


@app.post("/v1/broker/webull")
def save_webull_profile(
    request: WebullCredentialRequest,
    _: UserIdentity = Depends(require_user),
) -> dict[str, Any]:
    store = _control_store()
    store.save_webull_credentials(
        request.environment,
        request.app_key,
        request.app_secret,
        _cipher(),
    )
    # Never echo credentials, even paper credentials.
    return dict(store.broker_profile(request.environment))


@app.websocket("/v1/live")
async def live(websocket: WebSocket) -> None:
    try:
        _verify_google_bearer(websocket.headers.get("authorization"))
    except HTTPException as exc:
        await websocket.close(code=4401, reason=str(exc.detail))
        return

    await websocket.accept()
    try:
        while True:
            await websocket.send_json(status_payload())
            await asyncio.sleep(1.0)
    except WebSocketDisconnect:
        return
