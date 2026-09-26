from __future__ import annotations

import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, Field

from . import api as base
from .broker import OptionOrderRequest
from .live_alert_policy import build_live_entry_alert
from .live_broker_guard import cached_live_broker_guard
from .live_risk import LiveRiskEnvelopeError, LiveRiskEnvelopeStore
from .paper_account import PaperAccountStore
from .paper_ai_autotrader import run_forever as run_realtime_paper_autotrader
from .paper_ai_delayed_simulation import run_forever as run_delayed_paper_autotrader
from .paper_autotrader import automation_status
from .tradier_live import TradierLiveClient, TradierLiveError, tradier_env_status


class PaperResetRequest(BaseModel):
    starting_cash: float = Field(gt=0, le=1_000_000)
    confirmation: str


class PaperAutonomyRequest(BaseModel):
    armed: bool


class TradierPreviewRequest(BaseModel):
    client_order_id: str = Field(min_length=1, max_length=32)
    strike_price: float = Field(gt=0)
    expiration_date: str
    option_type: Literal["CALL", "PUT"]
    position_intent: Literal["BUY_TO_OPEN", "SELL_TO_CLOSE"]
    quantity: int = Field(ge=1, le=100)
    limit_price: float = Field(gt=0, le=1000)


class LiveRiskEnvelopeRequest(BaseModel):
    daily_loss_limit: float = Field(gt=0, le=1_000_000)
    daily_gain_limit: float = Field(gt=0, le=1_000_000)
    max_account_exposure_pct: float = Field(gt=0, le=1)
    max_contracts: int = Field(ge=1, le=100)
    confirmation: str


def _default_paper_cash() -> float:
    raw = os.environ.get("PAPER_STARTING_CASH", "1000").strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError("PAPER_STARTING_CASH must be numeric") from exc
    if value <= 0:
        raise RuntimeError("PAPER_STARTING_CASH must be positive")
    return value


def _paper_db_path() -> str:
    explicit = os.environ.get("PAPER_DB_PATH", "").strip()
    if explicit:
        return explicit

    control_path = os.environ.get("CONTROL_DB_PATH", "").strip()
    if control_path:
        return str(Path(control_path).with_name("spy_paper.sqlite"))
    return "/data/spy_paper.sqlite"


def _paper_store() -> PaperAccountStore:
    return PaperAccountStore(
        _paper_db_path(),
        default_starting_cash=_default_paper_cash(),
    )


def _live_risk_db_path() -> str:
    explicit = os.environ.get("LIVE_RISK_DB_PATH", "").strip()
    if explicit:
        return explicit
    control_path = Path(os.environ.get("CONTROL_DB_PATH", "/data/spy_control.sqlite"))
    return str(control_path.with_name("spy_live_risk.sqlite"))


def _live_risk_store() -> LiveRiskEnvelopeStore:
    return LiveRiskEnvelopeStore(_live_risk_db_path())


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _selected_live_broker() -> str:
    return os.environ.get("LIVE_BROKER", "webull").strip().lower() or "webull"


def _paper_autonomy_payload() -> dict[str, Any]:
    """Describe the credential-free autonomous simulation control plane."""

    mode = base._control_store().get_mode()
    runner_enabled = _env_bool("START_PAPER_AUTOTRADER", False)
    armed = mode is base.TradingMode.PAPER
    return {
        "armed": armed,
        "mode": mode.value,
        "engine_running": runner_enabled,
        "execution": "local_paper_ledger",
        "broker_credentials_required": False,
        "live_order_submission": False,
        "production_isolated": True,
        "new_entries_enabled": runner_enabled and armed,
        "existing_position_management_enabled": runner_enabled,
        "dynamic_exits_enabled": runner_enabled,
        "disarmed_behavior": "block_new_entries_manage_existing_positions",
    }


def _live_alert_payload(automation: dict[str, Any]) -> dict[str, Any] | None:
    """Expose a bounded strategy alert without transmitting an order."""

    if base._control_store().get_mode() is not base.TradingMode.LIVE:
        return None
    envelope = _live_risk_store().snapshot()
    if not envelope.armed_today():
        return None
    guard = cached_live_broker_guard(envelope)
    return build_live_entry_alert(automation, envelope, guard)


_original_live_gate = base._live_gate


def _live_gate(store: base.ControlStore) -> dict[str, Any]:
    if _selected_live_broker() != "tradier":
        return _original_live_gate(store)

    reasons: list[str] = []
    status = tradier_env_status()
    if not status["token_configured"]:
        reasons.append("tradier_production_token_missing")
    if not status["account_configured"]:
        reasons.append("tradier_account_id_missing")

    envelope = _live_risk_store().snapshot()
    if not envelope.armed_today():
        reasons.append("daily_live_limits_not_armed")

    if _env_bool("APP_PREVIEW_MODE", False):
        reasons.append("owner_auth_required_for_live")

    if status["configured"] and envelope.armed_today():
        guard = cached_live_broker_guard(envelope)
        blocking_guard_reasons = {
            "broker_guard_unavailable",
            "broker_account_not_active",
            "broker_account_not_cash",
            "daily_loss_stop_reached",
            "daily_gain_stop_reached",
            "no_live_exposure_budget",
            "live_exposure_basis_unavailable",
        }
        reasons.extend(reason for reason in guard.reasons if reason in blocking_guard_reasons)
        if not guard.connected and "broker_guard_unavailable" not in reasons:
            reasons.append("broker_guard_unavailable")

    return {
        "ready": not reasons,
        "provider": "tradier",
        "execution": "notification_only",
        "reasons": list(dict.fromkeys(reasons)),
    }


base._live_gate = _live_gate
_original_status_payload = base.status_payload
_original_lifespan = base.app.router.lifespan_context


def status_payload() -> dict[str, Any]:
    envelope = _live_risk_store().snapshot()
    guard = cached_live_broker_guard(envelope) if envelope.armed_today() else None

    # Daily gain/loss stops are server-authoritative. If one is reached while
    # LIVE alerts are armed, immediately return the control plane to SHADOW.
    if (
        base._control_store().get_mode() is base.TradingMode.LIVE
        and guard is not None
        and guard.daily_stop_reached
    ):
        base._control_store().set_mode(base.TradingMode.SHADOW)

    payload = _original_status_payload()
    paper = _paper_store().snapshot().as_dict()
    automation = automation_status()
    payload["paper"] = paper
    payload["paper_automation"] = automation
    payload["paper_autonomy"] = _paper_autonomy_payload()
    payload["live_risk"] = envelope.as_dict()
    payload["live_alert"] = _live_alert_payload(automation)
    payload["live_broker"] = {
        **tradier_env_status(),
        "selected": _selected_live_broker() == "tradier",
        "real_order_submission": False,
        "workflow": "strategy_alert_then_owner_review",
        "guard": None if guard is None else guard.as_dict(),
    }

    decision = payload.get("decision")
    if isinstance(decision, dict):
        state = str(automation.get("state") or "STOPPED")
        decision["state"] = state
        decision["reason"] = str(
            automation.get("reason")
            or "autonomous paper/shadow decision loop is starting"
        )
    return payload


base.status_payload = status_payload


def _strategy_mode() -> str:
    mode = base._control_store().get_mode()
    # LIVE uses the same strategy/risk loop as SHADOW so decisions can be
    # surfaced to the phone without giving that loop broker-write capability.
    if mode is base.TradingMode.LIVE:
        return base.TradingMode.SHADOW.value
    return mode.value


@asynccontextmanager
async def lifespan(app):
    if _env_bool("START_PAPER_AUTOTRADER", False):
        runner = (
            run_delayed_paper_autotrader
            if _env_bool("PAPER_DELAYED_SIMULATION", False)
            else run_realtime_paper_autotrader
        )
        thread = threading.Thread(
            target=runner,
            args=(_strategy_mode,),
            name="spy0dte-paper-autotrader",
            daemon=True,
        )
        thread.start()

    async with _original_lifespan(app):
        yield


base.app.router.lifespan_context = lifespan
app = base.app


@app.get("/v1/paper/account")
def paper_account(_: base.UserIdentity = Depends(base.require_user)) -> dict[str, Any]:
    return _paper_store().snapshot().as_dict()


@app.get("/v1/paper/positions")
def paper_positions(_: base.UserIdentity = Depends(base.require_user)) -> dict[str, Any]:
    return {"positions": _paper_store().positions()}


@app.get("/v1/paper/trades")
def paper_trades(
    limit: int = Query(default=20, ge=1, le=100),
    _: base.UserIdentity = Depends(base.require_user),
) -> dict[str, Any]:
    return {"trades": _paper_store().recent_trades(limit)}


@app.get("/v1/paper/automation")
def paper_automation(_: base.UserIdentity = Depends(base.require_user)) -> dict[str, Any]:
    return automation_status()


@app.get("/v1/paper/autonomy")
def paper_autonomy(_: base.UserIdentity = Depends(base.require_user)) -> dict[str, Any]:
    return _paper_autonomy_payload()


@app.post("/v1/paper/autonomy")
def set_paper_autonomy(
    request: PaperAutonomyRequest,
    _: base.UserIdentity = Depends(base.require_user),
) -> dict[str, Any]:
    store = base._control_store()
    store.set_mode(base.TradingMode.PAPER if request.armed else base.TradingMode.SHADOW)
    return _paper_autonomy_payload()


@app.post("/v1/paper/reset")
def reset_paper_account(
    request: PaperResetRequest,
    _: base.UserIdentity = Depends(base.require_user),
) -> dict[str, Any]:
    control = base._control_store()
    if control.get_mode() is base.TradingMode.LIVE:
        raise HTTPException(status_code=409, detail="paper account cannot be reset while LIVE mode is selected")
    if request.confirmation != "RESET PAPER ACCOUNT":
        raise HTTPException(status_code=400, detail="explicit paper-account reset confirmation required")
    return _paper_store().reset(request.starting_cash).as_dict()


@app.get("/v1/live/risk-envelope")
def live_risk_envelope(_: base.UserIdentity = Depends(base.require_user)) -> dict[str, Any]:
    return _live_risk_store().snapshot().as_dict()


@app.post("/v1/live/risk-envelope")
def arm_live_risk_envelope(
    request: LiveRiskEnvelopeRequest,
    _: base.UserIdentity = Depends(base.require_user),
) -> dict[str, Any]:
    try:
        envelope = _live_risk_store().arm(
            daily_loss_limit=request.daily_loss_limit,
            daily_gain_limit=request.daily_gain_limit,
            max_account_exposure_pct=request.max_account_exposure_pct,
            max_contracts=request.max_contracts,
            explicit_confirmation=request.confirmation,
        )
    except LiveRiskEnvelopeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return envelope.as_dict()


@app.post("/v1/live/risk-envelope/disarm")
def disarm_live_risk_envelope(
    _: base.UserIdentity = Depends(base.require_user),
) -> dict[str, Any]:
    if base._control_store().get_mode() is base.TradingMode.LIVE:
        base._control_store().set_mode(base.TradingMode.SHADOW)
    return _live_risk_store().disarm().as_dict()


@app.get("/v1/broker/tradier/readiness")
def tradier_readiness(_: base.UserIdentity = Depends(base.require_user)) -> dict[str, Any]:
    status = tradier_env_status()
    result: dict[str, Any] = {**status, "connected": False}
    if not status["configured"]:
        return result

    envelope = _live_risk_store().snapshot()
    guard = cached_live_broker_guard(envelope)
    result["connected"] = guard.connected
    result["guard"] = guard.as_dict()
    return result


@app.post("/v1/broker/tradier/preview")
def preview_tradier_option_order(
    request: TradierPreviewRequest,
    _: base.UserIdentity = Depends(base.require_user),
) -> dict[str, Any]:
    account_id = os.environ.get("TRADIER_ACCOUNT_ID", "").strip()
    if not account_id:
        raise HTTPException(status_code=409, detail="Tradier account is not configured")

    envelope = _live_risk_store().snapshot()
    if not envelope.armed_today():
        raise HTTPException(status_code=409, detail="daily live limits are not armed")

    if request.position_intent == "BUY_TO_OPEN":
        guard = cached_live_broker_guard(envelope, ttl_seconds=0.0)
        if not guard.entry_allowed:
            raise HTTPException(
                status_code=409,
                detail={"message": "new live entry is blocked", **guard.as_dict()},
            )
        if envelope.max_contracts is not None and request.quantity > envelope.max_contracts:
            raise HTTPException(status_code=409, detail="quantity exceeds today's contract ceiling")
        debit = request.limit_price * 100.0 * request.quantity
        if guard.max_entry_debit is None or debit > guard.max_entry_debit + 1e-9:
            raise HTTPException(status_code=409, detail="order exceeds today's account-exposure ceiling")

    order = OptionOrderRequest(
        account_id=account_id,
        client_order_id=request.client_order_id,
        underlying="SPY",
        strike_price=request.strike_price,
        expiration_date=request.expiration_date,
        option_type=request.option_type,
        side="BUY" if request.position_intent == "BUY_TO_OPEN" else "SELL",
        position_intent=request.position_intent,
        quantity=request.quantity,
        limit_price=request.limit_price,
    )
    try:
        payload = TradierLiveClient.from_env().preview_option_order(order)
    except TradierLiveError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "provider": "tradier",
        "preview": True,
        "submitted": False,
        "result": payload,
    }
