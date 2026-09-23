from engine.baseline_distribution import GaussianHorizonForecast, gaussian_distribution


def test_gaussian_baseline_produces_normalized_distribution():
    distribution = gaussian_distribution(
        GaussianHorizonForecast(
            horizon_minutes=30,
            mean_log_return=0.001,
            volatility=0.01,
        ),
        points=21,
    )
    assert distribution.horizon_minutes == 30
    assert abs(sum(point.probability for point in distribution.points) - 1.0) < 1e-9
    assert distribution.quantile(0.5) > -0.01
