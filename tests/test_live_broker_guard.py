from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from engine.live_alert_policy import build_live_entry_alert
from engine.live_broker_guard import evaluate_live_broker_guard
from engine.live_risk import LiveRiskEnvelope
from engine.tradier_live import TradierAccountProfile


EASTERN = ZoneInfo("America/New_York")


class FakeTradierClient:
    def __init__(
        self,
        *,
        close_pl: float = 0.0,
        open_pl: float = 0.0,
        cash_available: float = 1000.0,
        total_equity: float = 1000.0,
        pending_orders: int = 0,
        positions: list[dict] | None = None,
    ) -> None:
        self.close_pl = close_pl
        self.open_pl = open_pl
        self.cash_available = cash_available
        self.total_equity = total_equity
        self.pending_orders = pending_orders
        self.position_items = positions or []

    def account_profile(self):
        return TradierAccountProfile(
            account_number="VA000001",
            account_type="cash",
            option_level=2,
            status="active",
        )

    def balances(self, account_id):
        return {
            "balances": {
                "close_pl": self.close_pl,
                "open_pl": self.open_pl,
                "total_cash": self.cash_available,
                "total_equity": self.total_equity,
                "pending_orders_count": self.pending_orders,
                "cash": {"cash_available": self.cash_available},
            }
        }

    def positions(self, account_id):
        return {"positions": {"position": self.position_items}}


def _envelope() -> LiveRiskEnvelope:
    today = datetime.now(timezone.utc).astimezone(EASTERN).date().isoformat()
    return LiveRiskEnvelope(
        trading_date=today,
        daily_loss_limit=25.0,
        daily_gain_limit=40.0,
        max_account_exposure_pct=0.20,
        max_contracts=3,
        updated_at=datetime.now(timezone.utc).isoformat(),
    )


def _automation(*, contracts: int = 5, contract_cost: float = 80.0):
    return {
        "state": "SHADOW_SIGNAL",
        "last_tick": datetime.now(timezone.utc).isoformat(),
        "strategy": "market_ensemble_v1",
        "reason": "qualified signal",
        "last_signal": {
            "symbol": "SPY260925C00770000",
            "right": "call",
            "ask": 0.80,
            "bid": 0.78,
            "contract_cost": contract_cost,
        },
        "risk": {
            "allowed": True,
            "contracts": contracts,
            "sizing_mode": "dynamic",
        },
    }


def test_guard_uses_broker_pnl_and_exposure_budget():
    guard = evaluate_live_broker_guard(FakeTradierClient(), _envelope())
    assert guard.entry_allowed is True
    assert guard.daily_total_pnl == 0.0
    assert guard.open_positions == 0
    assert guard.pending_orders_count == 0
    assert guard.max_entry_debit == 200.0


def test_guard_blocks_after_daily_loss_or_gain_stop():
    loss = evaluate_live_broker_guard(FakeTradierClient(close_pl=-25.01), _envelope())
    assert loss.entry_allowed is False
    assert "daily_loss_stop_reached" in loss.reasons
    assert loss.daily_stop_reached is True

    gain = evaluate_live_broker_guard(FakeTradierClient(close_pl=40.0), _envelope())
    assert gain.entry_allowed is False
    assert "daily_gain_stop_reached" in gain.reasons
    assert gain.daily_stop_reached is True


def test_guard_blocks_new_entry_when_position_or_order_exists():
    position = evaluate_live_broker_guard(
        FakeTradierClient(positions=[{"symbol": "SPY260925C00770000", "quantity": 1}]),
        _envelope(),
    )
    assert "position_already_open" in position.reasons

    pending = evaluate_live_broker_guard(FakeTradierClient(pending_orders=1), _envelope())
    assert "broker_order_pending" in pending.reasons


def test_live_alert_contracts_are_capped_by_contract_and_exposure_limits():
    guard = evaluate_live_broker_guard(FakeTradierClient(), _envelope())
    alert = build_live_entry_alert(_automation(contracts=5, contract_cost=80.0), _envelope(), guard)
    assert alert is not None
    risk = alert["risk"]
    assert risk["model_contracts"] == 5
    assert risk["daily_contract_ceiling"] == 3
    assert risk["contracts"] == 2
    assert risk["bounded_entry_debit"] == 160.0
    assert alert["broker_submitted"] is False


def test_live_alert_is_suppressed_when_broker_guard_blocks_entry():
    guard = evaluate_live_broker_guard(
        FakeTradierClient(positions=[{"symbol": "SPY260925C00770000"}]),
        _envelope(),
    )
    assert build_live_entry_alert(_automation(), _envelope(), guard) is None
