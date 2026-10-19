from __future__ import annotations

import os
import re
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
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
from .live_autonomy import execution_blocks, live_status, run_forever as run_live
from .live_autonomy_store import ARM_AUTONOMY, LiveAutonomyStore, autonomy_db_path
from .runtime import start_worker
from .webull_live import (
    WebullLiveClient,
    WebullLiveError,
    cached_webull_guard,
    webull_env_status,
    selected_webull_account_id,
    save_selected_webull_account_id,
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
    standing = LiveAutonomyStore(autonomy_db_path()).policy()
    if standing["configured"]:
        live = live_status()
        reasons = execution_blocks()
        if not standing["enabled"]:
            reasons.append("autonomous_live_disabled")
        if not live["worker_current"]:
            reasons.append("live_worker_recovering")
        if not live.get("broker_connected"):
            reasons.append("broker_guard_unavailable")
        if live["state"] in {"DAILY_HALT", "RECOVERING", "POSITION_RECONCILIATION", "ORDER_UNKNOWN", "UNMANAGED_POSITION", "MARKET_CLOSED"}:
            reasons.append(live["state"].lower())
        return {"ready": not reasons, "provider": "webull", "execution": "autonomous",
                "reasons": list(dict.fromkeys(reasons)), "guard": None}
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
        # Legacy CONFIRMED trade-button remains V4-limits-only.
        # The independent V5 autonomous owner-toggle path skips these limits.
        reasons.append("standing_live_safety_not_configured")
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
    live = live_status()
    envelope = api_v2._live_risk_store().snapshot()
    guard = cached_webull_guard(envelope) if envelope.armed_today() and not live["configured"] else None
    gate = _webull_live_gate(base._control_store())

    payload["live_gate"] = gate
    payload["live_broker"] = {
        **webull_env_status(),
        "selected": _selected_broker() == "webull",
        "connected": bool(live.get("broker_connected") and live["worker_current"]) if live["configured"] else bool(guard and guard.connected),
        "real_order_submission": (
            not _env_bool("APP_PREVIEW_MODE", False)
            and _env_bool("ALLOW_LIVE_ORDERS", False)
            and _env_bool("LIVE_ORDER_EXECUTOR_READY", False)
        ),
        "autonomous_order_submission": live["enabled"] and not execution_blocks(),
        "workflow": "server_managed_entry_fill_reconciliation_and_exit",
        "owner_auth_required": _env_bool("APP_PREVIEW_MODE", False),
        "guard": None if guard is None else guard.as_dict(),
    }
    from .v4_operational import spy_chart_5m, spy_chart_30m
    payload["spy_chart_30m"] = spy_chart_30m()
    payload["spy_chart_5m"] = spy_chart_5m()
    payload["live_autonomy"] = live
    v5_unrestricted=os.environ.get("LIVE_STRATEGY_PROFILE","").strip().lower()=="v5_close_only_unrestricted_research"
    payload["live_strategy_profile"]="v5_close_only_unrestricted_research" if v5_unrestricted else "v4_original_3x"
    if live["configured"]:
        limits = LiveAutonomyStore(autonomy_db_path()).envelope(datetime.now(timezone.utc))
        if v5_unrestricted:
            payload["live_risk"] = {
                **limits.as_dict(), "armed_today": True, "configured": True,
                "standing_safety_limits": False,
                "daily_loss_limit": None, "daily_gain_limit": None,
                "max_contracts": None, "max_account_exposure_pct": None,
                "standing_authorization": live["enabled"],
                "sizing": "SETTLED_CASH_AND_VERIFIED_NBBO_DEPTH_ONLY",
            }
        else:
            payload["live_risk"] = {**limits.as_dict(), "armed_today": limits.is_complete, "standing_authorization": live["enabled"]}
        payload["live_alert"] = None
    if payload.get("mode") == "LIVE":
        payload["decision"] = {"state": live.get("state", "RECOVERING"), "reason": live.get("reason") or "awaiting live worker decision"}
    return payload


base.status_payload = status_payload
app = api_v2.app
_previous_lifespan = app.router.lifespan_context


@asynccontextmanager
async def lifespan(app):
    worker = start_worker("live", lambda context: run_live(lambda: base._control_store().get_mode().value, context))
    notifier = None
    # Slack is observational. Never start until the owner has separately
    # configured all three destinations and enabled it in Railway.
    if _env_bool("START_SLACK_ALERTS", False):
        from .slack_alerts import VARS, run_forever as run_slack_alerts
        if all(os.environ.get(var, "").strip() for var in VARS.values()):
            notifier = start_worker("slack", run_slack_alerts)
    try:
        async with _previous_lifespan(app):
            yield
    finally:
        if notifier is not None:
            notifier.close()
        worker.close()


app.router.lifespan_context = lifespan


class LiveAutonomyRequest(BaseModel):
    enabled: bool
    confirmation: Literal["ENABLE AUTONOMOUS LIVE", "STOP AUTONOMOUS LIVE"]


@app.get("/v1/live/decisions")
def get_live_decisions(
    limit: int = 100, _: base.UserIdentity = Depends(base.require_user)
):
    from .live_decision_audit import recent_decisions
    return {"decisions": recent_decisions(limit)}


class ExecutionCapabilityCheck(BaseModel):
    confirmation: Literal["VERIFY LIVE PREVIEW ONLY"]


@app.post("/v1/live/execution-check")
def check_live_execution(
    request: ExecutionCapabilityCheck,
    user: base.UserIdentity = Depends(base.require_user),
) -> dict[str, Any]:
    """Owner-triggered, NON-EXECUTING Webull option-order preview.

    Never calls place_order, never reserves an order ID, and never modifies
    LIVE/PAPER permissions. A successful preview does NOT prove an order will
    fill or that the live algorithm has a qualifying signal.
    """
    from .live_autonomy import account_values
    from .shared_live_quotes import collector_live_quotes
    from .session_calendar import EASTERN, session_bounds
    from .webull import build_option_order_item
    from datetime import time as wall_time

    _require_real_owner(user)
    now = datetime.now(timezone.utc)
    checks = {
        "live_armed": bool(live_status().get("enabled")),
        "live_worker_heartbeat": bool(live_status().get("worker_current")),
        "mode_live": base._control_store().get_mode() is base.TradingMode.LIVE,
        "execution_environment": not bool(execution_blocks()),
    }
    result: dict[str, Any] = {
        "checked_at": now.isoformat(),
        "order_submitted": False,
        "broker_order_acknowledged": False,
        "preview_only": True,
        "checks": checks,
        "execution_blockers": execution_blocks(),
        "broker_preview_accepted": False,
        "status": "NOT_PROVEN",
    }
    if not all(checks.values()):
        result["status"] = "BLOCKED_BY_LIVE_PREREQUISITES"
        return result

    eastern = now.astimezone(EASTERN)
    bounds = session_bounds(eastern.date(), wall_time(9,30), wall_time(16))
    if bounds is None or not bounds[0] <= eastern < bounds[1] or (bounds[1]-eastern).total_seconds() < 20*60:
        result["status"] = "OUTSIDE_LIVE_ENTRY_WINDOW"
        return result
    quotes = collector_live_quotes(
        os.environ.get("COLLECTOR_DB_PATH", "/data/spy_0dte.sqlite"),
        now, max_age_seconds=3.0,
    )
    eligible = []
    for q in quotes:
        try:
            if q.expiration != eastern.date() or q.greeks is None:
                continue
            if q.bid<=0 or q.ask<=q.bid or q.bid_size<1 or q.ask_size<1:
                continue
            if not .40 <= abs(float(q.greeks.delta)) <= .60:
                continue
            if (q.ask-q.bid)/((q.ask+q.bid)/2) > .03:
                continue
            _, expiry, option_type, strike = parse_occ_symbol(q.option_symbol)
            if expiry != eastern.date().isoformat():
                continue
            eligible.append((q,option_type,strike))
        except (TypeError,ValueError,ZeroDivisionError,OverflowError):
            continue
    result["fresh_option_contracts"] = len(quotes)
    result["eligible_preview_contracts"] = len(eligible)
    if not eligible:
        result["status"] = "NO_FRESH_ELIGIBLE_OPTION_QUOTE"
        return result

    client = WebullLiveClient.from_env()
    try:
        profile = client.account_profile()
        if not profile.is_cash:
            result["status"] = "BROKER_ACCOUNT_NOT_CASH"
            return result
        account = account_values(client.balances(profile.account_id))
        result["verified_settled_cash"] = round(account["settled_cash"],2)
        # Cheapest contract passing the same option quality and provenance
        # gates as V5. Preview is never an algorithm entry recommendation.
        q,option_type,strike = min(eligible,key=lambda x:x[0].ask)
        price = round(float(q.ask)+.00000001,2)
        result["minimum_one_contract_debit_usd"] = round(price*100+1.0,2)
        result["contract_type"] = option_type
        if account["settled_cash"] < result["minimum_one_contract_debit_usd"]:
            result["status"] = "INSUFFICIENT_SETTLED_BUYING_POWER"
            return result
        order = OptionOrderRequest(
            account_id=profile.account_id,client_order_id=uuid.uuid4().hex,
            underlying="SPY",strike_price=strike,
            expiration_date=eastern.date().isoformat(),
            option_type=option_type,side="BUY",
            position_intent="BUY_TO_OPEN",quantity=1,limit_price=price,
        )
        # Validate schema locally, then invoke the broker's official
        # *preview* endpoint. NEVER invoke place_option_order here.
        payload = build_option_order_item(order)
        if payload["order_type"] != "LIMIT" or payload["time_in_force"] != "DAY":
            result["status"] = "UNSUPPORTED_ORDER_SCHEMA"
            return result
        client.preview_option_order(order)
    except (WebullLiveError, RuntimeError, ValueError, TypeError, OSError) as exc:
        result["status"] = "BROKER_PREVIEW_FAILED"
        result["broker_error_type"] = type(exc).__name__
        result["broker_http_status"] = getattr(exc,"http_status",None)
        return result
    result["broker_preview_accepted"] = True
    result["status"] = "BROKER_PREVIEW_ACCEPTED_NO_REAL_ORDER"
    return result


@app.get("/v1/live/autonomy")
def get_live_autonomy(_: base.UserIdentity = Depends(base.require_user)):
    return live_status()


@app.post("/v1/live/autonomy")
def set_live_autonomy(request: LiveAutonomyRequest, user: base.UserIdentity = Depends(base.require_user)):
    _require_real_owner(user)
    store = LiveAutonomyStore(autonomy_db_path())
    now = datetime.now(timezone.utc)
    if request.enabled:
        v5_unrestricted=os.environ.get("LIVE_STRATEGY_PROFILE","").strip().lower()=="v5_close_only_unrestricted_research"
        saved_limits=api_v2._live_risk_store().snapshot()
        if not v5_unrestricted and not saved_limits.is_complete:
            raise HTTPException(status_code=400, detail="Save live limits in Settings before turning Auto trade on")
        try:
            store.enable(owner=user.subject, limits=saved_limits,
                         unrestricted_v5=v5_unrestricted,
                         confirmation=request.confirmation, now=now,
                         account_id=selected_webull_account_id())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        base._control_store().set_mode(base.TradingMode.LIVE)
    else:
        if request.confirmation != "STOP AUTONOMOUS LIVE":
            raise HTTPException(status_code=400, detail="explicit stop confirmation required")
        store.disable(now)
    return {**live_status(), "prerequisites": execution_blocks()}


def _require_real_owner(user: base.UserIdentity) -> None:
    if _env_bool("APP_PREVIEW_MODE", False) or user.subject == "preview":
        raise HTTPException(status_code=403, detail="owner sign-in is required for a live broker action")


def _require_live_ready() -> None:
    if LiveAutonomyStore(autonomy_db_path()).policy()["configured"]:
        raise HTTPException(status_code=409, detail="this account is managed by the autonomous executor")
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


class SelectWebullCashAccount(BaseModel):
    account_id: str = Field(min_length=1, max_length=128)
    confirmation: Literal["SELECT WEBULL CASH ACCOUNT"]


@app.get("/v1/broker/webull/accounts")
def list_webull_cash_accounts(user: base.UserIdentity = Depends(base.require_user)):
    _require_real_owner(user)
    if not webull_env_status()["configured"]:
        raise HTTPException(status_code=409, detail="Configure production Webull credentials first")
    try:
        entries = WebullLiveClient.from_env().accounts()
        accounts = []
        for entry in entries:
            account_id = str(entry.get("account_id") or "").strip()
            if not account_id:
                continue
            account_type = str(entry.get("account_type") or "UNKNOWN").upper()
            account_number = str(entry.get("account_number") or account_id)
            accounts.append({
                "account_id": account_id,
                "label": f"{account_type} account ending {account_number[-4:]}",
                "account_type": account_type,
                "selected": account_id == selected_webull_account_id(),
            })
        return {"accounts": accounts}
    except (WebullLiveError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="Webull account lookup failed; check Webull OpenAPI permissions") from exc


@app.post("/v1/broker/webull/select-account")
def select_webull_cash_account(request: SelectWebullCashAccount,
                               user: base.UserIdentity = Depends(base.require_user)):
    _require_real_owner(user)
    store = LiveAutonomyStore(autonomy_db_path())
    policy = store.policy()
    if policy["enabled"] or store.positions() or store.orders(pending_only=True):
        raise HTTPException(status_code=409, detail="Turn Auto Trade off and clear live managed positions/orders first")
    pinned = str(policy.get("account_id") or "")
    if pinned and pinned != request.account_id:
        raise HTTPException(status_code=409, detail="A different account is pinned to live authorization. Contact support for a verified reset.")
    if not webull_env_status()["configured"]:
        raise HTTPException(status_code=409, detail="Configure production Webull credentials first")
    try:
        client = WebullLiveClient.from_env()
        matches = [row for row in client.accounts() if str(row.get("account_id") or "") == request.account_id]
        if len(matches) != 1 or str(matches[0].get("account_type") or "").upper() != "CASH":
            raise HTTPException(status_code=422, detail="Select one accessible Webull CASH account")
        # No account selection is allowed with outstanding broker orders.
        if client.open_orders(request.account_id):
            raise HTTPException(status_code=409, detail="Selected Webull account has open orders; cancel or finish them first")
        save_selected_webull_account_id(request.account_id)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (WebullLiveError, RuntimeError) as exc:
        raise HTTPException(status_code=502, detail="Webull account verification failed; no selection saved") from exc
    return {"selected": True, "label": "Webull CASH account selected",
            "message": "Account saved; wait for broker connection to become ready before enabling Auto Trade"}


@app.get("/v1/broker/webull/readiness")
def webull_readiness(user: base.UserIdentity = Depends(base.require_user)) -> dict[str, Any]:
    status = webull_env_status()
    result: dict[str, Any] = {**status, "connected": False, "account": None}
    live = live_status()
    if live["configured"]:
        return {**result, "connected": bool(live.get("broker_connected") and live["worker_current"]),
                "autonomy_state": live["state"], "owner_authenticated": user.subject != "preview" and not _env_bool("APP_PREVIEW_MODE")}
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
        result["error"] = f"{type(exc).__name__}: broker connection unavailable"
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
