from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from typing import Any

import requests

from .broker import OptionOrderRequest


PRODUCTION_BASE_URL = "https://api.tradier.com/v1"


class TradierLiveError(RuntimeError):
    pass


@dataclass(frozen=True)
class TradierAccountProfile:
    account_number: str
    account_type: str
    option_level: int | None
    status: str

    @property
    def is_active(self) -> bool:
        return self.status.lower() == "active"

    @property
    def is_cash(self) -> bool:
        return self.account_type.lower() == "cash"

    def as_dict(self) -> dict[str, Any]:
        return {
            "account_number": self.account_number,
            "account_type": self.account_type,
            "option_level": self.option_level,
            "status": self.status,
            "is_active": self.is_active,
            "is_cash": self.is_cash,
        }


class TradierLiveClient:
    """Production Tradier connectivity with real-order submission intentionally absent.

    This adapter validates a production token, reads account/market/order state,
    and asks Tradier to preview a single-leg option order. The autonomous paper
    engine cannot gain a real-money execution path by importing this client.
    """

    def __init__(
        self,
        *,
        access_token: str,
        account_id: str | None = None,
        base_url: str = PRODUCTION_BASE_URL,
        timeout_seconds: float = 8.0,
        session: requests.Session | None = None,
    ) -> None:
        token = access_token.strip()
        if not token:
            raise ValueError("Tradier production access token is required")
        if base_url.rstrip("/") != PRODUCTION_BASE_URL:
            raise ValueError("live client is restricted to Tradier's production API")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._access_token = token
        self.account_id = (account_id or "").strip() or None
        self.base_url = PRODUCTION_BASE_URL
        self.timeout_seconds = float(timeout_seconds)
        self.session = session or requests.Session()

    @classmethod
    def from_env(cls) -> "TradierLiveClient":
        token = os.environ.get("TRADIER_ACCESS_TOKEN", "").strip()
        account_id = os.environ.get("TRADIER_ACCOUNT_ID", "").strip() or None
        if not token:
            raise TradierLiveError("TRADIER_ACCESS_TOKEN is not configured")
        return cls(access_token=token, account_id=account_id)

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Accept": "application/json",
        }

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = self.session.request(
            method,
            f"{self.base_url}/{path.lstrip('/')}",
            headers=self.headers,
            params=params,
            data=data,
            timeout=self.timeout_seconds,
        )
        try:
            response.raise_for_status()
        except requests.RequestException as exc:
            status = getattr(response, "status_code", "unknown")
            raise TradierLiveError(f"Tradier API request failed with HTTP {status}") from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise TradierLiveError("Tradier API returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise TradierLiveError("Tradier API returned an unexpected payload")
        return payload

    def user_profile(self) -> dict[str, Any]:
        return self._request("GET", "/user/profile")

    def account_profile(self) -> TradierAccountProfile:
        payload = self.user_profile()
        profile = payload.get("profile")
        if not isinstance(profile, dict):
            raise TradierLiveError("Tradier user profile is missing")
        accounts = profile.get("account")
        if isinstance(accounts, dict):
            accounts = [accounts]
        if not isinstance(accounts, list) or not accounts:
            raise TradierLiveError("Tradier profile has no brokerage accounts")

        selected: dict[str, Any] | None = None
        if self.account_id:
            for item in accounts:
                if isinstance(item, dict) and str(item.get("account_number") or "") == self.account_id:
                    selected = item
                    break
            if selected is None:
                raise TradierLiveError("TRADIER_ACCOUNT_ID is not present in the authenticated profile")
        else:
            for item in accounts:
                if isinstance(item, dict) and str(item.get("status") or "").lower() == "active":
                    selected = item
                    break
            if selected is None and isinstance(accounts[0], dict):
                selected = accounts[0]

        if not selected:
            raise TradierLiveError("unable to resolve a Tradier brokerage account")
        account_number = str(selected.get("account_number") or "").strip()
        if not account_number:
            raise TradierLiveError("Tradier account number is missing")
        option_level_raw = selected.get("option_level")
        option_level = None if option_level_raw is None else int(option_level_raw)
        return TradierAccountProfile(
            account_number=account_number,
            account_type=str(selected.get("type") or "unknown"),
            option_level=option_level,
            status=str(selected.get("status") or "unknown"),
        )

    def balances(self, account_id: str | None = None) -> dict[str, Any]:
        resolved = (account_id or self.account_id or self.account_profile().account_number).strip()
        return self._request("GET", f"/accounts/{resolved}/balances")

    def positions(self, account_id: str | None = None) -> dict[str, Any]:
        resolved = (account_id or self.account_id or self.account_profile().account_number).strip()
        return self._request("GET", f"/accounts/{resolved}/positions")

    def account_orders(
        self,
        account_id: str | None = None,
        *,
        status: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        if limit < 1 or limit > 1500:
            raise ValueError("limit must be between 1 and 1500")
        resolved = (account_id or self.account_id or self.account_profile().account_number).strip()
        params: dict[str, Any] = {"limit": limit}
        if status:
            params["status"] = status
        return self._request("GET", f"/accounts/{resolved}/orders", params=params)

    def order_status(self, order_id: int, account_id: str | None = None) -> dict[str, Any]:
        if order_id <= 0:
            raise ValueError("order_id must be positive")
        resolved = (account_id or self.account_id or self.account_profile().account_number).strip()
        return self._request(
            "GET",
            f"/accounts/{resolved}/orders/{order_id}",
            params={"includeTags": "true"},
        )

    def quote(self, symbol: str) -> dict[str, Any]:
        normalized = symbol.strip().upper()
        if not normalized:
            raise ValueError("symbol is required")
        return self._request(
            "GET",
            "/markets/quotes",
            params={"symbols": normalized, "greeks": "false"},
        )

    def option_chain(self, expiration: str) -> dict[str, Any]:
        parsed = date.fromisoformat(expiration)
        return self._request(
            "GET",
            "/markets/options/chains",
            params={
                "symbol": "SPY",
                "expiration": parsed.isoformat(),
                "greeks": "true",
            },
        )

    @staticmethod
    def occ_symbol(order: OptionOrderRequest) -> str:
        expiry = date.fromisoformat(order.expiration_date)
        strike_millis = round(order.strike_price * 1000)
        if strike_millis <= 0 or strike_millis > 99_999_999:
            raise ValueError("strike cannot be represented in OCC symbology")
        right = "C" if order.option_type == "CALL" else "P"
        return f"SPY{expiry:%y%m%d}{right}{strike_millis:08d}"

    def preview_option_order(self, order: OptionOrderRequest) -> dict[str, Any]:
        resolved_account = self.account_id or order.account_id
        if self.account_id and order.account_id != self.account_id:
            raise ValueError("order account does not match configured Tradier account")
        side = "buy_to_open" if order.position_intent == "BUY_TO_OPEN" else "sell_to_close"
        return self._request(
            "POST",
            f"/accounts/{resolved_account}/orders",
            data={
                "class": "option",
                "symbol": "SPY",
                "option_symbol": self.occ_symbol(order),
                "side": side,
                "quantity": str(order.quantity),
                "type": "limit",
                "duration": "day",
                "price": f"{order.limit_price:.2f}",
                "tag": order.client_order_id,
                "preview": "true",
            },
        )


def tradier_env_status() -> dict[str, Any]:
    """Non-secret deployment status suitable for API/UI health payloads."""

    token_present = bool(os.environ.get("TRADIER_ACCESS_TOKEN", "").strip())
    account_present = bool(os.environ.get("TRADIER_ACCOUNT_ID", "").strip())
    return {
        "provider": "tradier",
        "environment": "production",
        "token_configured": token_present,
        "account_configured": account_present,
        "configured": token_present and account_present,
        "execution": "preview_only",
    }
