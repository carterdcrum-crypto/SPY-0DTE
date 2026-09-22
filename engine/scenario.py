from __future__ import annotations

from dataclasses import dataclass
from statistics import NormalDist
from typing import Tuple

from .ensemble import EnsembleForecast


@dataclass(frozen=True)
class ReturnScenario:
    log_return: float
    probability: float
    iv_change: float


def generate_return_scenarios(
    forecast: EnsembleForecast,
    *,
    points: int = 21,
    tail_scale: float = 1.10,
    iv_leverage: float = -0.60,
) -> Tuple[ReturnScenario, ...]:
    """Create a deterministic probability grid from the ensemble forecast.

    The directional probability contributes an implied drift, blended with the
    ensemble's direct expected-return estimate. A small tail-scale inflation is
    deliberately conservative for 0DTE. `iv_leverage` captures the usual index
    leverage effect: negative SPY returns tend to coincide with higher IV.
    """

    if points < 5:
        raise ValueError("points must be at least 5")
    if forecast.volatility <= 0:
        raise ValueError("forecast volatility must be positive")

    normal = NormalDist()
    safe_p = max(1e-5, min(1.0 - 1e-5, forecast.probability_up))
    directional_mean = forecast.volatility * normal.inv_cdf(safe_p)
    mean = 0.5 * forecast.expected_log_return + 0.5 * directional_mean
    sigma = forecast.volatility * max(1.0, tail_scale)

    probability = 1.0 / points
    scenarios = []
    for index in range(points):
        quantile = (index + 0.5) / points
        z = normal.inv_cdf(quantile)
        log_return = mean + sigma * z
        iv_change = iv_leverage * log_return
        scenarios.append(ReturnScenario(log_return, probability, iv_change))

    return tuple(scenarios)
