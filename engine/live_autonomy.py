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
from .exit_rules import profit_exit_quantity
from .live_autonomy_store import LiveAutonomyStore, autonomy_db_path
from .live_market import LiveDataProvider, LiveMarketReader, parse_occ_symbol, position_symbol
from .live_risk import EASTERN
from .paper_runner import execution_lock
from .session_calendar import session_bounds
from .webull_live import WebullLiveClient, WebullLiveError, _balance_object, _number, _usd_asset, webull_env_status, production_credentials, selected_webull_account_id

log = logging.getLogger("spy0dte.live")
_status_lock = threading.Lock()
_status: dict[str, Any] = {"state": "STOPPED", "reason": "live worker has not started"}


def publish(**values):
    with _status_lock:
        _status.clear()
        _status.update(values, updated_at=datetime.now(timezone.utc).isoformat())
    # A protected, bounded decision trail makes every missing V5 entry
    # explainable after the fact. Never make execution depend on audit writes.
    try:
        from .live_decision_audit import record_decision
        record_decision(values)
    except (OSError, ValueError, TypeError, __import__("sqlite3").Error) as exc:
        log.warning("live decision audit unavailable type=%s", type(exc).__name__)


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

    def _unrestricted_v5(self) -> bool:
        # Only an explicitly selected, separately typed V5 strategy removes
        # discretionary limits. Merely changing a candidate reason cannot.
        return bool(getattr(self.strategy, "unrestricted_v5_live", False))

    def _account(self, account_id):
        now = self.clock()
        # At most one balance request per second (Webull permits two per two
        # seconds). A fast model pass can reuse the same sub-second snapshot.
        if self._account_read_at is None or (now-self._account_read_at).total_seconds() >= 3.0:
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

    def _fresh_pending_exit_quote(self, symbol):
        """Read the existing market collector, NEVER add a Webull option poll.

        Refresh only when a pending protective SELL needs a new observed bid.
        The existing working sell remains at the broker if quotes go stale.
        """
        now = self.clock()
        quote = self.market.fresh_quote(symbol, now)
        if quote is not None:
            return quote
        try:
            if isinstance(self.provider, LiveDataProvider):
                from .shared_live_quotes import collector_live_quotes
                snapshots = collector_live_quotes(
                    os.environ.get("COLLECTOR_DB_PATH", "/data/spy_0dte.sqlite"),
                    now,
                )
            else:
                snapshots = self.provider.collect_once(now.astimezone(EASTERN).date())
            self.market.ingest(snapshots, self.clock())
            return self.market.fresh_quote(symbol, self.clock())
        except Exception as exc:
            # Not a reason to cancel an already-working protective order.
            log.warning("pending protective exit quote unavailable type=%s",
                        type(exc).__name__)
            return None

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
            now = self.clock()  # Do not decide against a stale tick clock.
            age = (now-datetime.fromisoformat(order["created_at"])).total_seconds()
            request = order["request"]
            if request["side"] == "BUY":
                cancel_due = age >= 15 or not can_enter
            elif order["status"] == "UNKNOWN":
                # Submission might have reached Webull. Cancel ONLY its
                # original client ID, and never post a replacement until the
                # broker confirms a terminal cancellation or fill.
                cancel_due = age >= 10
            elif order["reason"] == "take_profit":
                # Preserve the existing V4 profit-order policy.
                cancel_due = age >= 10
            else:
                # A working protective SELL may have been left behind by a
                # falling option bid. Reprice by broker-confirmed
                # cancel -> reconcile -> fresh-bid replacement of remaining
                # quantity; never leave two simultaneous exit orders.
                # Do not cancel if no executable current quote is available:
                # the existing broker-side limit may still fill.
                cancel_due = False
                if age >= 5:
                    quote = self._fresh_pending_exit_quote(order["symbol"])
                    if quote is not None and quote.bid > 0:
                        cancel_due = round(quote.bid, 2) < float(request["limit_price"])
            cancel_at = order["cancel_at"]
            if cancel_due and (cancel_at is None or
                               (now-datetime.fromisoformat(cancel_at)).total_seconds() >= 10):
                # Cancel SAME ID only. A terminal broker state is mandatory
                # before new execution; UNKNOWN fills cannot be duplicated.
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
        from .v5_close_only_operational import V5_LATEST_ENTRY_MINUTES
        minimum_minutes = V5_LATEST_ENTRY_MINUTES if self._unrestricted_v5() else 20
        if order.side == "BUY" and self.strategy._minutes_to_close(now.astimezone(EASTERN)) <= minimum_minutes:
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
            # Read-only broker balances remain useful after the market closes.
            # A closed session must never create a new order or imply data
            # readiness. If Webull denies balance access, leave it unknown.
            account = None
            try:
                account = self._account(profile.account_id)
            except Exception as exc:
                log.warning("closed-session live balance unavailable type=%s", type(exc).__name__)
            return {"state": "MARKET_CLOSED",
                    "reason": "waiting for the next exchange session" if account is not None
                              else "market closed; Webull balance data unavailable",
                    "broker_connected": True, "account": account}

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
            daily = self.store.daily(
                now, account["equity"], account["daily_pnl"], self.store.envelope(now),
                unrestricted_v5=self._unrestricted_v5())
        except Exception as exc:
            log.warning("live balance unavailable type=%s", type(exc).__name__)
        common = {"broker_connected": True, "account": account, "daily": daily}
        if not positions and not can_enter:
            # Once V5's final-entry window closes, retain the final verified
            # FLAT-account mark for this session's next-day EOD drawdown gate.
            # This is observational: never creates an order or changes mode.
            if (self._unrestricted_v5() and account is not None
                    and not inventory and not foreign and in_session):
                from .v5_drawdown_sizing import observe_flat_session
                observe_flat_session(
                    autonomy_db_path(), eastern.date(), account["equity"])
            return {**common, "state": "PAUSED" if not policy["enabled"] or self.mode_getter() != "LIVE" else "ENTRY_WINDOW_CLOSED",
                    "reason": "new entries paused; owned positions remain managed"}
        if not positions and (foreign or inventory):
            return {**common, "state": "UNMANAGED_POSITION", "reason": "account contains a position outside this automation"}
        if not positions and (daily is None or daily["halt_reason"]):
            return {**common, "state": "DAILY_HALT" if daily else "ACCOUNT_UNAVAILABLE",
                    "reason": daily["halt_reason"] if daily else "settled cash and daily P&L must be verified"}

        self.provider.pinned_symbols = set(expected)
        # Update the real (subscription-gated) SPY M1 feed BEFORE fresh option
        # snapshots; never block owned-position exits on a denied bar feed.
        if hasattr(self.strategy,"refresh_bars"):
            self.strategy.refresh_bars(self.provider,now)
        # The collector is the only high-frequency Webull option quote owner.
        # Sharing its validated SQLite cycle eliminates duplicate production
        # snapshot requests, which previously saturated per-app API quotas.
        if isinstance(self.provider, LiveDataProvider):
            from .shared_live_quotes import collector_live_quotes
            snapshots = collector_live_quotes(
                os.environ.get("COLLECTOR_DB_PATH", "/data/spy_0dte.sqlite"),
                self.clock(),
            )
        else:
            # Preserve injected test/sandbox providers for deterministic E2E.
            snapshots = self.provider.collect_once(eastern.date())
        now = self.clock()
        self.market.ingest(snapshots, now)
        if positions:
            position = positions[0]
            q = self.market.fresh_quote(position["symbol"], now)
            if q is None or q.bid <= 0:
                return {**common, "state": "EXIT_WAITING_QUOTE", "reason": "fresh executable production bid required for exit"}
            # Only a fresh, tradable bid may move the high-water mark.
            fee = getattr(getattr(self.strategy, "settings", None), "fee_per_contract", 0.70)
            peak = self.store.record_exit_peak(
                position["position_id"],
                max(0.0, q.bid * 100.0 - fee),
                position["average_price"] * 100.0 + fee,
            )
            position["peak_exit_value"] = peak
            from .one_shot_live_test import TAG as TEST_TAG
            # For this specifically authorized diagnostic, submit SELL_TO_CLOSE
            # on the very first observed, broker-confirmed long position, rather
            # than waiting for stop/profit/timeout signals.
            reason = "one_shot_immediate_sell" if str(position.get("entry_reason","")).startswith(TEST_TAG) else None
            reason = reason or ("automation_stopped" if not can_enter and (not policy["enabled"] or self.mode_getter() != "LIVE") else None)
            reason = reason or ("daily_stop" if daily and daily["halt_reason"] else None)
            close_buffer = 15 if str(position.get("entry_reason","")).startswith(
                ("v4_original_3x;","v5_close_only_unrestricted_research;")) else 10
            reason = reason or ("session_close" if minutes_left <= close_buffer else None)
            reason = reason or ("continue_exit" if position["exit_requested"] else None)
            target_remaining = profit_exit_quantity(
                position["entry_quantity"], position["quantity"], position["target_filled"]
            )
            # A cancelled target order is retried only while the fresh net bid
            # still clears +100%; do not chase an old target below its price.
            entry_value = position["average_price"] * 100.0 + fee
            if (reason is None and not str(position.get("entry_reason","")).startswith(
                    "v5_close_only_unrestricted_research;")
                    and position["target_requested"] and target_remaining
                    and q.bid * 100.0 - fee >= entry_value * 2.0):
                reason = "take_profit"
            if reason is None and str(position.get("entry_reason","")).startswith(
                    "v5_close_only_unrestricted_research;"):
                # Preserve V5 trailing/reversal exits even if operator switches
                # the entry profile back to V4 while this position is held.
                from .v5_close_only_operational import paper_exit_reason
                from .v4_operational import FEE
                bars=getattr(getattr(self.strategy,"bar_cache",None),"bars",[])
                reason=paper_exit_reason(
                    {**position,"average_cost":
                     position["average_price"]*100+FEE},
                    self.market.quotes.get(position["symbol"]),bars,now)
            else:
                reason = reason or self.strategy.exit(position, now)
            if reason is None:
                return {**common, "state": "POSITION_OPEN", "reason": "managing confirmed broker position"}
            close_quantity = target_remaining if reason == "take_profit" else position["quantity"]
            if close_quantity <= 0:
                return {**common, "state": "POSITION_OPEN",
                        "reason": "profit scale-out complete; managing trailing runner"}
            request = position["request"]
            order = OptionOrderRequest(**{**request, "client_order_id": uuid.uuid4().hex,
                                         "side": "SELL", "position_intent": "SELL_TO_CLOSE",
                                         "quantity": close_quantity, "limit_price": round(q.bid, 2)})
            return {**common, **self._submit(order, symbol=position["symbol"], reason=reason,
                    signal_key=order.client_order_id, policy=policy, now=now, position_id=position["position_id"])}

        from .one_shot_live_test import (
            TAG as TEST_TAG, TestConfig, used_before,
            eligible_option, broker_preview_permits,
        )
        orders = self.store.orders()
        test = TestConfig.from_env()
        # Reflect that the one-shot authorization has been consumed even
        # during the ordinary 180-second cooldown. A prior unknown buy is
        # always irrevocably spent, including after a process restart.
        if test.enabled and used_before(orders):
            return {**common, "state": "ONE_SHOT_ALREADY_USED",
                    "reason": "one test entry reserved; no additional purchases allowed"}
        if orders and (now-datetime.fromisoformat(orders[-1]["updated_at"])).total_seconds() < 180:
            return {**common, "state": "COOLDOWN", "reason": "waiting between live executions"}
        if test.enabled:
            # Suppress ALL normal unrestricted V5 entries while this one-time
            # diagnostic is armed; never silently resume normal buying when
            # the date passes or when the test is over.
            if not self._unrestricted_v5():
                return {**common, "state": "ONE_SHOT_PROFILE_MISMATCH",
                        "reason": "one-shot is supported only with the V5 live profile"}
            timing = test.timing(self.clock())
            if timing is not None:
                return {**common, "state": timing,
                        "reason": "one-shot can only enter on its authorized session within 09:55-15:30 ET"}
            if not can_enter:
                return {**common, "state": "ONE_SHOT_WAITING_OWNER_LIVE_SWITCH",
                        "reason": "owner must enable LIVE Auto Trade; no authorization bypass"}
            candidate = eligible_option(self.market.quotes, self.clock(),
                                        account["settled_cash"], test)
            if candidate is None:
                return {**common, "state": "ONE_SHOT_NO_LIQUID_AFFORDABLE_QUOTE",
                        "reason": "no verified same-day SPY option fits the $25 reserve and liquidity checks"}
            order = OptionOrderRequest(
                account_id=profile.account_id,
                client_order_id=uuid.uuid4().hex,
                underlying="SPY", strike_price=candidate["strike"],
                expiration_date=self.clock().astimezone(EASTERN).date().isoformat(),
                option_type=candidate["right"], side="BUY",
                position_intent="BUY_TO_OPEN", quantity=1,
                limit_price=candidate["price"],
            )
            # Preview is an additional non-executing broker check. Reject any
            # error; never assume a preview is a fill or promise a quick exit.
            try:
                broker_preview = self.broker.preview_option_order(order)
                if not broker_preview_permits(broker_preview):
                    return {**common, "state": "ONE_SHOT_PREVIEW_REJECTED",
                            "reason": "Webull did not approve the diagnostic preview"}
            except Exception as exc:
                log.warning("one-shot Webull preview unavailable type=%s",
                            type(exc).__name__)
                return {**common, "state": "ONE_SHOT_PREVIEW_UNAVAILABLE",
                        "reason": "Webull preview failed; no live order submitted"}
            # The original fresh quote is mandatory even after broker preview.
            fresh = self.market.fresh_quote(candidate["symbol"], self.clock())
            if fresh is None or fresh.ask > order.limit_price + 1e-9:
                return {**common, "state": "ONE_SHOT_QUOTE_MOVED",
                        "reason": "quote moved or became stale during preview"}
            # Re-check settled cash after preview. Entry's durable reserve
            # prevents duplicate orders on a restart / ambiguous Webull POST.
            account = self._account(profile.account_id)
            if account["settled_cash"] < order.limit_price * 100 + 5.0:
                return {**common, "state": "ONE_SHOT_CASH_CHANGED",
                        "reason": "verified settled cash is no longer sufficient"}
            result = self._submit(
                order, symbol=candidate["symbol"],
                reason=TEST_TAG + ";max_25_usd;instant_exit_after_fill",
                signal_key="one_shot:" + test.session.isoformat(),
                policy=policy, now=self.clock(),
            )
            return {**common, **result}
        limits = self.store.envelope(now)
        if not self._unrestricted_v5() and (limits is None or not limits.is_complete):
            return {**common,"state":"RISK_BLOCKED",
                    "reason":"V4 requires saved standing safety limits"}
        candidate, decision = self.strategy.entry(now, account, daily, limits)
        if candidate is None:
            return {**common, **decision}
        now = self.clock()  # Model latency cannot make an old quote executable.
        q = self.market.fresh_quote(candidate["symbol"], now)
        if q is None or q.ask <= 0 or q.bid <= 0:
            return {**common, "state": "WAITING_FRESH_DATA", "reason": "signal quote expired before execution"}
        underlying, expiration, right, strike = parse_occ_symbol(candidate["symbol"])
        from .v5_close_only_operational import V5_LATEST_ENTRY_MINUTES
        latest_entry_buffer = V5_LATEST_ENTRY_MINUTES if self._unrestricted_v5() else 20
        if (expiration != now.astimezone(EASTERN).date().isoformat()
                or self.strategy._minutes_to_close(now.astimezone(EASTERN)) <= latest_entry_buffer):
            return {**common, "state": "ENTRY_WINDOW_CLOSED", "reason": "0DTE entry window closed"}
        # Re-read balances after model work. Reserve full premium + fee cushion,
        # including the worst possible loss of this long option, before sizing.
        account = self._account(profile.account_id)
        daily = self.store.daily(
            now, account["equity"], account["daily_pnl"], limits,
            unrestricted_v5=self._unrestricted_v5())
        if daily["halt_reason"]:
            return {**common, "state": "DAILY_HALT", "reason": daily["halt_reason"]}
        premium = math.ceil(q.ask*100-1e-9)/100
        per_contract = premium*100 + 1.0
        candidate_reason=str(candidate.get("reason",""))
        is_v5 = candidate_reason.startswith("v5_close_only_unrestricted_research;")
        if is_v5 != self._unrestricted_v5():
            # No spoofed candidate can opt out of V4's validated checks.
            return {**common,"state":"RISK_BLOCKED",
                    "reason":"strategy profile and order candidate do not match"}
        if is_v5:
            # OWNER REQUEST: no discretionary max contract count, daily
            # win/loss stop, profit target or account-exposure percent on V5.
            # Broker CASH buying power and fresh displayed NBBO depth remain
            # mandatory. Full premium for every contract can be lost.
            snapshot=self.market.quotes.get(candidate["symbol"])
            mid=(q.ask+q.bid)/2
            if (snapshot is None or snapshot.source!="webull_production"
                    or snapshot.timestamp_quality!="option_quote_timestamp"
                    or mid<=0 or (q.ask-q.bid)/mid>.03
                    or int(snapshot.bid_size)<1 or int(snapshot.ask_size)<1):
                return {**common,"state":"RISK_BLOCKED",
                        "reason":"V5 requires fresh verified market depth and narrow spread"}
            # Revalidate drawdown sizing against a FRESH broker balance.
            # The 100/70/40/15% risk budget cannot be enlarged by a quote
            # change between signal discovery and submission.
            try:
                fraction=float(candidate["risk_fraction"])
                opening=float(candidate["risk_opening_equity"])
                if (not math.isfinite(fraction) or not math.isfinite(opening)
                        or not 0 < fraction <= 1 or opening <= 0):
                    raise ValueError("invalid V5 drawdown budget")
            except (KeyError, TypeError, ValueError, OverflowError):
                return {**common, "state": "RISK_BLOCKED",
                        "reason": "missing or invalid V5 durable drawdown throttle"}
            budget=min(account["settled_cash"], opening*fraction)
            quantity=min(int(candidate["quantity"]),int(snapshot.bid_size),
                         int(snapshot.ask_size),int(budget//per_contract))
        else:
            budget=min(account["settled_cash"],
                       account["equity"]*limits.max_account_exposure_pct,
                       max(0.0, limits.daily_loss_limit+account["daily_pnl"]))
            if candidate_reason.startswith("v4_original_3x;"):
                snapshot=self.market.quotes.get(candidate["symbol"])
                mid=(q.ask+q.bid)/2
                if (snapshot is None or snapshot.source!="webull_production"
                        or snapshot.timestamp_quality!="option_quote_timestamp"
                        or mid<=0 or (q.ask-q.bid)/mid>.035
                        or int(snapshot.bid_size)<int(candidate["quantity"])
                        or int(snapshot.ask_size)<int(candidate["quantity"])):
                    return {**common,"state":"RISK_BLOCKED",
                            "reason":"V4 final quote spread/provenance/depth changed; no order"}
                budget=min(budget,account["equity"]*.35)
            quantity=min(int(candidate["quantity"]),limits.max_contracts,
                         int(budget//per_contract))
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
        active_account_id = None
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
                    selection = selected_webull_account_id()
                    if executor is None or credentials != active_credentials or selection != active_account_id:
                        market = LiveMarketReader()
                        key, secret = credentials
                        from .v4_live_strategy import V4QualityLiveStrategy
                        profile=os.environ.get("LIVE_STRATEGY_PROFILE","v4_original_3x").strip().lower()
                        if profile=="v5_close_only_unrestricted_research":
                            from .v5_close_only_live_strategy import V5CloseOnlyLiveStrategy
                            strategy=V5CloseOnlyLiveStrategy(market,path+".research")
                        elif profile=="v4_original_3x":
                            strategy=V4QualityLiveStrategy(market,path+".research")
                        else:
                            raise ValueError("unknown LIVE strategy profile; no broker orders")
                        executor = LiveExecutor(store=store, broker=WebullLiveClient.from_env(), market=market,
                            provider=LiveDataProvider(key, secret),
                            strategy=strategy,
                            mode_getter=mode_getter)
                        active_credentials = credentials
                        active_account_id = selection
                        log.info("live executor initialized strategy=%s quote_source=shared_collector",
                                 profile)
                    publish(**executor.telemetry(executor.tick()))
                failures = 0
                context.heartbeat(last_error=None)
            except Exception as exc:
                failures += 1
                # WebullLiveError strings are produced from fixed, sanitized
                # adapter messages (never the broker response body or keys).
                # Let the owner see which account/API step actually failed.
                if isinstance(exc, WebullLiveError):
                    description = str(exc)
                    if description.startswith(("Webull ", "broker ", "multiple Webull", "WEBULL_ACCOUNT_ID")):
                        detail = description[:140]
                    else:
                        detail = "Webull account reconciliation was rejected"
                    owner_reason = detail + "; no new entries until connected"
                else:
                    owner_reason = f"{type(exc).__name__}: retrying broker reconciliation automatically"
                publish(state="RECOVERING", reason=owner_reason, broker_connected=False)
                context.heartbeat(state="RECOVERING", last_error=type(exc).__name__)
                log.warning("live reconciliation failed type=%s stage=%s",
                            type(exc).__name__, "webull" if isinstance(exc, WebullLiveError) else "worker")
                # A Webull 429 can persist for minutes. Keep the single owner
                # reconciler alive rather than thrashing its SDK/session after
                # five failures; the watchdog still detects a stalled worker.
            context.wait(max(0.1, (3.0 if not failures else min(30, 2**failures)) - (time.monotonic()-started)))
