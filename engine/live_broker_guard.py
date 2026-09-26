from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

from .live_risk import LiveRiskEnvelope
from .tradier_live import TradierLiveClient, TradierLiveError, tradier_env_status


@dataclass(frozen=True)
class LiveBrokerGuard:
    connected: bool
    entry_allowed: bool
    reasons: tuple[str, ...]
    daily_realized_pnl: float
    daily_open_pnl: float
    daily_total_pnl: float
    cash_available: float | None
    total_equity: float | None
    pending_orders_count: int
    open_positions: int
    max_entry_debit: float | None
    error: str | None = None

    @property
    def daily_stop_reached(self) -> bool:
        return any(
            reason in {"daily_loss_stop_reached", "daily_gain_stop_reached"}
            for reason in self.reasons
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "connected": self.connected,
            "entry_allowed": self.entry_allowed,
            "reasons": list(self.reasons),
            "daily_realized_pnl": self.daily_realized_pnl,
            "daily_open_pnl": self.daily_open_pnl,
            "daily_total_pnl": self.daily_total_pnl,
            "cash_available": self.cash_available,
            "total_equity": self.total_equity,
            "pending_orders_count": self.pending_orders_count,
            "open_positions": self.open_positions,
            "max_entry_debit": self.max_entry_debit,
            "max_open_positions": 1,
            "error": self.error,
        }


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _positions_count(payload: dict[str, Any]) -> int:
    positions = payload.get("positions")
    if not isinstance(positions, dict):
        return 0
    position = positions.get("position")
    if position is None:
        return 0
    if isinstance(position, list):
        return len([item for item in position if isinstance(item, dict)])
    return 1 if isinstance(position, dict) else 0


def evaluate_live_broker_guard(
    client: TradierLiveClient,
    envelope: LiveRiskEnvelope,
) -> LiveBrokerGuard:
    """Reconcile the owner-selected daily envelope against broker state.

    This function is read-only. It never places, modifies, or cancels an order.
    The purpose is to prevent a new live alert when the account is no longer
    eligible for another entry under the owner's daily limits.
    """

    reasons: list[str] = []
    profile = client.account_profile()
    if not profile.is_active:
        reasons.append("broker_account_not_active")
    if not profile.is_cash:
        reasons.append("broker_account_not_cash")

    balances_payload = client.balances(profile.account_number)
    positions_payload = client.positions(profile.account_number)
    balances = balances_payload.get("balances")
    if not isinstance(balances, dict):
        balances = {}
    cash = balances.get("cash")
    if not isinstance(cash, dict):
        cash = {}

    realized = _number(balances.get("close_pl")) or 0.0
    open_pnl = _number(balances.get("open_pl")) or 0.0
    total_pnl = realized + open_pnl
    cash_available = _number(cash.get("cash_available"))
    if cash_available is None:
        cash_available = _number(balances.get("total_cash"))
    total_equity = _number(balances.get("total_equity"))
    pending_orders = int(_number(balances.get("pending_orders_count")) or 0.0)
    open_positions = _positions_count(positions_payload)

    if envelope.daily_loss_limit is not None and total_pnl <= -envelope.daily_loss_limit:
        reasons.append("daily_loss_stop_reached")
    if envelope.daily_gain_limit is not None and total_pnl >= envelope.daily_gain_limit:
        reasons.append("daily_gain_stop_reached")
    if open_positions > 0:
        reasons.append("position_already_open")
    if pending_orders > 0:
        reasons.append("broker_order_pending")

    basis_candidates = [
        value
        for value in (cash_available, total_equity)
        if value is not None and value > 0.0
    ]
    max_entry_debit = None
    if envelope.max_account_exposure_pct is not None and basis_candidates:
        max_entry_debit = min(basis_candidates) * envelope.max_account_exposure_pct
        if max_entry_debit <= 0.0:
            reasons.append("no_live_exposure_budget")
    else:
        reasons.append("live_exposure_basis_unavailable")

    return LiveBrokerGuard(
        connected=True,
        entry_allowed=not reasons,
        reasons=tuple(dict.fromkeys(reasons)),
        daily_realized_pnl=realized,
        daily_open_pnl=open_pnl,
        daily_total_pnl=total_pnl,
        cash_available=cash_available,
        total_equity=total_equity,
        pending_orders_count=pending_orders,
        open_positions=open_positions,
        max_entry_debit=max_entry_debit,
    )


_CACHE_LOCK = threading.Lock()
_CACHE_KEY: tuple[object, ...] | None = None
_CACHE_AT = 0.0
_CACHE_VALUE: LiveBrokerGuard | None = None


def _cache_key(envelope: LiveRiskEnvelope) -> tuple[object, ...]:
    status = tradier_env_status()
    return (
        status.get("configured"),
        envelope.trading_date,
        envelope.daily_loss_limit,
        envelope.daily_gain_limit,
        envelope.max_account_exposure_pct,
        envelope.max_contracts,
    )


def cached_live_broker_guard(
    envelope: LiveRiskEnvelope,
    *,
    ttl_seconds: float = 3.0,
) -> LiveBrokerGuard:
    """Return a short-lived cached guard so the 1 Hz status socket stays cheap."""

    global _CACHE_AT, _CACHE_KEY, _CACHE_VALUE
    key = _cache_key(envelope)
    now = time.monotonic()
    with _CACHE_LOCK:
        if _CACHE_VALUE is not None and _CACHE_KEY == key and now - _CACHE_AT < ttl_seconds:
            return _CACHE_VALUE

    status = tradier_env_status()
    if not status["configured"]:
        guard = LiveBrokerGuard(
            connected=False,
            entry_allowed=False,
            reasons=("tradier_credentials_missing",),
            daily_realized_pnl=0.0,
            daily_open_pnl=0.0,
            daily_total_pnl=0.0,
            cash_available=None,
            total_equity=None,
            pending_orders_count=0,
            open_positions=0,
            max_entry_debit=None,
        )
    else:
        try:
            guard = evaluate_live_broker_guard(TradierLiveClient.from_env(), envelope)
        except (TradierLiveError, ValueError, TypeError) as exc:
            guard = LiveBrokerGuard(
                connected=False,
                entry_allowed=False,
                reasons=("broker_guard_unavailable",),
                daily_realized_pnl=0.0,
                daily_open_pnl=0.0,
                daily_total_pnl=0.0,
                cash_available=None,
                total_equity=None,
                pending_orders_count=0,
                open_positions=0,
                max_entry_debit=None,
                error=f"{type(exc).__name__}: {exc}",
            )

    with _CACHE_LOCK:
        _CACHE_KEY = key
        _CACHE_AT = now
        _CACHE_VALUE = guard
    return guard
