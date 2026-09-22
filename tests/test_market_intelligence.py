from __future__ import annotations

import pytest

from engine.ensemble import EnsembleForecast, ModelForecast, combine_forecasts
from engine.market import MarketSnapshot, OptionQuote
from engine.opportunity import evaluate_option_candidate, rank_opportunities
from engine.options import reprice_from_greeks
from engine.scenario import ReturnScenario, generate_return_scenarios


def _forecast() -> EnsembleForecast:
    return EnsembleForecast(
        probability_up=0.72,
        expected_log_return=0.0035,
        volatility=0.006,
        agreement_score=0.94,
        calibration_score=0.92,
        regime_match_score=0.90,
        model_weights=(("test", 1.0),),
    )


def _market() -> MarketSnapshot:
    return MarketSnapshot(
        spot=500.0,
        bid=499.99,
        ask=500.01,
        realized_volatility=0.16,
        implied_volatility=0.18,
        volume_ratio=1.2,
        minutes_to_close=240.0,
        data_age_seconds=0.25,
        ood_score=0.08,
    )


def _quote(*, bid: float, ask: float, symbol: str = "SPY_TEST_C") -> OptionQuote:
    return OptionQuote(
        symbol=symbol,
        right="call",
        strike=500.0,
        bid=bid,
        ask=ask,
        delta=0.52,
        gamma=0.07,
        theta=-0.55,
        vega=0.05,
        implied_volatility=0.18,
        volume=1200,
        open_interest=5000,
        underlying_price=500.0,
        minutes_to_expiry=240.0,
    )


def test_bad_recent_model_gets_less_weight() -> None:
    good = ModelForecast("good", 0.72, 0.003, 0.006, 0.05, 0.95, 0.95)
    bad = ModelForecast("bad", 0.20, -0.002, 0.009, 1.00, 0.95, 0.95)
    combined = combine_forecasts((good, bad), loss_sensitivity=4.0)
    weights = dict(combined.model_weights)
    assert weights["good"] > weights["bad"]
    assert combined.probability_up > 0.5


def test_poor_calibration_shrinks_extreme_probability() -> None:
    raw_probability = 0.90
    model = ModelForecast("loud", raw_probability, 0.004, 0.007, 0.1, 0.25, 1.0)
    combined = combine_forecasts((model,))
    assert 0.5 < combined.probability_up < raw_probability
    assert combined.probability_up == pytest.approx(0.60)


def test_scenarios_are_normalized_and_ordered() -> None:
    scenarios = generate_return_scenarios(_forecast(), points=21)
    assert sum(item.probability for item in scenarios) == pytest.approx(1.0)
    assert scenarios[0].log_return < scenarios[-1].log_return
    assert scenarios[0].iv_change > scenarios[-1].iv_change


def test_call_reprices_higher_when_spy_rises() -> None:
    quote = _quote(bid=0.95, ask=1.00)
    down = ReturnScenario(log_return=-0.006, probability=0.5, iv_change=0.004)
    up = ReturnScenario(log_return=0.006, probability=0.5, iv_change=-0.004)
    assert reprice_from_greeks(quote, up, horizon_minutes=15) > reprice_from_greeks(
        quote, down, horizon_minutes=15
    )


def test_wide_spread_is_penalized() -> None:
    forecast = _forecast()
    scenarios = generate_return_scenarios(forecast, points=21)
    tight = evaluate_option_candidate(
        _quote(bid=0.95, ask=1.00, symbol="TIGHT"),
        scenarios,
        forecast,
        _market(),
        horizon_minutes=15,
    )
    wide = evaluate_option_candidate(
        _quote(bid=0.70, ask=1.20, symbol="WIDE"),
        scenarios,
        forecast,
        _market(),
        horizon_minutes=15,
    )
    assert tight.expected_return > wide.expected_return
    assert tight.candidate.liquidity_score > wide.candidate.liquidity_score
    assert rank_opportunities((wide, tight))[0].candidate.option_symbol == "TIGHT"


def test_uncertainty_penalty_increases_when_reliability_falls() -> None:
    scenarios = generate_return_scenarios(_forecast(), points=21)
    reliable = evaluate_option_candidate(
        _quote(bid=0.95, ask=1.00),
        scenarios,
        _forecast(),
        _market(),
        horizon_minutes=15,
    )
    weak_forecast = EnsembleForecast(
        probability_up=0.72,
        expected_log_return=0.0035,
        volatility=0.006,
        agreement_score=0.45,
        calibration_score=0.50,
        regime_match_score=0.55,
        model_weights=(("test", 1.0),),
    )
    weak_scenarios = generate_return_scenarios(weak_forecast, points=21)
    weak = evaluate_option_candidate(
        _quote(bid=0.95, ask=1.00),
        weak_scenarios,
        weak_forecast,
        _market(),
        horizon_minutes=15,
    )
    assert weak.uncertainty_penalty > reliable.uncertainty_penalty
