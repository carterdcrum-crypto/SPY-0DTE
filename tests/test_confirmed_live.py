from __future__ import annotations

import pytest

from engine.broker import OptionOrderRequest
from engine.confirmed_live import ConfirmedLiveCoordinator, LiveSafetyError, LiveSafetyPolicy
from engine.live_order_ticket import (
    CONFIRMATION_PHRASE,
    LiveOrderAuthorizationError,
    LiveOrderTicketStore,
)
from engine.tradier_live import TradierAccountProfile


def _order(*, quantity: int = 1, limit_price: float = 0.42) -> OptionOrderRequest:
    return OptionOrderRequest(
        account_id="VA000001",
        client_order_id="spy0dte-live-001",
        underlying="SPY",
        strike_price=770.0,
        expiration_date="2026-09-25",
        option_type="CALL",
        side="BUY",
        position_intent="BUY_TO_OPEN",
        quantity=quantity,
        limit_price=limit_price,
    )


class FakeTradierClient:
    def __init__(self) -> None:
        self.previewed = []

    def account_profile(self):
        return TradierAccountProfile(
            account_number="VA000001",
            account_type="cash",
            option_level=2,
            status="active",
        )

    def preview_option_order(self, order):
        self.previewed.append(order)
        return {"order": {"status": "ok", "result": True, "order_cost": 42.0}}

    def balances(self, account_id):
        return {"balances": {"total_cash": 1000.0}}

    def positions(self, account_id):
        return {"positions": {"position": []}}

    def account_orders(self, account_id, *, limit=100, status=None):
        return {"orders": {"order": []}}


def test_ticket_is_exact_order_single_use(tmp_path):
    store = LiveOrderTicketStore(tmp_path / "tickets.sqlite")
    order = _order()
    token = store.issue(
        order,
        user_subject="owner-1",
        explicit_confirmation=CONFIRMATION_PHRASE,
        ttl_seconds=45,
    )

    auth = store.consume(token, order, user_subject="owner-1")
    assert auth.order_fingerprint
    assert auth.user_subject == "owner-1"

    with pytest.raises(LiveOrderAuthorizationError, match="already been used"):
        store.consume(token, order, user_subject="owner-1")


def test_ticket_rejects_changed_order(tmp_path):
    store = LiveOrderTicketStore(tmp_path / "tickets.sqlite")
    token = store.issue(
        _order(),
        user_subject="owner-1",
        explicit_confirmation=CONFIRMATION_PHRASE,
    )

    with pytest.raises(LiveOrderAuthorizationError, match="does not match"):
        store.consume(token, _order(limit_price=0.43), user_subject="owner-1")


def test_ticket_requires_exact_confirmation_phrase(tmp_path):
    store = LiveOrderTicketStore(tmp_path / "tickets.sqlite")
    with pytest.raises(LiveOrderAuthorizationError, match="confirmation phrase"):
        store.issue(
            _order(),
            user_subject="owner-1",
            explicit_confirmation="yes",
        )


def test_safety_policy_caps_contracts_and_debit():
    policy = LiveSafetyPolicy(max_contracts=2, max_order_debit=100.0)
    policy.validate(_order(quantity=2, limit_price=0.50))

    with pytest.raises(LiveSafetyError, match="contract ceiling"):
        policy.validate(_order(quantity=3, limit_price=0.10))

    with pytest.raises(LiveSafetyError, match="dollar ceiling"):
        policy.validate(_order(quantity=2, limit_price=0.51))


def test_prepare_previews_then_issues_short_lived_ticket(tmp_path):
    client = FakeTradierClient()
    coordinator = ConfirmedLiveCoordinator(
        client=client,
        ticket_store=LiveOrderTicketStore(tmp_path / "tickets.sqlite"),
        policy=LiveSafetyPolicy(
            max_contracts=2,
            max_order_debit=100.0,
            confirmation_ttl_seconds=30,
        ),
    )
    order = _order()

    prepared = coordinator.prepare(
        order,
        user_subject="owner-1",
        explicit_confirmation=CONFIRMATION_PHRASE,
    )
    assert prepared.ticket_token
    assert prepared.expires_in_seconds == 30
    assert prepared.preview["order"]["result"] is True
    assert client.previewed == [order]

    auth = coordinator.consume_authorization(
        prepared.ticket_token,
        order,
        user_subject="owner-1",
    )
    assert auth.user_subject == "owner-1"


def test_reconcile_reads_broker_source_of_truth(tmp_path):
    coordinator = ConfirmedLiveCoordinator(
        client=FakeTradierClient(),
        ticket_store=LiveOrderTicketStore(tmp_path / "tickets.sqlite"),
        policy=LiveSafetyPolicy(max_contracts=1, max_order_debit=100.0),
    )
    state = coordinator.reconcile()
    assert state["account"]["is_cash"] is True
    assert "balances" in state
    assert "positions" in state
    assert "orders" in state
