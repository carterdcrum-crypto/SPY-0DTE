"""Regression tests: software stop exit reprice is safe despite partial fills and stale quotes."""
from datetime import timedelta
from engine.live_autonomy import LiveExecutor


def open_long_and_exit(rig, *, quantity=3):
    assert rig.executor.tick()["state"] == "ORDER_SUBMITTED"
    rig.broker.fill(price=1.00)
    rig.strategy.exit_reason = "v5_paper_stop_35pct"
    first = rig.executor.tick()
    assert first["state"] == "ORDER_SUBMITTED"
    sell = rig.broker.submissions[-1]
    assert sell.side == "SELL"
    assert sell.quantity == quantity
    return sell


def test_protective_sell_cancel_requested_after_five_seconds_of_worse_verified_bid(rig):
    original = open_long_and_exit(rig)
    rig.clock[0] += timedelta(seconds=6)
    rig.provider.bid = 0.72
    rig.provider.ask = 0.74
    result = rig.executor.tick()
    assert result["state"] == "ORDER_PENDING"
    assert rig.broker.cancels == [original.client_order_id]
    # No second sell until Webull confirms original cancelled; broker fake
    # requires another reconciliation cycle to report that final state.
    assert len(rig.broker.submissions) == 2
    result = rig.executor.tick()
    assert result["state"] == "ORDER_SUBMITTED"
    replacement = rig.broker.submissions[-1]
    assert replacement.client_order_id != original.client_order_id
    assert replacement.limit_price == 0.72
    assert replacement.quantity == 3
    assert len(rig.broker.submissions) == 3
    rig.broker.fill(price=.72)
    assert rig.executor.tick()["state"] == "POSITION_OPEN" or not rig.store.positions()
    assert rig.store.positions() == []


def test_no_bid_means_never_cancel_only_working_protective_sell(rig):
    original = open_long_and_exit(rig)
    rig.clock[0] += timedelta(seconds=14)
    rig.provider.delay = 900  # No authentic actionable option quote.
    result = rig.executor.tick()
    assert result["state"] == "ORDER_PENDING"
    assert rig.broker.cancels == []
    assert len(rig.broker.submissions) == 2
    assert rig.store.orders(pending_only=True)[0]["client_id"] == original.client_order_id


def test_no_unnecessary_cancel_when_fresh_bid_is_same_or_better(rig):
    original = open_long_and_exit(rig)
    rig.clock[0] += timedelta(seconds=13)
    rig.provider.bid = 1.01
    rig.provider.ask = 1.02
    assert rig.executor.tick()["state"] == "ORDER_PENDING"
    assert rig.broker.cancels == []
    assert rig.store.orders(pending_only=True)[0]["client_id"] == original.client_order_id


def test_partial_exit_reprices_only_confirmed_unsold_contracts(rig):
    original = open_long_and_exit(rig)
    rig.broker.fill(index=-1, quantity=1, price=.90)
    rig.clock[0] += timedelta(seconds=6)
    rig.provider.bid = .68
    rig.provider.ask = .70
    assert rig.executor.tick()["state"] == "ORDER_PENDING"
    assert rig.broker.cancels == [original.client_order_id]
    assert len(rig.broker.submissions) == 2
    assert rig.executor.tick()["state"] == "ORDER_SUBMITTED"
    assert rig.broker.submissions[-1].quantity == 2
    assert rig.broker.submissions[-1].limit_price == .68


def test_unknown_submission_is_not_replaced_until_broker_confirmed(rig):
    assert rig.executor.tick()["state"] == "ORDER_SUBMITTED"
    rig.broker.fill(price=1.)
    rig.strategy.exit_reason = "v5_paper_stop_35pct"
    rig.broker.uncertain = True
    assert rig.executor.tick()["state"] == "ORDER_UNKNOWN"
    original = rig.broker.submissions[-1]
    rig.broker.details.pop(original.client_order_id)  # ambiguous broker acceptance
    rig.clock[0] += timedelta(seconds=6)
    rig.provider.bid = .20
    assert rig.executor.tick()["state"] == "ORDER_UNKNOWN"
    assert len(rig.broker.submissions) == 2
    rig.clock[0] += timedelta(seconds=6)
    assert rig.executor.tick()["state"] == "ORDER_UNKNOWN"
    assert rig.broker.cancels == [original.client_order_id]
    assert len(rig.broker.submissions) == 2
