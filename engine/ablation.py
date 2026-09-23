from __future__ import annotations

from dataclasses import dataclass
from random import Random
from statistics import mean
from typing import Mapping, Sequence, Tuple


@dataclass(frozen=True)
class AblationResult:
    feature_group: str
    full_score: float
    ablated_score: float
    delta: float
    lower_ci: float
    upper_ci: float
    samples: int


def paired_score_delta(full_scores: Sequence[float], ablated_scores: Sequence[float]) -> float:
    if len(full_scores) != len(ablated_scores) or not full_scores:
        raise ValueError("score sequences must be non-empty and equal length")
    return mean(float(ablated) - float(full) for full, ablated in zip(full_scores, ablated_scores))


def block_bootstrap_ablation(
    feature_group: str,
    full_scores: Sequence[float],
    ablated_scores: Sequence[float],
    *,
    block_size: int = 5,
    repetitions: int = 2000,
    seed: int = 7,
) -> AblationResult:
    """Paired block bootstrap for lower-is-better forecast scores.

    Positive delta means removing the feature group made the score worse, so the
    feature group added value. Resampling contiguous blocks preserves some of the
    serial dependence that makes intraday observations unsafe to treat as IID.
    """

    if len(full_scores) != len(ablated_scores) or not full_scores:
        raise ValueError("score sequences must be non-empty and equal length")
    if block_size <= 0 or repetitions <= 0:
        raise ValueError("block_size and repetitions must be positive")

    full = tuple(float(value) for value in full_scores)
    ablated = tuple(float(value) for value in ablated_scores)
    n = len(full)
    observed_delta = paired_score_delta(full, ablated)
    rng = Random(seed)
    deltas: list[float] = []

    for _ in range(repetitions):
        sampled_full: list[float] = []
        sampled_ablated: list[float] = []
        while len(sampled_full) < n:
            start = rng.randrange(0, n)
            for offset in range(block_size):
                index = (start + offset) % n
                sampled_full.append(full[index])
                sampled_ablated.append(ablated[index])
                if len(sampled_full) >= n:
                    break
        deltas.append(paired_score_delta(sampled_full, sampled_ablated))

    ordered = sorted(deltas)
    lower_index = max(0, int(0.025 * repetitions))
    upper_index = min(repetitions - 1, int(0.975 * repetitions))
    return AblationResult(
        feature_group=feature_group,
        full_score=mean(full),
        ablated_score=mean(ablated),
        delta=observed_delta,
        lower_ci=ordered[lower_index],
        upper_ci=ordered[upper_index],
        samples=n,
    )


def rank_ablations(results: Sequence[AblationResult]) -> Tuple[AblationResult, ...]:
    return tuple(sorted(results, key=lambda item: item.delta, reverse=True))


def paid_feature_earned_its_keep(
    result: AblationResult,
    *,
    require_positive_lower_ci: bool = True,
) -> bool:
    """Gate paid inputs on measured out-of-sample forecast improvement.

    This is deliberately only a research gate; economic ROI still has to be
    checked separately against subscription cost and trading-capital scale.
    """

    if require_positive_lower_ci:
        return result.lower_ci > 0.0
    return result.delta > 0.0
