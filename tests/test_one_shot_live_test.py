"""No-network tests for the single authorized, $25-limit LIVE broker round trip.

Never invokes the actual Webull SDK. A successful test does NOT imply
instantaneous real-market fills or availability of a cheap/liquid 0DTE option.
"""
from dataclasses import replace
from datetime import timedelta
import pytest

from test_live_autonomy import rig
from engine.one_shot_live_test import (
    TestConfig, broker_preview_permits, used_before,
)


@pytest.fixture
def one_shot(rig, monkeypatch):
    monkeypatch.setenv("LIVE_ONE_SHOT_TEST", "true")
    monkeypatch.setenv("LIVE_ONE_SHOT_TEST_DATE", rig.clock[0].date().isoformat())
    monkeypatch.setenv("LIVE_ONE_SHOT_TEST_MAX_TOTAL_USD", "25")
    rig.strategy.unrestricted_v5_live = True
    rig.broker.cash = rig.broker.equity = 100
    rig.provider.bid, rig.provider.ask = .18, .19
    raw = rig.provider.collect_once
    rig.provider.collect_once = lambda date: [
        replace(item, bid_size=10, ask_size=10) for item in raw(date)
    ]
    rig.broker.previews = []
    def preview(order):
        rig.broker.previews.append(order)
        return {"result": True, "estimated_fee": 1.0}
    rig.broker.preview_option_order = preview
    return rig


def test_one_single_live_buy_then_immediate_sell_with_fresh_bid(one_shot):
    rig = one_shot
    assert rig.executor.tick()["state"] == "ORDER_SUBMITTED"
    buy = rig.broker.submissions[0]
    assert buy.side == "BUY" and buy.quantity == 1
    assert buy.limit_price * 100 <= 20.0
    assert len(rig.broker.previews) == 1
    assert used_before(rig.store.orders())

    rig.broker.fill(index=0, price=.19)
    exit_result = rig.executor.tick()
    assert exit_result["state"] == "ORDER_SUBMITTED"
    sell = rig.broker.submissions[1]
    assert sell.side == "SELL" and sell.position_intent == "SELL_TO_CLOSE"
    assert sell.quantity == 1 and sell.limit_price == .18
    rig.broker.fill(index=1, price=.18)
    assert rig.executor.tick()["state"] == "ONE_SHOT_ALREADY_USED"
    assert rig.store.positions() == []
    assert len(rig.broker.submissions) == 2

    rig.executor = rig.create()  # persists across worker restart
    assert rig.executor.tick()["state"] == "ONE_SHOT_ALREADY_USED"
    assert len(rig.broker.submissions) == 2


def test_only_one_buy_even_when_entry_cancelled_or_unacknowledged(one_shot):
    rig = one_shot
    rig.broker.unknown = True
    assert rig.executor.tick()["state"] == "ORDER_UNKNOWN"
    assert len(rig.broker.submissions) == 1
    rig.clock[0] += timedelta(seconds=16)
    assert rig.executor.tick()["state"] == "ORDER_UNKNOWN"
    assert len(rig.broker.submissions) == 1
    rig.broker.unknown = False
    # Reconciliation must not create another entry, even if the broker later
    # acknowledges a cancelled or failed original order.
    original = rig.broker.submissions[0].client_order_id
    rig.broker.details[original] = {"orders": [{
        "client_order_id": original, "side": "BUY", "status": "CANCELLED",
        "total_quantity": "1", "filled_quantity": "0", "filled_price": None}]}
    assert rig.executor.tick()["state"] == "ONE_SHOT_ALREADY_USED"
    assert len(rig.broker.submissions) == 1


def test_no_cheap_verified_quote_means_no_trade(one_shot):
    rig = one_shot
    rig.provider.ask = 1.00
    rig.provider.bid = .99
    result = rig.executor.tick()
    assert result["state"] == "ONE_SHOT_NO_LIQUID_AFFORDABLE_QUOTE"
    assert not rig.broker.previews and not rig.broker.submissions


def test_preview_rejected_aborts_before_broker_order(one_shot):
    rig = one_shot
    rig.broker.preview_option_order = lambda order: {"result": False}
    assert rig.executor.tick()["state"] == "ONE_SHOT_PREVIEW_REJECTED"
    assert not rig.store.orders() and not rig.broker.submissions


def test_preview_error_aborts_before_broker_order(one_shot):
    rig = one_shot
    def reject(order):
        raise TimeoutError("simulated provider preview timeout")
    rig.broker.preview_option_order = reject
    assert rig.executor.tick()["state"] == "ONE_SHOT_PREVIEW_UNAVAILABLE"
    assert not rig.store.orders() and not rig.broker.submissions


def test_no_auth_bypass_after_user_disarms_one_shot(one_shot):
    rig = one_shot
    rig.store.disable(rig.clock[0])
    assert rig.executor.tick()["state"] == "PAUSED"
    assert not rig.broker.submissions
    rig.mode[0] = "PAPER"
    assert not rig.broker.submissions


def test_date_expiration_blocks_all_normal_v5_buys(one_shot):
    rig = one_shot
    rig.clock[0] += timedelta(days=1)
    assert rig.executor.tick()["state"] == "ONE_SHOT_EXPIRED_NO_MORE_BUYS"
    assert not rig.broker.submissions


def test_quote_dropout_after_fill_cannot_promise_instant_sale(one_shot):
    rig = one_shot
    assert rig.executor.tick()["state"] == "ORDER_SUBMITTED"
    rig.broker.fill(price=.19)
    rig.provider.delay = 900
    assert rig.executor.tick()["state"] == "EXIT_WAITING_QUOTE"
    assert len(rig.broker.submissions) == 1
    assert rig.store.positions()[0]["quantity"] == 1


def test_budget_and_rejection_schema_fail_closed(one_shot):
    rig = one_shot
    rig.broker.cash = rig.broker.equity = 22.0
    assert rig.executor.tick()["state"] == "ONE_SHOT_NO_LIQUID_AFFORDABLE_QUOTE"
    assert not rig.broker.submissions
    for invalid in ({}, {"result": False}, {"error_code": 12001}, {"status": "rejected"}):
        assert not broker_preview_permits(invalid)
