import math

from engine.risk import (
    constrained_fractional_kelly,
    drawdown_multiplier,
    expected_log_growth,
    optimal_kelly_fraction,
    weighted_cvar,
)


def test_expected_log_growth_rejects_ruin():
    assert expected_log_growth(1.0, [-1.0, 1.0], [0.5, 0.5]) == -math.inf


def test_positive_edge_gets_positive_kelly_fraction():
    fraction = optimal_kelly_fraction([-1.0, 1.5], [0.4, 0.6])
    assert 0.0 < fraction < 1.0


def test_negative_edge_gets_zero_kelly_fraction():
    fraction = optimal_kelly_fraction([-1.0, 0.2], [0.8, 0.2])
    assert fraction == 0.0


def test_weighted_cvar_focuses_on_worst_tail():
    result = weighted_cvar([100.0, 20.0, 0.0], [0.01, 0.09, 0.90], 0.99)
    assert math.isclose(result, 100.0, rel_tol=1e-9)


def test_drawdown_multiplier_reaches_zero_at_halt():
    assert drawdown_multiplier(0.0, 0.10) == 1.0
    assert math.isclose(drawdown_multiplier(0.05, 0.10), 0.5)
    assert drawdown_multiplier(0.10, 0.10) == 0.0


def test_cvar_can_shrink_position_size():
    fraction, raw_kelly, cvar = constrained_fractional_kelly(
        account_equity=10_000.0,
        returns=(-1.0, 0.8),
        probabilities=(0.20, 0.80),
        model_confidence=1.0,
        calibration_score=1.0,
        agreement_score=1.0,
        regime_match_score=1.0,
        drawdown_fraction=0.0,
        drawdown_halt_fraction=0.10,
        safety_fraction=0.50,
        maximum_position_fraction=0.20,
        maximum_cvar_fraction_of_equity=0.01,
        cvar_confidence_level=0.99,
    )
    assert raw_kelly > 0.0
    assert 0.0 < fraction <= 0.01 + 1e-9
    assert cvar <= 100.0 + 1e-6
