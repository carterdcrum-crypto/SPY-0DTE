from __future__ import annotations

from dataclasses import dataclass
from math import exp
from statistics import NormalDist
from typing import Sequence, Tuple

from .distribution import DistributionPoint, ReturnDistribution


@dataclass(frozen=True)
class GaussianHorizonForecast:
    horizon_minutes: int
    mean_log_return: float
    volatility: float


def gaussian_distribution(
    forecast: GaussianHorizonForecast,
    *,
    points: int = 41,
    model_name: str = "gaussian_baseline",
    regime: str = "unknown",
) -> ReturnDistribution:
    """Zero-cost probabilistic baseline used before ML models are promoted.

    The point grid intentionally extends into the tails. More sophisticated
    models can replace this baseline while keeping the same distribution API.
    """

    if forecast.horizon_minutes <= 0:
        raise ValueError("horizon_minutes must be positive")
    if forecast.volatility <= 0:
        raise ValueError("volatility must be positive")
    if points < 9:
        raise ValueError("points must be at least 9")

    normal = NormalDist()
    probability = 1.0 / points
    values = []
    for index in range(points):
        quantile = (index + 0.5) / points
        z = normal.inv_cdf(quantile)
        values.append(
            DistributionPoint(
                log_return=forecast.mean_log_return + forecast.volatility * z,
                probability=probability,
            )
        )
    return ReturnDistribution(
        horizon_minutes=forecast.horizon_minutes,
        points=tuple(values),
        model_name=model_name,
        regime=regime,
    )
