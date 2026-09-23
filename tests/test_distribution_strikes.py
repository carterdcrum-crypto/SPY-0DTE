from engine.distribution import ReturnDistribution


def test_probability_finish_above_matches_scenario_mass():
    distribution = ReturnDistribution.from_samples(15, [-0.02, -0.01, 0.0, 0.01, 0.02])
    probability = distribution.probability_finish_above(spot=100.0, strike=100.0)
    assert 0.39 <= probability <= 0.41
