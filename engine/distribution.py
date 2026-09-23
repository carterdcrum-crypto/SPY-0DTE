from __future__ import annotations

from dataclasses import dataclass
from math import exp
from typing import Iterable, Mapping, Sequence, Tuple


DEFAULT_HORIZONS_MINUTES: tuple[int, ...] = (5, 15, 30, 60, 180)


@dataclass(frozen=True)
class DistributionPoint:
    log_return: float
    probability: float


@dataclass(frozen=True)
class ReturnDistribution:
    """Discrete forecast distribution for one forward horizon.

    Model implementations may be parametric or ML-based internally, but the
    research engine consumes this common probability distribution interface.
    """

    horizon_minutes: int
    points: Tuple[DistributionPoint, ...]
    model_name: str = "unknown"
    regime: str = "unknown"

    def __post_init__(self) -> None:
        if self.horizon_minutes <= 0:
            raise ValueError("horizon_minutes must be positive")
        if not self.points:
            raise ValueError("distribution requires at least one point")
        if any(point.probability < 0 for point in self.points):
            raise ValueError("probabilities cannot be negative")
        total = sum(point.probability for point in self.points)
        if total <= 0:
            raise ValueError("probabilities must sum to a positive value")
        # Require a normalized contract rather than silently hiding model bugs.
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"probabilities must sum to 1, got {total}")

    @classmethod
    def from_samples(
        cls,
        horizon_minutes: int,
        log_returns: Iterable[float],
        *,
        model_name: str = "empirical",
        regime: str = "unknown",
    ) -> "ReturnDistribution":
        values = tuple(sorted(float(value) for value in log_returns))
        if not values:
            raise ValueError("at least one sample is required")
        probability = 1.0 / len(values)
        return cls(
            horizon_minutes=horizon_minutes,
            points=tuple(DistributionPoint(value, probability) for value in values),
            model_name=model_name,
            regime=regime,
        )

    @property
    def expected_log_return(self) -> float:
        return sum(point.log_return * point.probability for point in self.points)

    @property
    def variance(self) -> float:
        mean = self.expected_log_return
        return sum(point.probability * (point.log_return - mean) ** 2 for point in self.points)

    def probability_above(self, log_return_threshold: float = 0.0) -> float:
        return sum(point.probability for point in self.points if point.log_return > log_return_threshold)

    def probability_below(self, log_return_threshold: float = 0.0) -> float:
        return sum(point.probability for point in self.points if point.log_return < log_return_threshold)

    def probability_finish_above(self, *, spot: float, strike: float) -> float:
        if spot <= 0 or strike <= 0:
            raise ValueError("spot and strike must be positive")
        return sum(
            point.probability
            for point in self.points
            if spot * exp(point.log_return) > strike
        )

    def quantile(self, probability: float) -> float:
        if not 0.0 <= probability <= 1.0:
            raise ValueError("probability must be between 0 and 1")
        ordered = sorted(self.points, key=lambda item: item.log_return)
        cumulative = 0.0
        for point in ordered:
            cumulative += point.probability
            if cumulative + 1e-12 >= probability:
                return point.log_return
        return ordered[-1].log_return

    def central_interval(self, coverage: float = 0.90) -> tuple[float, float]:
        if not 0.0 < coverage < 1.0:
            raise ValueError("coverage must be between 0 and 1")
        alpha = (1.0 - coverage) / 2.0
        return self.quantile(alpha), self.quantile(1.0 - alpha)


@dataclass(frozen=True)
class MultiHorizonDistributionForecast:
    as_of_epoch_ms: int
    distributions: Tuple[ReturnDistribution, ...]

    def __post_init__(self) -> None:
        if not self.distributions:
            raise ValueError("at least one horizon distribution is required")
        horizons = [item.horizon_minutes for item in self.distributions]
        if len(horizons) != len(set(horizons)):
            raise ValueError("duplicate horizon distributions are not allowed")

    def for_horizon(self, minutes: int) -> ReturnDistribution:
        for distribution in self.distributions:
            if distribution.horizon_minutes == minutes:
                return distribution
        raise KeyError(f"missing forecast horizon: {minutes} minutes")

    def as_mapping(self) -> Mapping[int, ReturnDistribution]:
        return {item.horizon_minutes: item for item in self.distributions}
