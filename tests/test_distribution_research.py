from engine.ablation import block_bootstrap_ablation, paid_feature_earned_its_keep
from engine.calibration import brier_score, discrete_crps, expected_calibration_error, quantile_coverage
from engine.distribution import MultiHorizonDistributionForecast, ReturnDistribution


def test_return_distribution_contract_and_strike_probability():
    distribution = ReturnDistribution.from_samples(
        15,
        [-0.02, -0.01, 0.0, 0.01, 0.02],
        model_name="test",
    )
    assert 0.0 < distribution.probability_above() < 1.0
    assert distribution.quantile(0.5) == 0.0
    lower, upper = distribution.central_interval(0.80)
    assert lower <= 0.0 <= upper
    assert 0.0 <= distribution.probability_finish_above(spot=500.0, strike=505.0) <= 1.0


def test_multi_horizon_forecast_requires_unique_horizons():
    first = ReturnDistribution.from_samples(5, [-0.01, 0.0, 0.01])
    second = ReturnDistribution.from_samples(15, [-0.02, 0.0, 0.02])
    forecast = MultiHorizonDistributionForecast(123, (first, second))
    assert forecast.for_horizon(5) is first
    assert forecast.for_horizon(15) is second


def test_calibration_metrics_reward_better_probabilities():
    outcomes = [1, 1, 0, 0]
    good = [0.9, 0.8, 0.2, 0.1]
    bad = [0.55, 0.55, 0.45, 0.45]
    assert brier_score(good, outcomes) < brier_score(bad, outcomes)
    assert expected_calibration_error(good, outcomes, bins=2) <= 0.2


def test_distribution_scores_and_coverage():
    forecasts = [
        ReturnDistribution.from_samples(15, [-0.02, -0.01, 0.0, 0.01, 0.02]),
        ReturnDistribution.from_samples(15, [-0.03, -0.01, 0.0, 0.01, 0.03]),
    ]
    realized = [0.005, -0.005]
    assert 0.0 <= quantile_coverage(forecasts, realized, coverage=0.8) <= 1.0
    assert discrete_crps(forecasts[0], realized[0]) >= 0.0


def test_block_ablation_identifies_useful_feature_group():
    full_scores = [0.10, 0.11, 0.09, 0.10, 0.12, 0.10, 0.09, 0.11, 0.10, 0.10]
    ablated_scores = [0.20, 0.19, 0.18, 0.21, 0.22, 0.20, 0.19, 0.20, 0.18, 0.21]
    result = block_bootstrap_ablation(
        "surface",
        full_scores,
        ablated_scores,
        block_size=2,
        repetitions=300,
        seed=1,
    )
    assert result.delta > 0
    assert result.lower_ci > 0
    assert paid_feature_earned_its_keep(result)
