from __future__ import annotations

import pytest

from engine.ensemble import EnsembleForecast
from engine.hold_value import evaluate_hold_vs_sell_now
from engine.market import OptionQuote
from engine.scenario import ReturnScenario


def _quote() -> OptionQuote:
    return OptionQuote(
        symbol="SPY260925C00100000",
        right="call",
        strike=100.0,
        bid=1.00,
        ask=1.10,
        delta=0.50,
        gamma=0.02,
        theta=-1.0,
        vega=0.10,
        implied_volatility=0.25,
        volume=1000,
        open_interest=5000,
        underlying_price=100.0,
        minutes_to_expiry=120.0,
    )


def _forecast() -> EnsembleForecast:
    return EnsembleForecast(
        probability_up=0.55,
        expected_log_return=0.0005,
        volatility=0.01,
        agreement_score=0.80,
        calibration_score=0.80,
        regime_match_score=0.80,
        model_weights=(("test", 1.0),),
    )


def test_hold_value_uses_current_bid_as_liquidation_baseline() -> None:
    result = evaluate_hold_vs_sell_now(
        _quote(),
        (ReturnScenario(log_return=0.0, probability=1.0, iv_change=0.0),),
        _forecast(),
        horizon_minutes=2.0,
        fee_per_contract=0.65,
    )

    assert result.sell_now_value == pytest.approx(99.35)
    # A flat scenario should not be made to look attractive by comparing with
    # a hypothetical re-entry at the ask. We already own the contract.
    assert result.expected_advantage_dollars < 0.0
    assert result.lower_confidence_advantage_dollars <= result.expected_advantage_dollars
    assert result.probability_hold_beats_sell_now == 0.0


def test_hold_value_can_identify_positive_incremental_hold_edge() -> None:
    result = evaluate_hold_vs_sell_now(
        _quote(),
        (
            ReturnScenario(log_return=0.010, probability=0.70, iv_change=0.0),
            ReturnScenario(log_return=0.004, probability=0.30, iv_change=0.0),
        ),
        _forecast(),
        horizon_minutes=2.0,
        fee_per_contract=0.65,
    )

    assert result.expected_future_exit_value > result.sell_now_value
    assert result.expected_advantage_dollars > 0.0
    assert result.expected_advantage_fraction > 0.0
    assert result.probability_hold_beats_sell_now > 0.5


def test_hold_value_requires_two_sided_quote() -> None:
    quote = _quote()
    one_sided = OptionQuote(
        **{**quote.__dict__, "bid": 0.0}
    )
    with pytest.raises(ValueError):
        evaluate_hold_vs_sell_now(
            one_sided,
            (ReturnScenario(log_return=0.0, probability=1.0, iv_change=0.0),),
            _forecast(),
            horizon_minutes=2.0,
        )
