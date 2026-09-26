from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .broker import OptionOrderRequest
from .live_broker_guard import LiveBrokerGuard
from .live_risk import LiveRiskEnvelope
from .webull import PRODUCTION_API_HOST, build_option_order_item


class WebullLiveError(RuntimeError):
    pass


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _response_json(response: Any, action: str) -> Any:
    status = getattr(response, "status_code", 200)
    try:
        payload = response.json() if hasattr(response, "json") else response
    except Exception as exc:  # pragma: no cover - broker SDK edge case
        raise WebullLiveError(f"Webull {action} returned unreadable JSON") from exc
    if status is not None and not 200 <= int(status) < 300:
        detail = payload if isinstance(payload, (dict, list)) else str(payload)
        raise WebullLiveError(f"Webull {action} failed with HTTP {status}: {str(detail)[:500]}")
    return payload


def _rows(payload: Any) -> tuple[Mapping[str, Any], ...]:
    if payload is None:
        return ()
    if isinstance(payload, list):
        return tuple(item for item in payload if isinstance(item, Mapping))
    if not isinstance(payload, Mapping):
        return ()
    for key in ("data", "items", "list", "results", "accounts", "positions", "orders"):
        value = payload.get(key)
        if isinstance(value, list):
            return tuple(item for item in value if isinstance(item, Mapping))
        if isinstance(value, Mapping):
            for nested in ("data", "items", "list", "results", "accounts", "positions", "orders"):
                nested_value = value.get(nested)
                if isinstance(nested_value, list):
                    return tuple(item for item in nested_value if isinstance(item, Mapping))
    return ()


@dataclass(frozen=True)
class WebullAccountProfile:
    account_id: str
    account_number: str
    account_type: str

    @property
    def is_cash(self) -> bool:
        return self.account_type.upper() == "CASH"

    def as_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "account_number": self.account_number,
            "account_type": self.account_type,
            "is_cash": self.is_cash,
        }


class WebullLiveClient:
    """Production Webull adapter for user-confirmed SPY option orders.

    The client exposes preview/place only as explicit methods. The autonomous
    strategy loop never owns an instance of this class; the confirmed-live API
    constructs it only at the owner authorization boundary.
    """

    def __init__(
        self,
        app_key: str,
        app_secret: str,
        *,
        account_id: str | None = None,
        region: str = "us",
        token_dir: str | None = None,
        trade_client: Any | None = None,
    ) -> None:
        if not app_key or not app_secret:
            raise ValueError("WEBULL_APP_KEY and WEBULL_APP_SECRET are required")
        self._configured_account_id = (account_id or "").strip()

        if trade_client is not None:
            self._trade_client = trade_client
            return

        try:
            from webull.core.client import ApiClient
            from webull.trade.trade_client import TradeClient
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("Install webull-openapi-python-sdk for Webull live support") from exc

        api_client = ApiClient(app_key, app_secret, region)
        api_client.add_endpoint(region, PRODUCTION_API_HOST)
        resolved_token_dir = (
            token_dir
            or os.environ.get("WEBULL_OPENAPI_TOKEN_DIR", "").strip()
            or "/data/webull-openapi"
        )
        Path(resolved_token_dir).mkdir(parents=True, exist_ok=True)
        if hasattr(api_client, "set_token_dir"):
            api_client.set_token_dir(resolved_token_dir)
        self._trade_client = TradeClient(api_client)

    @classmethod
    def from_env(cls) -> "WebullLiveClient":
        return cls(
            os.environ.get("WEBULL_APP_KEY", "").strip(),
            os.environ.get("WEBULL_APP_SECRET", "").strip(),
            account_id=os.environ.get("WEBULL_ACCOUNT_ID", "").strip() or None,
            token_dir=os.environ.get("WEBULL_OPENAPI_TOKEN_DIR", "").strip() or None,
        )

    def accounts(self) -> tuple[Mapping[str, Any], ...]:
        payload = _response_json(self._trade_client.account_v2.get_account_list(), "account list")
        return _rows(payload)

    def account_profile(self) -> WebullAccountProfile:
        rows = self.accounts()
        if not rows:
            raise WebullLiveError("Webull returned no trading accounts")

        selected: Mapping[str, Any] | None = None
        if self._configured_account_id:
            selected = next(
                (
                    row
                    for row in rows
                    if str(row.get("account_id") or "") == self._configured_account_id
                    or str(row.get("account_number") or "") == self._configured_account_id
                ),
                None,
            )
            if selected is None:
                raise WebullLiveError("WEBULL_ACCOUNT_ID does not match an accessible Webull account")
        else:
            cash_rows = [row for row in rows if str(row.get("account_type") or "").upper() == "CASH"]
            if len(cash_rows) == 1:
                selected = cash_rows[0]
            elif len(rows) == 1:
                selected = rows[0]
            elif len(cash_rows) > 1:
                raise WebullLiveError("multiple Webull cash accounts found; configure WEBULL_ACCOUNT_ID")
            else:
                raise WebullLiveError("multiple Webull accounts found; configure WEBULL_ACCOUNT_ID")

        account_id = str(selected.get("account_id") or "").strip()
        if not account_id:
            raise WebullLiveError("Webull account response is missing account_id")
        return WebullAccountProfile(
            account_id=account_id,
            account_number=str(selected.get("account_number") or account_id),
            account_type=str(selected.get("account_type") or "UNKNOWN").upper(),
        )

    def balances(self, account_id: str) -> dict[str, Any]:
        payload = _response_json(
            self._trade_client.account_v2.get_account_balance(account_id),
            "account balance",
        )
        if not isinstance(payload, Mapping):
            raise WebullLiveError("Webull balance response is not an object")
        return dict(payload)

    def positions(self, account_id: str) -> tuple[Mapping[str, Any], ...]:
        payload = _response_json(
            self._trade_client.account_v2.get_account_position(account_id),
            "account positions",
        )
        return _rows(payload)

    def open_orders(self, account_id: str) -> tuple[Mapping[str, Any], ...]:
        payload = _response_json(
            self._trade_client.order_v3.get_order_open(account_id=account_id),
            "open orders",
        )
        return _rows(payload)

    def preview_option_order(self, order: OptionOrderRequest) -> dict[str, Any]:
        item = build_option_order_item(order)
        payload = _response_json(
            self._trade_client.order_v3.preview_order(order.account_id, [item]),
            "order preview",
        )
        if not isinstance(payload, Mapping):
            raise WebullLiveError("Webull preview response is not an object")
        return dict(payload)

    def place_confirmed_option_order(self, order: OptionOrderRequest) -> dict[str, Any]:
        item = build_option_order_item(order)
        payload = _response_json(
            self._trade_client.order_v3.place_order(order.account_id, [item]),
            "order placement",
        )
        if not isinstance(payload, Mapping):
            raise WebullLiveError("Webull place response is not an object")
        return dict(payload)

    def order_detail(self, account_id: str, client_order_id: str) -> dict[str, Any] | None:
        try:
            payload = _response_json(
                self._trade_client.order_v3.get_order_detail(account_id, client_order_id),
                "order detail",
            )
        except WebullLiveError:
            return None
        return dict(payload) if isinstance(payload, Mapping) else None


def webull_env_status() -> dict[str, Any]:
    key = bool(os.environ.get("WEBULL_APP_KEY", "").strip())
    secret = bool(os.environ.get("WEBULL_APP_SECRET", "").strip())
    return {
        "provider": "webull",
        "environment": "production",
        "app_key_configured": key,
        "app_secret_configured": secret,
        "account_id_configured": bool(os.environ.get("WEBULL_ACCOUNT_ID", "").strip()),
        "configured": key and secret,
        "execution": "confirmed_order_only",
    }


def _balance_object(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    nested = payload.get("balance")
    return nested if isinstance(nested, Mapping) else payload


def _usd_asset(balance: Mapping[str, Any]) -> Mapping[str, Any]:
    assets = balance.get("account_currency_assets")
    if isinstance(assets, list):
        for item in assets:
            if isinstance(item, Mapping) and str(item.get("currency") or "USD").upper() == "USD":
                return item
    return {}


def evaluate_webull_guard(client: WebullLiveClient, envelope: LiveRiskEnvelope) -> LiveBrokerGuard:
    reasons: list[str] = []
    profile = client.account_profile()
    if not profile.is_cash:
        reasons.append("broker_account_not_cash")

    raw_balance = client.balances(profile.account_id)
    balance = _balance_object(raw_balance)
    positions = client.positions(profile.account_id)
    orders = client.open_orders(profile.account_id)
    usd = _usd_asset(balance)

    total_day_pnl = _number(balance.get("total_day_profit_loss"))
    if total_day_pnl is None:
        total_day_pnl = _number(usd.get("day_profit_loss"))
    if total_day_pnl is None:
        reasons.append("broker_daily_pnl_unavailable")
        total_day_pnl = 0.0

    open_pnl = _number(balance.get("total_unrealized_profit_loss"))
    if open_pnl is None:
        open_pnl = _number(usd.get("unrealized_profit_loss")) or 0.0

    settled_cash = _number(usd.get("settled_cash"))
    buying_power = _number(usd.get("buying_power"))
    total_cash = _number(balance.get("total_cash_balance"))
    cash_candidates = [value for value in (settled_cash, buying_power, total_cash) if value is not None and value >= 0]
    cash_available = min(cash_candidates) if cash_candidates else None
    total_equity = _number(balance.get("total_net_liquidation_value"))

    open_positions = sum(
        1
        for row in positions
        if abs(_number(row.get("quantity")) or 0.0) > 1e-12
    )
    pending_orders = len(orders)

    if envelope.daily_loss_limit is not None and total_day_pnl <= -envelope.daily_loss_limit:
        reasons.append("daily_loss_stop_reached")
    if envelope.daily_gain_limit is not None and total_day_pnl >= envelope.daily_gain_limit:
        reasons.append("daily_gain_stop_reached")
    if open_positions > 0:
        reasons.append("position_already_open")
    if pending_orders > 0:
        reasons.append("broker_order_pending")

    basis = [value for value in (cash_available, total_equity) if value is not None and value > 0]
    max_entry_debit = None
    if envelope.max_account_exposure_pct is not None and basis:
        max_entry_debit = min(basis) * envelope.max_account_exposure_pct
        if max_entry_debit <= 0:
            reasons.append("no_live_exposure_budget")
    else:
        reasons.append("live_exposure_basis_unavailable")

    return LiveBrokerGuard(
        connected=True,
        entry_allowed=not reasons,
        reasons=tuple(dict.fromkeys(reasons)),
        daily_realized_pnl=total_day_pnl,
        daily_open_pnl=open_pnl,
        daily_total_pnl=total_day_pnl,
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


def cached_webull_guard(envelope: LiveRiskEnvelope, *, ttl_seconds: float = 3.0) -> LiveBrokerGuard:
    global _CACHE_KEY, _CACHE_AT, _CACHE_VALUE
    env = webull_env_status()
    key = (
        env["configured"],
        os.environ.get("WEBULL_ACCOUNT_ID", "").strip(),
        envelope.trading_date,
        envelope.daily_loss_limit,
        envelope.daily_gain_limit,
        envelope.max_account_exposure_pct,
        envelope.max_contracts,
    )
    now = time.monotonic()
    with _CACHE_LOCK:
        if _CACHE_VALUE is not None and _CACHE_KEY == key and now - _CACHE_AT < ttl_seconds:
            return _CACHE_VALUE

    if not env["configured"]:
        guard = LiveBrokerGuard(
            connected=False,
            entry_allowed=False,
            reasons=("webull_credentials_missing",),
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
            guard = evaluate_webull_guard(WebullLiveClient.from_env(), envelope)
        except (WebullLiveError, ValueError, TypeError, RuntimeError) as exc:
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
