from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from typing import Iterable, Sequence, Tuple

from .distribution import ReturnDistribution


@dataclass(frozen=True)
class ReliabilityBin:
    lower: float
    upper: float
    count: int
    mean_probability: float
    observed_frequency: float


def brier_score(probabilities: Sequence[float], outcomes: Sequence[int | bool]) -> float:
    if len(probabilities) != len(outcomes) or not probabilities:
        raise ValueError("probabilities and outcomes must be non-empty and equal length")
    total = 0.0
    for probability, outcome in zip(probabilities, outcomes):
        p = float(probability)
        if not 0.0 <= p <= 1.0:
            raise ValueError("probability outside [0, 1]")
        y = 1.0 if bool(outcome) else 0.0
        total += (p - y) ** 2
    return total / len(probabilities)


def reliability_bins(
    probabilities: Sequence[float],
    outcomes: Sequence[int | bool],
    *,
    bins: int = 10,
) -> Tuple[ReliabilityBin, ...]:
    if bins < 2:
        raise ValueError("bins must be at least 2")
    if len(probabilities) != len(outcomes) or not probabilities:
        raise ValueError("probabilities and outcomes must be non-empty and equal length")

    buckets: list[list[tuple[float, float]]] = [[] for _ in range(bins)]
    for probability, outcome in zip(probabilities, outcomes):
        p = float(probability)
        if not 0.0 <= p <= 1.0:
            raise ValueError("probability outside [0, 1]")
        index = min(bins - 1, int(p * bins))
        buckets[index].append((p, 1.0 if bool(outcome) else 0.0))

    result = []
    for index, bucket in enumerate(buckets):
        if not bucket:
            continue
        lower = index / bins
        upper = (index + 1) / bins
        result.append(
            ReliabilityBin(
                lower=lower,
                upper=upper,
                count=len(bucket),
                mean_probability=sum(item[0] for item in bucket) / len(bucket),
                observed_frequency=sum(item[1] for item in bucket) / len(bucket),
            )
        )
    return tuple(result)


def expected_calibration_error(
    probabilities: Sequence[float],
    outcomes: Sequence[int | bool],
    *,
    bins: int = 10,
) -> float:
    groups = reliability_bins(probabilities, outcomes, bins=bins)
    total = sum(group.count for group in groups)
    if total == 0:
        raise ValueError("no observations")
    return sum(
        (group.count / total) * abs(group.mean_probability - group.observed_frequency)
        for group in groups
    )


def quantile_coverage(
    distributions: Sequence[ReturnDistribution],
    realized_log_returns: Sequence[float],
    *,
    coverage: float = 0.90,
) -> float:
    if len(distributions) != len(realized_log_returns) or not distributions:
        raise ValueError("distributions and realized returns must be non-empty and equal length")
    inside = 0
    for distribution, realized in zip(distributions, realized_log_returns):
        lower, upper = distribution.central_interval(coverage)
        if lower <= float(realized) <= upper:
            inside += 1
    return inside / len(distributions)


def discrete_crps(distribution: ReturnDistribution, realized_log_return: float) -> float:
    """Continuous Ranked Probability Score for a discrete forecast.

    Lower is better. This proper score evaluates the entire forecast distribution,
    unlike a directional accuracy statistic.
    """

    points = distribution.points
    observed = float(realized_log_return)
    first = sum(point.probability * abs(point.log_return - observed) for point in points)
    second = 0.0
    for left in points:
        for right in points:
            second += left.probability * right.probability * abs(left.log_return - right.log_return)
    return first - 0.5 * second


def root_mean_square_calibration_error(
    probabilities: Sequence[float],
    outcomes: Sequence[int | bool],
    *,
    bins: int = 10,
) -> float:
    groups = reliability_bins(probabilities, outcomes, bins=bins)
    total = sum(group.count for group in groups)
    if total == 0:
        raise ValueError("no observations")
    mse = sum(
        (group.count / total) * (group.mean_probability - group.observed_frequency) ** 2
        for group in groups
    )
    return sqrt(mse)
