from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from engine.live_autonomy import LiveExecutor, account_values, broker_inventory, parse_order_detail
from engine.live_autonomy_store import ARM_AUTONOMY, LiveAutonomyStore
from engine.live_market import LiveMarketReader, LiveDataProvider
from engine.live_risk import LiveRiskEnvelope
from engine.providers.webull_free import FreeOptionSnapshot
from engine.webull_live import strict_rows, WebullLiveError

NOW = datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc)
SYMBOL = "SPY260928C00660000"


class Broker:
    def __init__(self):
        self.submissions, self.cancels = [], []
        self.details, self.inventory = {}, {}
        self.uncertain = False
        self.unknown = False
        self.day_pnl = 0
        self.cash = 10000
        self.equity = 10000
        self.cash_account = True
        self.external_orders = []

    def account_profile(self):
        return SimpleNamespace(account_id="acct", is_cash=self.cash_account)

    def balances(self, account):
        return {"total_net_liquidation_value": self.equity, "total_day_profit_loss": self.day_pnl,
                "account_currency_assets": [{"currency": "USD", "settled_cash": self.cash, "buying_power": self.cash}]}

    def positions(self, account):
        return [{"symbol": s, "quantity": q} for s,q in self.inventory.items() if q]

    def open_orders(self, account):
        return self.external_orders

    def place_option_order(self, order):
        self.submissions.append(order)
        if not self.unknown:
            self.details[order.client_order_id] = {"orders": [{
                "client_order_id": order.client_order_id, "side": order.side,
                "status": "SUBMITTED", "total_quantity": str(order.quantity),
                "filled_quantity": "0", "filled_price": None,
            }]}
        if self.uncertain or self.unknown:
            raise TimeoutError("sensitive response must not appear in telemetry")
        return {}

    def order_detail(self, account, client_id):
        return self.details.get(client_id)

    def cancel_order(self, account, client_id):
        self.cancels.append(client_id)
        if client_id in self.details:
            self.details[client_id]["orders"][0]["status"] = "CANCELLED"

    def fill(self, index=-1, *, quantity=None, price=1.0):
        order = self.submissions[index]
        detail = self.details[order.client_order_id]["orders"][0]
        quantity = order.quantity if quantity is None else quantity
        difference = quantity - int(detail["filled_quantity"])
        detail.update(status="FILLED" if quantity == order.quantity else "PARTIAL_FILLED",
                      filled_quantity=str(quantity), filled_price=str(price))
        symbol = f"SPY{datetime.fromisoformat(order.expiration_date):%y%m%d}{order.option_type[0]}{round(order.strike_price*1000):08d}"
        self.inventory[symbol] = self.inventory.get(symbol, 0) + difference*(1 if order.side == "BUY" else -1)


class Provider:
    def __init__(self, clock):
        self.clock = clock
        self.delay = 0
        self.quality = "option_quote_timestamp"
        self.source = "webull_production"
        self.bid = .99

    def collect_once(self, day):
        now = self.clock()
        return [FreeOptionSnapshot(
            timestamp=now-timedelta(seconds=self.delay), received_at=now, timestamp_quality=self.quality,
            feed_delay_seconds=self.delay, option_symbol=f"SPY{day:%y%m%d}C00660000", expiration=day,
            right="call", strike=660, bid=self.bid, ask=1.0, underlying_price=660,
            minutes_to_expiry=300, volume=2000, open_interest=3000, source=self.source,
            underlying_timestamp=now,
        )]


class Strategy:
    def __init__(self):
        self.exit_reason = None
        self.quantity = 3
        self.after_decision = lambda: None

    def entry(self, now, account, daily, limits):
        self.after_decision()
        return {"symbol": f"SPY{now:%y%m%d}C00660000", "cycle": now.isoformat(),
                "quantity": self.quantity, "reason": "test signal"}, {"state": "SIGNAL", "reason": "test"}

    def exit(self, position, now):
        return self.exit_reason

    def _minutes_to_close(self, now):
        return 300


@pytest.fixture
def rig(tmp_path):
    clock = [NOW]
    store = LiveAutonomyStore(tmp_path/"live.sqlite")
    limits = LiveRiskEnvelope(None, 1000, 1000, .20, 3, None)
    store.enable(owner="owner", limits=limits, confirmation=ARM_AUTONOMY, now=NOW)
    broker, strategy = Broker(), Strategy()
    provider = Provider(lambda: clock[0])
    mode = ["LIVE"]
    allowed = [True]
    def create():
        return LiveExecutor(store=LiveAutonomyStore(store.path), broker=broker, market=LiveMarketReader(),
                            provider=provider, strategy=strategy, mode_getter=lambda: mode[0],
                            writes_allowed=lambda: allowed[0], clock=lambda: clock[0])
    return SimpleNamespace(clock=clock, store=store, broker=broker, strategy=strategy, provider=provider,
                           mode=mode, allowed=allowed, create=create, executor=create())


def test_timeout_after_acceptance_recovers_same_order_after_restart(rig):
    rig.broker.uncertain = True
    assert rig.executor.tick()["state"] == "ORDER_UNKNOWN"
    client_id = rig.broker.submissions[0].client_order_id
    rig.executor = rig.create()
    assert rig.executor.tick()["state"] == "ORDER_PENDING"
    assert len(rig.broker.submissions) == 1
    rig.broker.fill()
    assert rig.executor.tick()["state"] == "POSITION_OPEN"
    assert rig.executor.tick()["state"] == "POSITION_OPEN"
    assert rig.store.positions()[0]["quantity"] == 3
    assert rig.store.positions()[0]["position_id"] == client_id


def test_unknown_order_never_blindly_reposts(rig):
    rig.broker.unknown = True
    rig.executor.tick()
    for _ in range(4):
        rig.clock[0] += timedelta(seconds=20)
        rig.executor = rig.create()
        assert rig.executor.tick()["state"] == "ORDER_UNKNOWN"
    assert len(rig.broker.submissions) == 1
    assert set(rig.broker.cancels) == {rig.broker.submissions[0].client_order_id}


def test_partial_buy_cancel_then_stop_sells_only_filled_quantity(rig):
    rig.executor.tick()
    rig.broker.fill(quantity=1, price=.98)
    rig.store.disable(rig.clock[0])
    assert rig.executor.tick()["state"] == "ORDER_PENDING"
    assert len(rig.broker.submissions) == 1  # cancel acknowledgement is not a terminal fill snapshot
    assert rig.executor.tick()["state"] == "ORDER_SUBMITTED"
    exit_order = rig.broker.submissions[-1]
    assert (exit_order.side, exit_order.position_intent, exit_order.quantity) == ("SELL", "SELL_TO_CLOSE", 1)
    rig.broker.fill(price=.99)
    assert rig.executor.tick()["state"] == "PAUSED"
    assert rig.store.positions() == []


def test_partial_exit_cancel_reprices_remaining_only_and_persists_exit_intent(rig):
    rig.executor.tick()
    rig.broker.fill()
    rig.strategy.exit_reason = "take_profit"
    rig.executor.tick()
    rig.broker.fill(quantity=1)
    rig.clock[0] += timedelta(seconds=12)
    rig.executor.tick()
    rig.strategy.exit_reason = None
    rig.executor = rig.create()
    rig.executor.tick()
    assert [o.quantity for o in rig.broker.submissions] == [3,3,2]
    assert [o.side for o in rig.broker.submissions] == ["BUY","SELL","SELL"]


@pytest.mark.parametrize("change", ["delayed", "paper_source", "untrusted", "future", "late_model"])
def test_paper_stale_future_and_untrusted_quotes_never_execute(rig, change):
    if change == "delayed": rig.provider.delay = 900
    if change == "future": rig.provider.delay = -10
    if change == "paper_source": rig.provider.source = "webull_sandbox_delayed"
    if change == "untrusted": rig.provider.quality = "underlying_trade_anchor"
    if change == "late_model": rig.strategy.after_decision = lambda: rig.clock.__setitem__(0, rig.clock[0]+timedelta(seconds=4))
    assert rig.executor.tick()["state"] == "WAITING_FRESH_DATA"
    assert rig.broker.submissions == []


def test_stop_racing_strategy_decision_cannot_reserve_buy(rig):
    rig.strategy.after_decision = lambda: rig.store.disable(rig.clock[0])
    assert rig.executor.tick()["state"] == "RECONCILING"
    assert rig.broker.submissions == []


def test_balance_may_not_fall_back_to_unsettled_total():
    with pytest.raises(ValueError, match="settled cash"):
        account_values({"total_cash_balance": 10000, "total_net_liquidation_value": 10000,
                        "total_day_profit_loss": 0, "account_currency_assets": [{"currency":"USD", "buying_power":10000}]})


def test_full_premium_plus_fees_and_daily_loss_allowance_cap_entry(rig):
    rig.broker.cash = 205
    rig.broker.day_pnl = -850
    rig.executor.tick()
    assert rig.broker.submissions[0].quantity == 1


def test_daily_stop_persists_if_pnl_recovers_and_resumes_next_session(rig):
    rig.broker.day_pnl = -1000
    assert rig.executor.tick()["state"] == "DAILY_HALT"
    rig.broker.day_pnl = 0
    rig.executor = rig.create()
    assert rig.executor.tick()["state"] == "DAILY_HALT"
    rig.clock[0] += timedelta(days=1)
    assert rig.executor.tick()["state"] == "ORDER_SUBMITTED"
    assert rig.store.policy()["enabled"]


def test_margin_account_and_changed_account_are_blocked(rig):
    rig.broker.cash_account = False
    assert rig.executor.tick()["state"] == "LIVE_LOCKED"
    rig.broker.cash_account = True
    rig.store.pin_account("other-account")
    with pytest.raises(ValueError, match="account changed"):
        rig.executor.tick()
    assert rig.broker.submissions == []


def test_external_position_or_open_order_prevents_entry(rig):
    rig.broker.inventory[SYMBOL] = 1
    assert rig.executor.tick()["state"] == "UNMANAGED_POSITION"
    rig.broker.inventory.clear()
    rig.broker.external_orders = [{"client_order_id":"external"}]
    assert rig.executor.tick()["state"] == "RECONCILING"
    assert rig.broker.submissions == []


def test_disabled_write_gate_never_calls_broker(rig):
    rig.allowed[0] = False
    rig.broker.account_profile = lambda: pytest.fail("must not access broker when execution is locked")
    assert rig.executor.tick()["state"] == "LIVE_LOCKED"


def test_broker_inventory_mismatch_does_not_oversell(rig):
    rig.executor.tick()
    rig.broker.fill()
    rig.broker.inventory[SYMBOL] = 1
    rig.strategy.exit_reason = "stop_loss"
    assert rig.executor.tick()["state"] == "POSITION_RECONCILIATION"
    assert len(rig.broker.submissions) == 1


def test_failed_balance_read_does_not_prevent_owned_exit(rig):
    rig.executor.tick()
    rig.broker.fill()
    rig.strategy.exit_reason = "stop_loss"
    rig.broker.balances = lambda _: {}
    assert rig.executor.tick()["side"] == "SELL"


def test_cumulative_fill_validation_and_no_duplicate_inventory(rig):
    rig.executor.tick()
    row = rig.store.orders()[0]
    kwargs = dict(client_id=row["client_id"], now=NOW)
    rig.store.reconcile(**kwargs, status="PARTIAL_FILLED", filled=1, average_price=1)
    rig.store.reconcile(**kwargs, status="PARTIAL_FILLED", filled=1, average_price=1)
    assert rig.store.positions()[0]["quantity"] == 1
    for filled,price,status in [(0,1,"SUBMITTED"),(4,1,"FILLED"),(2,float("nan"),"PARTIAL_FILLED"),(1,1,"FILLED")]:
        with pytest.raises(ValueError):
            rig.store.reconcile(**kwargs, status=status, filled=filled, average_price=price)


def test_real_webull_order_and_position_response_shape(rig):
    rig.executor.tick()
    order = rig.store.orders()[0]["request"]
    status,filled,price = parse_order_detail({"client_order_id": order["client_order_id"], "combo_type":"NORMAL",
        "orders":[{"client_order_id":order["client_order_id"], "side":"BUY", "status":"PARTIAL_FILLED",
                   "total_quantity":"3", "filled_quantity":"1", "filled_price":"1.02"}]}, order)
    assert (status,filled,price) == ("PARTIAL_FILLED",1,1.02)
    inventory,foreign = broker_inventory([{"symbol":"SPY", "quantity":"2", "instrument_type":"OPTION",
        "legs":[{"symbol":"SPY", "quantity":"2", "option_type":"CALL", "option_expire_date":"2026-09-28",
                 "option_exercise_price":"660", "option_contract_multiplier":"100", "option_contract_deliverable":"100"}]}])
    assert inventory == {SYMBOL:2} and not foreign


@pytest.mark.parametrize("payload", [{},None,{"error_code":"NO_PERMISSION"},{"data":None},{"data":[],"has_next":True}])
def test_bad_broker_response_is_not_an_empty_account(payload):
    with pytest.raises(WebullLiveError): strict_rows(payload)


def test_held_contract_keeps_quote_slot():
    provider = object.__new__(LiveDataProvider)
    provider.pinned_symbols = {"held"}
    rows = tuple({"symbol":str(i), "strike_price":100+i} for i in range(25)) + ({"symbol":"held", "strike_price":200},)
    selected = provider.select_contracts(rows, 101)
    assert len(selected) == 20
    assert any(r["symbol"] == "held" for r in selected)


def test_live_strategy_constructs_without_a_paper_account(tmp_path):
    from engine.live_strategy import LiveStrategy
    strategy = LiveStrategy(LiveMarketReader(), str(tmp_path/"research.sqlite"))
    assert not strategy.risk_config.allow_minimum_viable_contract
    assert not hasattr(strategy.account_store, "open_long")
    with pytest.raises(RuntimeError, match="decisions only"):
        strategy.tick()


def test_early_close_cancels_entry_and_exits_before_close(rig):
    rig.clock[0] = datetime(2026,11,27,17,49,tzinfo=timezone.utc) # 12:49 ET
    assert rig.executor.tick()["state"] == "ENTRY_WINDOW_CLOSED"
    assert rig.broker.submissions == []


def test_preview_cannot_authorize_autonomy(tmp_path, monkeypatch):
    from fastapi import HTTPException
    from engine import api, webull_live_api
    monkeypatch.setenv("LIVE_AUTONOMY_DB_PATH", str(tmp_path/"live.sqlite"))
    monkeypatch.setenv("APP_PREVIEW_MODE", "true")
    with pytest.raises(HTTPException) as exc:
        webull_live_api.set_live_autonomy(webull_live_api.LiveAutonomyRequest(enabled=True, confirmation=ARM_AUTONOMY),
                                         api.UserIdentity(subject="preview", email="preview@local"))
    assert exc.value.status_code == 403
    assert not LiveAutonomyStore(tmp_path/"live.sqlite").policy()["enabled"]
