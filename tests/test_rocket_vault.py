"""Guardrails for the research-only profit-ratchet strategy."""
from datetime import datetime, timedelta, timezone

import pytest

from engine.backtest import BacktestConfig, BacktestContext, BacktestSignal, PositionView, run_backtest
from engine.data import HistoricalFrame
from engine.event_alpha import EventAlphaStrategy
from engine.market import MarketSnapshot, OptionQuote
from engine.rocket_vault import RocketVaultConfig, RocketVaultEventAlphaStrategy


def _option(*, bid=0.95, ask=1.00):
    return OptionQuote(
        symbol="CALL", right="call", strike=700.0,
        bid=bid, ask=ask, delta=0.45, gamma=0.02,
        theta=-1.0, vega=0.03, implied_volatility=0.20,
        volume=1000, open_interest=2000,
        underlying_price=700.0, minutes_to_expiry=120.0,
    )


def _frame(minute, *, bid=0.95, ask=1.00):
    return HistoricalFrame(
        timestamp=datetime(2026, 10, 1, 14, 0, tzinfo=timezone.utc) + timedelta(minutes=minute),
        market=MarketSnapshot(
            spot=700.0, bid=699.99, ask=700.01,
            realized_volatility=0.20, implied_volatility=0.20,
            volume_ratio=1.0, minutes_to_close=300.0 - minute, data_age_seconds=0.1,
        ),
        options=(_option(bid=bid, ask=ask),),
    )


def _flat(equity, settled=None):
    return BacktestContext(
        settled_cash=equity if settled is None else settled,
        unsettled_cash=0.0, equity=equity, position=None,
    )


def test_compounds_before_double_then_irreversibly_ratchets_realized_profits():
    strategy = RocketVaultEventAlphaStrategy(starting_cash=300)
    strategy._observe_flat_equity(590)
    assert strategy.locked_profit == 0
    strategy._observe_flat_equity(600)
    assert strategy.locked_profit == pytest.approx(195)
    strategy._observe_flat_equity(900)
    assert strategy.locked_profit == pytest.approx(440)
    strategy._observe_flat_equity(500)
    assert strategy.locked_profit == pytest.approx(440)
    assert strategy._entry_budget(_flat(500)) == pytest.approx(54)


def test_does_not_lock_unrealized_option_price_spike():
    strategy = RocketVaultEventAlphaStrategy(starting_cash=300)
    opened = BacktestContext(
        settled_cash=100, unsettled_cash=0, equity=1000,
        position=PositionView(
            option_symbol="CALL", quantity=2, entry_price=1.0,
            entry_cost=200.0, entry_timestamp=_frame(0).timestamp,
        ),
    )
    strategy.decide(_frame(1), opened)
    assert strategy.locked_profit == 0
    assert strategy.realized_high_watermark == 300
    strategy.decide(_frame(2), _flat(900))
    assert strategy.locked_profit > 0


def test_budget_uses_only_settled_cash_and_not_reserved_equity():
    strategy = RocketVaultEventAlphaStrategy(starting_cash=300)
    budget = strategy._entry_budget(_flat(300))
    signal = strategy._entry_signal(_option(), budget)
    assert signal.action == "open"
    assert signal.quantity == 2
    assert signal.max_total_cost == pytest.approx(270)
    strategy._observe_flat_equity(600)
    # Rising high-watermark does not make unsettled cash instantly spendable.
    assert strategy._entry_budget(_flat(600, settled=75)) == pytest.approx(67.5)
    assert strategy._entry_signal(_option(), 67.5).action == "hold"


def test_baseline_event_alpha_still_opens_one_contract_without_vault_cap():
    signal = EventAlphaStrategy()._entry_signal(_option(), 100)
    assert signal.quantity == 1
    assert signal.max_total_cost is None


class _SpendBudget:
    def __init__(self, cap):
        self.cap = cap
        self.n = 0

    def decide(self, frame, context):
        self.n += 1
        if self.n == 1:
            return BacktestSignal("open", "CALL", 2, "test", max_total_cost=self.cap)
        return BacktestSignal("hold")


def test_price_gap_cannot_consume_protected_cash_on_next_frame():
    result = run_backtest(
        [_frame(0), _frame(1, bid=2.85, ask=3.00)],
        _SpendBudget(cap=210),
        config=BacktestConfig(starting_cash=600, fee_per_contract=0, maximum_contracts=5),
    )
    assert result.metrics.trades == 0
    assert result.metrics.ending_equity == pytest.approx(600)


def test_budgeted_trade_still_executes_when_next_quote_within_limit():
    result = run_backtest(
        [_frame(0), _frame(1, bid=0.94, ask=1.01), _frame(2, bid=1.09, ask=1.10)],
        _SpendBudget(cap=210),
        config=BacktestConfig(starting_cash=600, fee_per_contract=0, slippage_spread_fraction=0, maximum_contracts=5),
    )
    assert result.metrics.trades == 1
    assert result.trades[0].quantity == 2


@pytest.mark.parametrize("policy", [
    dict(activation_multiple=1),
    dict(first_lock_fraction=1.1),
    dict(first_lock_fraction=0.9, max_lock_fraction=0.7),
    dict(growth_exposure_fraction=0),
    dict(maximum_contracts=0),
])
def test_invalid_policy_rejected(policy):
    with pytest.raises(ValueError):
        RocketVaultConfig(**policy)
