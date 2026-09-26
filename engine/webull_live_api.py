from __future__ import annotations

import os
import re
import uuid
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

from . import api as base
from . import api_v2
from .broker import OptionOrderRequest
from .live_alert_policy import build_live_entry_alert
from .live_order_ticket import (
    CONFIRMATION_PHRASE,
    LiveOrderAuthorizationError,
    LiveOrderTicketStore,
)
from .paper_autotrader import automation_status
from .webull_live import (
    WebullLiveClient,
    WebullLiveError,
    cached_webull_guard,
    webull_env_status,
)


class PrepareLiveOrderRequest(BaseModel):
    confirmation: Literal["PREPARE LIVE ORDER"]


class ExactLiveOrder(BaseModel):
    account_id: str = Field(min_length=1, max_length=128)
    client_order_id: str = Field(min_length=1, max_length=32)
    underlying: Literal["SPY"]
    strike_price: float = Field(gt=0)
    expiration_date: str
    option_type: Literal["CALL", "PUT"]
    side: Literal["BUY", "SELL"]
    position_intent: Literal["BUY_TO_OPEN", "SELL_TO_CLOSE"]
    quantity: int = Field(ge=1, le=100)
    limit_price: float = Field(gt=0, le=1000)


class SubmitLiveOrderRequest(BaseModel):
    ticket_token: str = Field(min_length=10, max_length=512)
    order: ExactLiveOrder
    confirmation: Literal["CONFIRM TRADE"]


_OCC_RE = re.compile(r"^([A-Z]{1,6})(\d{6})([CP])(\d{8})$")


def parse_occ_symbol(symbol: str) -> tuple[str, str, str, float]:
    match = _OCC_RE.fullmatch(symbol.strip().upper())
    if match is None:
        raise ValueError("live alert does not contain a valid OCC option symbol")
    underlying, yymmdd, right, strike_raw = match.groups()
    year = 2000 + int(yymmdd[:2])
    expiration = f"{year:04d}-{yymmdd[2:4]}-{yymmdd[4:6]}"
    option_type = "CALL" if right == "C" else "PUT"
    return underlying, expiration, option_type, int(strike_raw) / 1000.0


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _selected_broker() -> str:
    return os.environ.get("LIVE_BROKER", "webull").strip().lower() or "webull"


def _max_feed_delay() -> float:
    return max(0.25, float(os.environ.get("LIVE_MAX_FEED_DELAY_SECONDS", "5")))


def _max_data_age() -> float:
    return max(0.25, float(os.environ.get("LIVE_MAX_DATA_AGE_SECONDS", "5")))


def _market_reasons() -> list[str]:
    market = base._market_status()
    reasons: list[str] = []
    data_age = market.get("data_age_seconds")
    feed_delay = market.get("feed_delay_seconds")
    if data_age is None or float(data_age) > _max_data_age():
        reasons.append("live_market_data_stale")
    if feed_delay is None or float(feed_delay) > _max_feed_delay():
        reasons.append("realtime_option_data_required")
    if str(market.get("timestamp_quality") or "") == "receive_time_only":
        reasons.append("live_market_timestamp_untrusted")
    return reasons


def _webull_live_gate(_: base.ControlStore) -> dict[str, Any]:
    reasons: list[str] = []
    if _selected_broker() != "webull":
        reasons.append("webull_not_selected")
    if not _env_bool("ALLOW_LIVE_ORDERS", False):
        reasons.append("live_orders_disabled")
    if not _env_bool("LIVE_ORDER_EXECUTOR_READY", False):
        reasons.append("live_order_executor_not_ready")
    if _env_bool("APP_PREVIEW_MODE", False):
        reasons.append("owner_auth_required_for_live")

    broker = webull_env_status()
    if not broker["configured"]:
        reasons.append("webull_production_credentials_missing")

    envelope = api_v2._live_risk_store().snapshot()
    if not envelope.armed_today():
        reasons.append("daily_live_limits_not_armed")
    reasons.extend(_market_reasons())

    guard = None
    if broker["configured"] and envelope.armed_today():
        guard = cached_webull_guard(envelope)
        reasons.extend(guard.reasons)
        if not guard.connected and "broker_guard_unavailable" not in reasons:
            reasons.append("broker_guard_unavailable")

    return {
        "ready": not reasons,
        "provider": "webull",
        "execution": "confirmed_trade_button",
        "reasons": list(dict.fromkeys(reasons)),
        "guard": None if guard is None else guard.as_dict(),
    }


# api_v2 owns the PAPER/SHADOW engine and live-alert construction. Swap only its
# broker-state reader so the strategy remains unchanged while the live boundary
# moves from Tradier to Webull.
api_v2.cached_live_broker_guard = cached_webull_guard
base._live_gate = _webull_live_gate
_original_status_payload = base.status_payload


def status_payload() -> dict[str, Any]:
    payload = _original_status_payload()
    envelope = api_v2._live_risk_store().snapshot()
    guard = cached_webull_guard(envelope) if envelope.armed_today() else None
    gate = _webull_live_gate(base._control_store())

    payload["live_gate"] = gate
    payload["live_broker"] = {
        **webull_env_status(),
        "selected": _selected_broker() == "webull",
        "connected": bool(guard and guard.connected),
        "real_order_submission": (
            not _env_bool("APP_PREVIEW_MODE", False)
            and _env_bool("ALLOW_LIVE_ORDERS", False)
            and _env_bool("LIVE_ORDER_EXECUTOR_READY", False)
        ),
        "autonomous_order_submission": False,
        "workflow": "railway_signal_then_trade_button_confirmation",
        "owner_auth_required": _env_bool("APP_PREVIEW_MODE", False),
        "guard": None if guard is None else guard.as_dict(),
    }
    return payload


base.status_payload = status_payload
app = api_v2.app


def _require_real_owner(user: base.UserIdentity) -> None:
    if _env_bool("APP_PREVIEW_MODE", False) or user.subject == "preview":
        raise HTTPException(status_code=403, detail="owner sign-in is required for a live broker action")


def _require_live_ready() -> None:
    if base._control_store().get_mode() is not base.TradingMode.LIVE:
        raise HTTPException(status_code=409, detail="LIVE mode is not selected")
    gate = _webull_live_gate(base._control_store())
    if not gate["ready"]:
        raise HTTPException(status_code=409, detail={"message": "live trading is not ready", **gate})


def _ticket_store() -> LiveOrderTicketStore:
    explicit = os.environ.get("LIVE_TICKET_DB_PATH", "").strip()
    if explicit:
        path = explicit
    else:
        control = Path(os.environ.get("CONTROL_DB_PATH", "/data/spy_control.sqlite"))
        path = str(control.with_name("spy_live_tickets.sqlite"))
    return LiveOrderTicketStore(path)


def _ticket_ttl() -> int:
    ttl = int(os.environ.get("LIVE_CONFIRMATION_TTL_SECONDS", "45"))
    if ttl < 5 or ttl > 120:
        raise HTTPException(status_code=503, detail="LIVE_CONFIRMATION_TTL_SECONDS must be 5..120")
    return ttl


def _order_dict(order: OptionOrderRequest) -> dict[str, Any]:
    return {
        "account_id": order.account_id,
        "client_order_id": order.client_order_id,
        "underlying": order.underlying,
        "strike_price": order.strike_price,
        "expiration_date": order.expiration_date,
        "option_type": order.option_type,
        "side": order.side,
        "position_intent": order.position_intent,
        "quantity": order.quantity,
        "limit_price": order.limit_price,
        "max_debit": round(order.limit_price * 100.0 * order.quantity, 2),
    }


def _order_from_model(model: ExactLiveOrder) -> OptionOrderRequest:
    return OptionOrderRequest(
        account_id=model.account_id,
        client_order_id=model.client_order_id,
        underlying=model.underlying,
        strike_price=model.strike_price,
        expiration_date=model.expiration_date,
        option_type=model.option_type,
        side=model.side,
        position_intent=model.position_intent,
        quantity=model.quantity,
        limit_price=model.limit_price,
    )


def _current_entry_order(client: WebullLiveClient) -> tuple[OptionOrderRequest, dict[str, Any]]:
    envelope = api_v2._live_risk_store().snapshot()
    guard = cached_webull_guard(envelope, ttl_seconds=0.0)
    if not guard.entry_allowed:
        raise HTTPException(
            status_code=409,
            detail={"message": "new live entry is blocked", **guard.as_dict()},
        )

    automation = automation_status()
    alert = build_live_entry_alert(automation, envelope, guard)
    if alert is None:
        raise HTTPException(status_code=409, detail="there is no current qualified live strategy alert")
    signal = alert.get("signal")
    risk = alert.get("risk")
    if not isinstance(signal, dict) or not isinstance(risk, dict):
        raise HTTPException(status_code=409, detail="live strategy alert is incomplete")

    symbol = str(signal.get("symbol") or "")
    try:
        underlying, expiration, option_type, strike = parse_occ_symbol(symbol)
        ask = round(float(signal.get("ask") or 0.0), 2)
        quantity = int(risk.get("contracts") or 0)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=f"live strategy alert is invalid: {exc}") from exc
    if underlying != "SPY" or ask <= 0 or quantity < 1:
        raise HTTPException(status_code=409, detail="live strategy alert cannot form a valid SPY option order")

    profile = client.account_profile()
    if not profile.is_cash:
        raise HTTPException(status_code=409, detail="live trading is restricted to a Webull cash account")
    debit = ask * 100.0 * quantity
    if guard.max_entry_debit is None or debit > guard.max_entry_debit + 1e-9:
        raise HTTPException(status_code=409, detail="current order exceeds the armed account-exposure ceiling")
    if envelope.max_contracts is None or quantity > envelope.max_contracts:
        raise HTTPException(status_code=409, detail="current order exceeds today's contract ceiling")

    return (
        OptionOrderRequest(
            account_id=profile.account_id,
            client_order_id=uuid.uuid4().hex,
            underlying="SPY",
            strike_price=strike,
            expiration_date=expiration,
            option_type=option_type,  # type: ignore[arg-type]
            side="BUY",
            position_intent="BUY_TO_OPEN",
            quantity=quantity,
            limit_price=ask,
        ),
        alert,
    )


@app.get("/v1/broker/webull/readiness")
def webull_readiness(user: base.UserIdentity = Depends(base.require_user)) -> dict[str, Any]:
    status = webull_env_status()
    result: dict[str, Any] = {**status, "connected": False, "account": None}
    if not status["configured"]:
        return result
    try:
        client = WebullLiveClient.from_env()
        profile = client.account_profile()
        result["connected"] = True
        result["account"] = profile.as_dict()
        envelope = api_v2._live_risk_store().snapshot()
        if envelope.armed_today():
            result["guard"] = cached_webull_guard(envelope, ttl_seconds=0.0).as_dict()
    except (WebullLiveError, RuntimeError, ValueError) as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    result["owner_authenticated"] = user.subject != "preview" and not _env_bool("APP_PREVIEW_MODE", False)
    return result


@app.post("/v1/live/order/prepare")
def prepare_live_order(
    request: PrepareLiveOrderRequest,
    user: base.UserIdentity = Depends(base.require_user),
) -> dict[str, Any]:
    _require_real_owner(user)
    if request.confirmation != "PREPARE LIVE ORDER":
        raise HTTPException(status_code=400, detail="explicit order preparation confirmation required")
    _require_live_ready()

    try:
        client = WebullLiveClient.from_env()
        order, alert = _current_entry_order(client)
        preview = client.preview_option_order(order)
        ttl = _ticket_ttl()
        token = _ticket_store().issue(
            order,
            user_subject=user.subject,
            explicit_confirmation=CONFIRMATION_PHRASE,
            ttl_seconds=ttl,
        )
    except (WebullLiveError, LiveOrderAuthorizationError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {
        "provider": "webull",
        "preview": preview,
        "order": _order_dict(order),
        "ticket_token": token,
        "expires_in_seconds": ttl,
        "strategy_alert": alert,
        "submitted": False,
    }


@app.post("/v1/live/order/submit")
def submit_live_order(
    request: SubmitLiveOrderRequest,
    user: base.UserIdentity = Depends(base.require_user),
) -> dict[str, Any]:
    _require_real_owner(user)
    if request.confirmation != "CONFIRM TRADE":
        raise HTTPException(status_code=400, detail="explicit trade confirmation required")
    _require_live_ready()

    try:
        order = _order_from_model(request.order)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if order.position_intent != "BUY_TO_OPEN" or order.side != "BUY":
        raise HTTPException(status_code=400, detail="this trade button only submits the prepared long-option entry")

    try:
        client = WebullLiveClient.from_env()
        profile = client.account_profile()
        if profile.account_id != order.account_id or not profile.is_cash:
            raise HTTPException(status_code=409, detail="prepared order no longer matches the active Webull cash account")

        # Reconcile immediately before consuming the one-time authorization.
        envelope = api_v2._live_risk_store().snapshot()
        guard = cached_webull_guard(envelope, ttl_seconds=0.0)
        if not guard.entry_allowed:
            raise HTTPException(status_code=409, detail={"message": "entry became ineligible", **guard.as_dict()})
        debit = order.limit_price * 100.0 * order.quantity
        if guard.max_entry_debit is None or debit > guard.max_entry_debit + 1e-9:
            raise HTTPException(status_code=409, detail="prepared order now exceeds available live exposure")

        authorization = _ticket_store().consume(
            request.ticket_token,
            order,
            user_subject=user.subject,
        )

        try:
            broker_result = client.place_confirmed_option_order(order)
            detail = client.order_detail(order.account_id, order.client_order_id)
        except WebullLiveError as submit_error:
            # Never blindly retry a timed-out/uncertain production POST. Query the
            # exact client order id first; a returned order means Webull received it.
            recovered = client.order_detail(order.account_id, order.client_order_id)
            if recovered is None:
                raise submit_error
            broker_result = {"recovered_after_submit_error": True, "error": str(submit_error)}
            detail = recovered
    except HTTPException:
        raise
    except LiveOrderAuthorizationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (WebullLiveError, RuntimeError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {
        "provider": "webull",
        "submitted": True,
        "order": _order_dict(order),
        "authorization": {
            "ticket_id": authorization.ticket_id,
            "consumed_at": authorization.consumed_at,
        },
        "broker_result": broker_result,
        "order_detail": detail,
    }
