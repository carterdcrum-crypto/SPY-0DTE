from __future__ import annotations

from dataclasses import dataclass

import pytest

from engine.broker import OptionOrderRequest
from engine.live_risk import LiveRiskEnvelope
from engine.webull_live import WebullLiveClient, WebullLiveError, evaluate_webull_guard
from engine.webull_live_api import parse_occ_symbol


@dataclass
class Response:
    payload: object
    status_code: int = 200

    def json(self):
        return self.payload


class AccountV2:
    def __init__(self, accounts, balance, positions):
        self._accounts = accounts
        self._balance = balance
        self._positions = positions

    def get_account_list(self):
        return Response({"data": self._accounts})

    def get_account_balance(self, account_id):
        return Response(self._balance)

    def get_account_position(self, account_id):
        return Response({"data": self._positions})


class OrderV3:
    def __init__(self, open_orders=()):
        self._open_orders = list(open_orders)
        self.preview_calls = []
        self.place_calls = []

    def get_order_open(self, account_id):
        return Response({"data": self._open_orders})

    def preview_order(self, account_id, orders):
        self.preview_calls.append((account_id, orders))
        return Response({"estimated_cost": "12.34"})

    def place_order(self, account_id, orders):
        self.place_calls.append((account_id, orders))
        return Response({"client_order_id": orders[0]["client_order_id"], "status": "PENDING"})

    def get_order_detail(self, account_id, client_order_id):
        return Response({"client_order_id": client_order_id, "status": "WORKING"})


class TradeClient:
    def __init__(self, *, accounts, balance, positions=(), open_orders=()):
        self.account_v2 = AccountV2(accounts, balance, positions)
        self.order_v3 = OrderV3(open_orders)


def client(*, account_type="CASH", balance=None, positions=(), open_orders=()):
    balance = balance or {
        "total_cash_balance": "115.00",
        "total_net_liquidation_value": "115.00",
        "total_day_profit_loss": "0.00",
        "total_unrealized_profit_loss": "0.00",
        "account_currency_assets": [
            {"currency": "USD", "settled_cash": "115.00", "buying_power": "115.00"}
        ],
    }
    trade = TradeClient(
        accounts=[
            {
                "account_id": "acct-1",
                "account_number": "U1",
                "account_type": account_type,
            }
        ],
        balance=balance,
        positions=positions,
        open_orders=open_orders,
    )
    return WebullLiveClient("key", "secret", trade_client=trade), trade


def envelope(**overrides):
    values = dict(
        trading_date="2099-09-26",
        daily_loss_limit=25.0,
        daily_gain_limit=40.0,
        max_account_exposure_pct=0.20,
        max_contracts=3,
        updated_at="2099-09-26T12:00:00+00:00",
    )
    values.update(overrides)
    return LiveRiskEnvelope(**values)


def order():
    return OptionOrderRequest(
        account_id="acct-1",
        client_order_id="0123456789abcdef0123456789abcdef",
        underlying="SPY",
        strike_price=770.0,
        expiration_date="2026-09-25",
        option_type="CALL",
        side="BUY",
        position_intent="BUY_TO_OPEN",
        quantity=1,
        limit_price=0.52,
    )


def test_occ_symbol_parser():
    assert parse_occ_symbol("SPY260925C00770000") == ("SPY", "2026-09-25", "CALL", 770.0)
    with pytest.raises(ValueError):
        parse_occ_symbol("not-an-occ-symbol")


def test_cash_account_is_selected_and_option_can_be_previewed_then_placed():
    broker, trade = client()
    profile = broker.account_profile()
    assert profile.account_id == "acct-1"
    assert profile.is_cash is True

    preview = broker.preview_option_order(order())
    placed = broker.place_confirmed_option_order(order())
    assert preview["estimated_cost"] == "12.34"
    assert placed["status"] == "PENDING"
    assert trade.order_v3.preview_calls[0][1][0]["position_intent"] == "BUY_TO_OPEN"
    assert trade.order_v3.place_calls[0][1][0]["legs"][0]["symbol"] == "SPY"


def test_multiple_cash_accounts_require_explicit_account_id():
    trade = TradeClient(
        accounts=[
            {"account_id": "a", "account_number": "U1", "account_type": "CASH"},
            {"account_id": "b", "account_number": "U2", "account_type": "CASH"},
        ],
        balance={},
    )
    broker = WebullLiveClient("key", "secret", trade_client=trade)
    with pytest.raises(WebullLiveError, match="multiple Webull cash accounts"):
        broker.account_profile()


def test_guard_enforces_cash_one_position_pending_orders_and_daily_stop():
    broker, _ = client(
        balance={
            "total_cash_balance": "100.00",
            "total_net_liquidation_value": "110.00",
            "total_day_profit_loss": "-25.00",
            "total_unrealized_profit_loss": "-3.00",
            "account_currency_assets": [
                {"currency": "USD", "settled_cash": "100.00", "buying_power": "100.00"}
            ],
        },
        positions=[{"symbol": "SPY260925C00770000", "quantity": "1"}],
        open_orders=[{"client_order_id": "pending"}],
    )
    guard = evaluate_webull_guard(broker, envelope())
    assert guard.entry_allowed is False
    assert guard.daily_total_pnl == -25.0
    assert guard.max_entry_debit == 20.0
    assert "daily_loss_stop_reached" in guard.reasons
    assert "position_already_open" in guard.reasons
    assert "broker_order_pending" in guard.reasons


def test_margin_account_is_never_entry_eligible():
    broker, _ = client(account_type="MARGIN")
    guard = evaluate_webull_guard(broker, envelope())
    assert guard.entry_allowed is False
    assert "broker_account_not_cash" in guard.reasons
