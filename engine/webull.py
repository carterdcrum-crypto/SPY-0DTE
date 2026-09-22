from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict

from .broker import OptionOrderRequest, ReplaceOrderRequest


SANDBOX_API_HOST = "api.sandbox.webull.com"
SANDBOX_EVENTS_HOST = "events-api.sandbox.webull.com"
PRODUCTION_API_HOST = "api.webull.com"
PRODUCTION_EVENTS_HOST = "events-api.webull.com"


def _price(value: float) -> str:
    return f"{value:.2f}"


def build_option_order_item(request: OptionOrderRequest) -> Dict[str, Any]:
    """Build the Webull unified-order item for a single SPY option.

    This project remains long-premium only: BUY_TO_OPEN and SELL_TO_CLOSE are the
    only intents representable by `OptionOrderRequest`.
    """

    return {
        "client_order_id": request.client_order_id,
        "combo_type": "NORMAL",
        "order_type": "LIMIT",
        "limit_price": _price(request.limit_price),
        "quantity": str(request.quantity),
        "option_strategy": "SINGLE",
        "side": request.side,
        "time_in_force": "DAY",
        "entrust_type": "QTY",
        "instrument_type": "OPTION",
        "market": "US",
        "symbol": "SPY",
        "position_intent": request.position_intent,
        "legs": [
            {
                "side": request.side,
                "quantity": str(request.quantity),
                "symbol": "SPY",
                "strike_price": _price(request.strike_price),
                "option_expire_date": request.expiration_date,
                "instrument_type": "OPTION",
                "option_type": request.option_type,
                "market": "US",
            }
        ],
    }


def build_place_payload(request: OptionOrderRequest) -> Dict[str, Any]:
    return {
        "account_id": request.account_id,
        "new_orders": [build_option_order_item(request)],
    }


def build_replace_item(request: ReplaceOrderRequest) -> Dict[str, str]:
    return {
        "client_order_id": request.client_order_id,
        "quantity": str(request.quantity),
        "limit_price": _price(request.limit_price),
    }


class WebullSandboxClient:
    """Thin adapter around Webull's official Python SDK.

    Imports are deferred so the core research engine and CI do not require broker
    credentials or the Webull package. Instantiating this class is the explicit
    boundary where sandbox connectivity begins.
    """

    def __init__(self, app_key: str, app_secret: str, *, region: str = "us") -> None:
        if not app_key or not app_secret:
            raise ValueError("Webull app_key and app_secret are required")

        try:
            from webull.core.client import ApiClient
            from webull.trade.trade_client import TradeClient
        except ImportError as exc:  # pragma: no cover - optional broker dependency
            raise RuntimeError(
                "Install the optional Webull dependency: "
                "pip install webull-openapi-python-sdk"
            ) from exc

        api_client = ApiClient(app_key, app_secret, region)
        api_client.add_endpoint(region, SANDBOX_API_HOST)
        self._trade_client = TradeClient(api_client)

    def get_accounts(self):
        return self._trade_client.account_v2.get_account_list()

    def get_balance(self, account_id: str):
        return self._trade_client.account_v2.get_account_balance(account_id)

    def get_positions(self, account_id: str):
        return self._trade_client.account_v2.get_account_position(account_id)

    def place_option_limit(self, request: OptionOrderRequest):
        item = build_option_order_item(request)
        return self._trade_client.order_v3.place_order(request.account_id, [item])

    def replace_option_limit(self, request: ReplaceOrderRequest):
        item = build_replace_item(request)
        return self._trade_client.order_v3.replace_order(request.account_id, [item])

    def cancel_order(self, account_id: str, client_order_id: str):
        return self._trade_client.order_v3.cancel_order(account_id, client_order_id)
