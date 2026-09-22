from __future__ import annotations

import pytest

from engine.broker import OptionOrderRequest, ReplaceOrderRequest
from engine.webull import (
    SANDBOX_API_HOST,
    build_option_order_item,
    build_place_payload,
    build_replace_item,
)


def _open_request() -> OptionOrderRequest:
    return OptionOrderRequest(
        account_id="acct-test",
        client_order_id="spy-20260922-001",
        underlying="SPY",
        strike_price=680.0,
        expiration_date="2026-09-22",
        option_type="CALL",
        side="BUY",
        position_intent="BUY_TO_OPEN",
        quantity=2,
        limit_price=1.23,
    )


def test_webull_sandbox_host_is_explicit() -> None:
    assert SANDBOX_API_HOST == "api.sandbox.webull.com"


def test_build_single_leg_option_payload() -> None:
    request = _open_request()
    payload = build_place_payload(request)
    assert payload["account_id"] == "acct-test"
    order = payload["new_orders"][0]
    assert order["instrument_type"] == "OPTION"
    assert order["order_type"] == "LIMIT"
    assert order["option_strategy"] == "SINGLE"
    assert order["symbol"] == "SPY"
    assert order["position_intent"] == "BUY_TO_OPEN"
    assert order["quantity"] == "2"
    assert order["limit_price"] == "1.23"
    assert order["legs"][0]["strike_price"] == "680.00"
    assert order["legs"][0]["option_expire_date"] == "2026-09-22"


def test_long_only_domain_blocks_short_intent_shape() -> None:
    with pytest.raises(ValueError):
        OptionOrderRequest(
            account_id="acct-test",
            client_order_id="bad",
            underlying="SPY",
            strike_price=680.0,
            expiration_date="2026-09-22",
            option_type="CALL",
            side="SELL",
            position_intent="BUY_TO_OPEN",
            quantity=1,
            limit_price=1.0,
        )


def test_execution_layer_rejects_non_spy_orders() -> None:
    with pytest.raises(ValueError):
        OptionOrderRequest(
            account_id="acct-test",
            client_order_id="not-spy",
            underlying="AAPL",
            strike_price=200.0,
            expiration_date="2026-09-22",
            option_type="CALL",
            side="BUY",
            position_intent="BUY_TO_OPEN",
            quantity=1,
            limit_price=1.0,
        )


def test_replace_order_only_changes_quantity_and_limit() -> None:
    item = build_replace_item(
        ReplaceOrderRequest(
            account_id="acct-test",
            client_order_id="spy-20260922-001",
            quantity=2,
            limit_price=1.18,
        )
    )
    assert item == {
        "client_order_id": "spy-20260922-001",
        "quantity": "2",
        "limit_price": "1.18",
    }
