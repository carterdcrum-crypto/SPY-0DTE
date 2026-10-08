"""Tests for early realized-profit protection, continuous resizing, and trails."""
from datetime import datetime, timedelta, timezone

import pytest

from engine.adaptive_vault import AdaptiveVaultConfig, AdaptiveVaultEventAlphaStrategy
from engine.backtest import BacktestContext, PositionView
from engine.data import HistoricalFrame
from engine.market import MarketSnapshot, OptionQuote


def _quote(*, bid=1.0, ask=1.05):
    return OptionQuote(
        symbol="CALL", right="call", strike=700.0,
        bid=bid, ask=ask, delta=0.45, gamma=0.02, theta=-1.0,
        vega=0.03, implied_volatility=0.20, volume=1500, open_interest=4000,
        underlying_price=700.0, minutes_to_expiry=120.0,
    )


def _frame(minute=0, *, bid=1.0, ask=1.05):
    return HistoricalFrame(
        timestamp=datetime(2026, 10, 1, 14, 0, tzinfo=timezone.utc) + timedelta(minutes=minute),
        market=MarketSnapshot(
            spot=700.0, bid=699.99, ask=700.01,
            realized_volatility=0.20, implied_volatility=0.20,
            volume_ratio=1.0, minutes_to_close=300.0-minute, data_age_seconds=0.1,
        ),
        options=(_quote(bid=bid, ask=ask),),
    )


def _flat(equity, settled=None):
    return BacktestContext(
        settled_cash=equity if settled is None else settled,
        unsettled_cash=0.0,
        equity=equity,
        position=None,
    )


def _held(equity=300):
    return BacktestContext(
        settled_cash=100, unsettled_cash=0, equity=equity,
        position=PositionView(
            option_symbol="CALL", quantity=1, entry_price=1.00,
            entry_cost=100.0, entry_timestamp=_frame(0).timestamp,
        ),
    )


def test_early_ratchet_locks_realized_profits_at_ten_percent_gain():
    strategy=AdaptiveVaultEventAlphaStrategy(starting_cash=300)
    strategy._observe_flat_equity(329)
    assert strategy.locked_profit == 0
    strategy._observe_flat_equity(330)
    assert strategy.locked_profit == pytest.approx(10.5)
    strategy._observe_flat_equity(450)
    old=strategy.locked_profit
    assert old > 10.5
    strategy._observe_flat_equity(350)
    assert strategy.locked_profit == old


def test_exposure_rises_with_recovery_and_shrinks_as_losses_accumulate():
    strategy=AdaptiveVaultEventAlphaStrategy(starting_cash=300)
    strategy._observe_flat_equity(500)
    high=strategy._entry_budget(_flat(500))
    loss=strategy._entry_budget(_flat(400))
    assert 0 < loss < high
    strategy.losing_streak=2
    consecutive_loss_budget=strategy._entry_budget(_flat(400))
    assert 0 < consecutive_loss_budget < loss
    strategy.losing_streak=0
    assert strategy._entry_budget(_flat(500)) == pytest.approx(high)


def test_no_exposure_to_unsettled_cash_or_reserved_profits():
    s=AdaptiveVaultEventAlphaStrategy(starting_cash=300)
    s._observe_flat_equity(450)
    budget=s._entry_budget(_flat(450,settled=50))
    assert budget <= 50
    assert budget >= 0


def test_losing_streak_counts_closed_losses_only():
    s=AdaptiveVaultEventAlphaStrategy(starting_cash=300)
    s.decide(_frame(0),_flat(300))
    s.decide(_frame(1),_held(275))
    s.decide(_frame(2),_held(240))
    assert s.losing_streak == 0
    s.decide(_frame(3),_flat(245))
    assert s.losing_streak == 1
    s.decide(_frame(4),_flat(245))
    assert s.losing_streak == 1
    s.decide(_frame(5),_held(245))
    s.decide(_frame(6),_flat(295))
    assert s.losing_streak == 0


def test_adaptive_trail_arms_only_after_bid_gain_then_closes_on_giveback():
    s=AdaptiveVaultEventAlphaStrategy(starting_cash=300)
    ctx=_held(300)
    assert s.decide(_frame(1,bid=1.15,ask=1.20),ctx).action=="hold"
    assert s.decide(_frame(2,bid=1.40,ask=1.45),ctx).action=="hold"
    decision=s.decide(_frame(3,bid=1.15,ask=1.20),ctx)
    assert decision.action=="close"
    assert decision.reason=="adaptive_vault_profit_trail"


def test_unrealized_windfall_does_not_trigger_profit_lock():
    s=AdaptiveVaultEventAlphaStrategy(starting_cash=300)
    s.decide(_frame(1,bid=4.0,ask=4.1),_held(600))
    assert s.locked_profit == 0


@pytest.mark.parametrize("kwargs", [
    dict(activation_multiple=1.0),
    dict(drawdown_exponent=-1.0),
    dict(loss_streak_penalty=-0.01),
    dict(trailing_arm_return=0.0),
    dict(trailing_profit_retention=1.01),
    dict(maximum_contracts=0),
])
def test_reject_invalid_risk_parameters(kwargs):
    with pytest.raises(ValueError):
        AdaptiveVaultConfig(**kwargs)
