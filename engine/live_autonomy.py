"""Single-writer autonomous execution, isolated from paper orders and clocks."""
from __future__ import annotations

import logging
import math
import os
import threading
import time
import uuid
from datetime import datetime, time as wall_time, timezone
from typing import Any

from .broker import OptionOrderRequest
from .live_autonomy_store import LiveAutonomyStore, autonomy_db_path
from .live_market import LiveDataProvider, LiveMarketReader, parse_occ_symbol, position_symbol
from .live_risk import EASTERN
from .live_strategy import LiveStrategy
from .paper_runner import execution_lock
from .session_calendar import session_bounds
from .webull_live import WebullLiveClient, _balance_object, _number, _usd_asset, webull_env_status, production_credentials

log = logging.getLogger("spy0dte.live")
_status_lock = threading.Lock()
_status: dict[str, Any] = {"state": "STOPPED", "reason": "live worker has not started"}


def publish(**values):
    with _status_lock:
        _status.clear()
        _status.update(values, updated_at=datetime.now(timezone.utc).isoformat())


def live_status() -> dict[str, Any]:
    store = LiveAutonomyStore(autonomy_db_path())
    policy = store.policy()
    with _status_lock:
        result = dict(_status)
    updated = result.get("updated_at")
    healthy = updated is not None and (datetime.now(timezone.utc)-datetime.fromisoformat(updated)).total_seconds() < 15
    return {**result, "enabled": policy["enabled"], "configured": policy["configured"],
            "limits": policy.get("limits"),
            "worker_current": healthy, "resume_without_phone": True,
            "per_order_confirmation": False, "automatic_daily_resume": True,
            "positions": [{k:v for k,v in p.items() if k != "request"} for p in store.positions()],
            "pending_orders": [{k:v for k,v in o.items() if k != "request"} for o in store.orders(pending_only=True)]}


def env_bool(name, default=False):
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def execution_blocks() -> list[str]:
    reasons = []
    if env_bool("APP_PREVIEW_MODE"):
        reasons.append("owner_auth_required_for_live")
    if os.environ.get("LIVE_BROKER", "webull").lower() != "webull":
        reasons.append("webull_not_selected")
    if not env_bool("ALLOW_LIVE_ORDERS"):
        reasons.append("live_orders_disabled")
    if not env_bool("LIVE_ORDER_EXECUTOR_READY"):
        reasons.append("live_order_executor_not_ready")
    if not webull_env_status()["configured"]:
        reasons.append("webull_production_credentials_missing")
    return reasons


def finite(value, name):
    result = _number(value)
    if result is None:
        raise ValueError(f"broker {name} is unavailable")
    return result


def account_values(payload):
    balance = _balance_object(payload)
    usd = _usd_asset(balance)
    cash = finite(usd.get("settled_cash"), "settled cash")
    equity = finite(balance.get("total_net_liquidation_value"), "equity")
    pnl = finite(balance.get("total_day_profit_loss", usd.get("day_profit_loss")), "daily P&L")
    if equity <= 0:
        raise ValueError("broker equity must be positive")
    buying_power = _number(usd.get("buying_power"))
    return {"settled_cash": max(0.0, min(cash, buying_power) if buying_power is not None else cash),
            "equity": equity, "daily_pnl": pnl}


def order_rows(rows):
    result = []
    for row in rows:
        if isinstance(row.get("orders"), list):
            result.extend(order_rows(row["orders"]))
        elif row.get("client_order_id"):
            result.append(row)
        else:
            raise ValueError("unidentifiable broker order")
    return result


def parse_order_detail(payload, request):
    if not isinstance(payload, dict):
        raise ValueError("broker order outcome is unknown")
    rows = order_rows([payload])
    matching = [r for r in rows if r.get("client_order_id") == request["client_order_id"]]
    if len(rows) != 1 or len(matching) != 1:
        raise ValueError("broker returned a different order")
    row = matching[0]
    if row.get("side") != request["side"] or finite(row.get("total_quantity"), "order quantity") != request["quantity"]:
        raise ValueError("broker order does not match journal intent")
    if row.get("position_intent", request["position_intent"]) != request["position_intent"]:
        raise ValueError("broker order intent differs")
    filled = finite(row.get("filled_quantity"), "filled quantity")
    if not filled.is_integer():
        raise ValueError("fractional option fill")
    average = finite(row.get("filled_price"), "average fill price") if filled else 0.0
    return str(row.get("status", "")), int(filled), average


def broker_inventory(rows):
    known = {}
    foreign = False
    for row in rows:
        quantity = finite(row.get("quantity"), "position quantity")
        if not quantity:
            continue
        try:
            symbol = position_symbol(row)
            if not quantity.is_integer() or quantity < 0:
                raise ValueError("non-long position")
            legs = row.get("legs")
            if legs and "quantity" in legs[0] and finite(legs[0]["quantity"], "leg quantity") != quantity:
                raise ValueError("nonstandard position ratio")
            known[symbol] = known.get(symbol, 0) + int(quantity)
        except (ValueError, TypeError, KeyError):
            foreign = True
    return known, foreign


class LiveExecutor:
    """Dependency-injected for replay tests. Production creation is in run_forever."""
    def __init__(self, *, store, broker, market, provider, strategy, mode_getter,
                 writes_allowed=lambda: not execution_blocks(), clock=lambda: datetime.now(timezone.utc)):
        self.store, self.broker, self.market = store, broker, market
        self.provider, self.strategy = provider, strategy
        self.mode_getter, self.writes_allowed, self.clock = mode_getter, writes_allowed, clock
        self._account_read_at = None
        self._last_account = None

    def _account(self, account_id):
        now = self.clock()
        # At most one balance request per second (Webull permits two per two
        # seconds). A fast model pass can reuse the same sub-second snapshot.
        if self._account_read_at is None or (now-self._account_read_at).total_seconds() >= 1.05:
            self._last_account = account_values(self.broker.balances(account_id))
            self._account_read_at = now
        return self._last_account

    def telemetry(self, result):
        now = self.clock()
        quotes = [q for q in self.market.quotes.values() if self.market.fresh_quote(q.option_symbol, now) is not None]
        market = {} if not quotes else {
            "spot": quotes[0].underlying_price,
            "data_age_seconds": max((now-q.received_at).total_seconds() for q in quotes),
            "feed_delay_seconds": max((now-q.timestamp).total_seconds() for q in quotes),
            "source": "webull_production", "timestamp_quality": "option_quote_timestamp",
        }
        account = result.get("account")
        guard = None if account is None else {
            "connected": True, "entry_allowed": result["state"] == "SIGNAL",
            "cash_available": account["settled_cash"], "total_equity": account["equity"],
            "daily_total_pnl": account["daily_pnl"],
        }
        strategy = self.strategy.telemetry() if hasattr(self.strategy, "telemetry") else {}
        if not quotes or result["state"] not in {"SIGNAL", "NO_SIGNAL", "RISK_BLOCKED", "ORDER_SUBMITTED", "POSITION_OPEN"}:
            strategy = {k:v for k,v in strategy.items() if k not in {"ai_decision", "ai_advisory"}}
        signal = result.get("signal")
        alert = None if signal is None else {"signal": signal, "risk": result.get("risk", {})}
        return {**result, **strategy, "market": market, "guard": guard, "entry_alert": alert}

    def _reconcile(self, now, can_enter):
        pending = self.store.orders(pending_only=True)
        errors = []
        for order in pending:
            request = order["request"]
            try:
                detail = self.broker.order_detail(request["account_id"], order["client_id"])
                if detail is not None:
                    status, filled, average = parse_order_detail(detail, request)
                    self.store.reconcile(order["client_id"], status=status, filled=filled, average_price=average, now=now)
            except Exception as exc:
                errors.append(exc)
        for order in self.store.orders(pending_only=True):
            age = (now-datetime.fromisoformat(order["created_at"])).total_seconds()
            request = order["request"]
            cancel_due = age >= (15 if request["side"] == "BUY" else 10) or (request["side"] == "BUY" and not can_enter)
            cancel_at = order["cancel_at"]
            if cancel_due and (cancel_at is None or (now-datetime.fromisoformat(cancel_at)).total_seconds() >= 10):
                # Cancel the SAME id even after an ambiguous POST; still wait for
                # terminal confirmation before releasing any remaining quantity.
                self.store.mark_cancel(order["client_id"], now)
                self.broker.cancel_order(request["account_id"], order["client_id"])
        if errors:
            raise errors[0]

    def _submit(self, order, *, symbol, reason, signal_key, policy, now, position_id=None):
        if not self.writes_allowed():
            return {"state": "LIVE_LOCKED", "reason": "live execution disabled"}
        if order.side == "BUY" and self.mode_getter() != "LIVE":
            return {"state": "PAUSED", "reason": "LIVE mode is no longer selected"}
        now = self.clock()
        if self.market.fresh_quote(symbol, now) is None:
            return {"state": "WAITING_FRESH_DATA", "reason": "quote expired during final broker checks"}
        if order.side == "BUY" and self.strategy._minutes_to_close(now.astimezone(EASTERN)) <= 20:
            return {"state": "ENTRY_WINDOW_CLOSED", "reason": "0DTE entry window closed during final checks"}
        if not self.store.reserve(order, symbol=symbol, reason=reason, signal_key=signal_key,
                                  now=now, generation=policy["generation"], position_id=position_id):
            return {"state": "RECONCILING", "reason": "authorization changed or order intent already recorded"}
        try:
            self.broker.place_option_order(order)
        except Exception as exc:
            # The durable UNKNOWN order is the recovery state. Never POST again.
            log.warning("live submission outcome unknown type=%s", type(exc).__name__)
            return {"state": "ORDER_UNKNOWN", "reason": "reconciling the original client order id; new entries paused"}
        return {"state": "ORDER_SUBMITTED", "reason": reason, "client_order_id": order.client_order_id, "side": order.side}

    def tick(self):
        now = self.clock()
        policy = self.store.policy()
        if not policy["configured"]:
            return {"state": "DISABLED", "reason": "enable live automation once with owner-selected limits"}
        if not self.writes_allowed():
            return {"state": "LIVE_LOCKED", "reason": "live execution prerequisites are unavailable"}
        profile = self.broker.account_profile()
        if not profile.is_cash:
            return {"state": "LIVE_LOCKED", "reason": "a cash account is required"}
        self.store.pin_account(profile.account_id)
        eastern = now.astimezone(EASTERN)
        bounds = session_bounds(eastern.date(), wall_time(9,30), wall_time(16))
        minutes_left = -1 if bounds is None else (bounds[1]-eastern).total_seconds()/60
        in_session = bounds is not None and bounds[0] <= eastern < bounds[1]
        can_enter = policy["enabled"] and self.mode_getter() == "LIVE" and in_session and minutes_left > 20 and eastern.time() >= wall_time(9,45)
        self._reconcile(now, can_enter)
        pending = self.store.orders(pending_only=True)
        positions = self.store.positions()
        if pending:
            return {"state": "ORDER_PENDING" if pending[0]["status"] != "UNKNOWN" else "ORDER_UNKNOWN",
                    "reason": "waiting for broker confirmation of the original order", "broker_connected": True}
        if not in_session:
            return {"state": "MARKET_CLOSED", "reason": "waiting for the next exchange session", "broker_connected": True}

        # Account reads occur only here, at a two-second cadence, never per phone.
        inventory, foreign = broker_inventory(self.broker.positions(profile.account_id))
        open_orders = order_rows(self.broker.open_orders(profile.account_id))
        if open_orders:
            return {"state": "RECONCILING", "reason": "broker reports a working order; waiting for account reconciliation", "broker_connected": True}
        expected = {p["symbol"]: p["quantity"] for p in positions}
        if any(inventory.get(s, 0) != q for s,q in expected.items()):
            return {"state": "POSITION_RECONCILIATION", "reason": "broker inventory differs from confirmed fills; entries paused", "broker_connected": True}
        # Balance failures must not prevent a confirmed owned position from exiting.
        account = daily = None
        try:
            account = self._account(profile.account_id)
            daily = self.store.daily(now, account["equity"], account["daily_pnl"], self.store.envelope(now))
        except Exception as exc:
            log.warning("live balance unavailable type=%s", type(exc).__name__)
        common = {"broker_connected": True, "account": account, "daily": daily}
        if not positions and not can_enter:
            return {**common, "state": "PAUSED" if not policy["enabled"] or self.mode_getter() != "LIVE" else "ENTRY_WINDOW_CLOSED",
                    "reason": "new entries paused; owned positions remain managed"}
        if not positions and (foreign or inventory):
            return {**common, "state": "UNMANAGED_POSITION", "reason": "account contains a position outside this automation"}
        if not positions and (daily is None or daily["halt_reason"]):
            return {**common, "state": "DAILY_HALT" if daily else "ACCOUNT_UNAVAILABLE",
                    "reason": daily["halt_reason"] if daily else "settled cash and daily P&L must be verified"}

        self.provider.pinned_symbols = set(expected)
        snapshots = self.provider.collect_once(eastern.date())
        now = self.clock()
        self.market.ingest(snapshots, now)
        if positions:
            position = positions[0]
            q = self.market.fresh_quote(position["symbol"], now)
            if q is None or q.bid <= 0:
                return {**common, "state": "EXIT_WAITING_QUOTE", "reason": "fresh executable production bid required for exit"}
            reason = "automation_stopped" if not can_enter and (not policy["enabled"] or self.mode_getter() != "LIVE") else None
            reason = reason or ("daily_stop" if daily and daily["halt_reason"] else None)
            reason = reason or ("session_close" if minutes_left <= 10 else None)
            reason = reason or ("continue_exit" if position["exit_requested"] else None)
            reason = reason or self.strategy.exit(position, now)
            if reason is None:
                return {**common, "state": "POSITION_OPEN", "reason": "managing confirmed broker position"}
            request = position["request"]
            order = OptionOrderRequest(**{**request, "client_order_id": uuid.uuid4().hex,
                                         "side": "SELL", "position_intent": "SELL_TO_CLOSE",
                                         "quantity": position["quantity"], "limit_price": round(q.bid, 2)})
            return {**common, **self._submit(order, symbol=position["symbol"], reason=reason,
                    signal_key=order.client_order_id, policy=policy, now=now, position_id=position["position_id"])}

        orders = self.store.orders()
        if orders and (now-datetime.fromisoformat(orders[-1]["updated_at"])).total_seconds() < 180:
            return {**common, "state": "COOLDOWN", "reason": "waiting between live executions"}
        limits = self.store.envelope(now)
        candidate, decision = self.strategy.entry(now, account, daily, limits)
        if candidate is None:
            return {**common, **decision}
        now = self.clock()  # Model latency cannot make an old quote executable.
        q = self.market.fresh_quote(candidate["symbol"], now)
        if q is None or q.ask <= 0 or q.bid <= 0:
            return {**common, "state": "WAITING_FRESH_DATA", "reason": "signal quote expired before execution"}
        underlying, expiration, right, strike = parse_occ_symbol(candidate["symbol"])
        if expiration != now.astimezone(EASTERN).date().isoformat() or self.strategy._minutes_to_close(now.astimezone(EASTERN)) <= 20:
            return {**common, "state": "ENTRY_WINDOW_CLOSED", "reason": "0DTE entry window closed"}
        # Re-read balances after model work. Reserve full premium + fee cushion,
        # including the worst possible loss of this long option, before sizing.
        account = self._account(profile.account_id)
        daily = self.store.daily(now, account["equity"], account["daily_pnl"], limits)
        if daily["halt_reason"]:
            return {**common, "state": "DAILY_HALT", "reason": daily["halt_reason"]}
        premium = math.ceil(q.ask*100-1e-9)/100
        per_contract = premium*100 + 1.0
        budget = min(account["settled_cash"], account["equity"]*limits.max_account_exposure_pct,
                     max(0.0, limits.daily_loss_limit+account["daily_pnl"]))
        quantity = min(int(candidate["quantity"]), limits.max_contracts, int(budget//per_contract))
        if quantity < 1:
            return {**common, "state": "RISK_BLOCKED", "reason": "one contract exceeds settled cash or remaining loss/exposure allowance"}
        order = OptionOrderRequest(profile.account_id, uuid.uuid4().hex, underlying, strike,
                                   expiration, right, "BUY", "BUY_TO_OPEN", quantity, premium)
        return {**common, **decision, **self._submit(order, symbol=candidate["symbol"], reason=candidate["reason"],
                signal_key=f"{candidate['symbol']}:{candidate['cycle']}", policy=policy, now=now)}


def run_forever(mode_getter, context):
    path = autonomy_db_path()
    with execution_lock(path):
        store = LiveAutonomyStore(path)
        executor = None
        active_credentials = None
        failures = 0
        while not context.stop.is_set():
            started = time.monotonic()
            try:
                policy = store.policy()
                reasons = execution_blocks()
                if not policy["configured"] or reasons:
                    publish(state="LIVE_LOCKED" if reasons else "DISABLED",
                            reason=", ".join(reasons) if reasons else "awaiting one-time owner activation",
                            reasons=reasons, broker_connected=False)
                else:
                    credentials = production_credentials()
                    if executor is None or credentials != active_credentials:
                        market = LiveMarketReader()
                        key, secret = credentials
                        executor = LiveExecutor(store=store, broker=WebullLiveClient.from_env(), market=market,
                            provider=LiveDataProvider(key, secret),
                            strategy=LiveStrategy(market, path+".research"), mode_getter=mode_getter)
                        active_credentials = credentials
                    publish(**executor.telemetry(executor.tick()))
                failures = 0
                context.heartbeat(last_error=None)
            except Exception as exc:
                failures += 1
                publish(state="RECOVERING", reason=f"{type(exc).__name__}: retrying broker reconciliation automatically",
                        broker_connected=False)
                context.heartbeat(state="RECOVERING", last_error=type(exc).__name__)
                log.warning("live reconciliation failed type=%s", type(exc).__name__)
                if failures >= 5:
                    raise
            context.wait(max(0.1, (2.0 if not failures else min(30, 2**failures)) - (time.monotonic()-started)))
