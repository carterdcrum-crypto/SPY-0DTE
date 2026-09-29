from __future__ import annotations

import hashlib
import math
import statistics
from dataclasses import dataclass
from datetime import timedelta
from typing import Callable, Sequence, Tuple

from .backtest import BacktestConfig, BacktestResult, BacktestStrategy, run_backtest
from .data import HistoricalFrame
from .walkforward import WalkForwardSplit, walk_forward_splits


StrategyFitter = Callable[[Tuple[HistoricalFrame, ...]], BacktestStrategy]
SelectionScore = Callable[[BacktestResult], float]


@dataclass(frozen=True)
class StrategyCandidate:
    """One pre-declared model/parameter candidate.

    Keep this list small and define it before looking at the locked holdout. The
    `complexity` value is used as a tie-breaker so a simpler candidate wins when
    validation performance is effectively indistinguishable.
    """

    name: str
    fit: StrategyFitter
    complexity: int = 0

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("candidate name cannot be blank")
        if self.complexity < 0:
            raise ValueError("candidate complexity cannot be negative")


@dataclass(frozen=True)
class ResearchPartition:
    development: Tuple[HistoricalFrame, ...]
    locked_holdout: Tuple[HistoricalFrame, ...]
    purge: timedelta
    dataset_fingerprint: str


@dataclass(frozen=True)
class CandidateValidation:
    name: str
    complexity: int
    score: float
    result: BacktestResult


@dataclass(frozen=True)
class FoldEvaluation:
    index: int
    split: WalkForwardSplit
    validation: Tuple[CandidateValidation, ...]
    selected_candidate: str
    selected_validation_score: float
    test_result: BacktestResult


@dataclass(frozen=True)
class WalkForwardEvaluation:
    folds: Tuple[FoldEvaluation, ...]
    champion_candidate: str
    selection_counts: Tuple[Tuple[str, int], ...]
    median_test_return: float
    worst_test_return: float
    median_test_drawdown: float
    total_test_trades: int


@dataclass(frozen=True)
class HoldoutEvaluation:
    candidate: str
    dataset_fingerprint: str
    result: BacktestResult


def _ordered_unique(frames: Sequence[HistoricalFrame]) -> Tuple[HistoricalFrame, ...]:
    ordered = tuple(sorted(frames, key=lambda frame: frame.timestamp))
    if any(a.timestamp == b.timestamp for a, b in zip(ordered, ordered[1:])):
        raise ValueError("historical frames must have unique timestamps")
    return ordered


def dataset_fingerprint(frames: Sequence[HistoricalFrame]) -> str:
    """Fingerprint the exact historical sample used for research.

    Including timestamps, SPY quotes, and option bid/ask data makes it obvious
    when a later run silently used a different dataset.
    """

    digest = hashlib.sha256()
    for frame in _ordered_unique(frames):
        digest.update(frame.timestamp.isoformat().encode("utf-8"))
        digest.update(
            (
                f"|{frame.market.spot:.12g}|{frame.market.bid:.12g}|"
                f"{frame.market.ask:.12g}|{len(frame.options)}"
            ).encode("utf-8")
        )
        for quote in sorted(frame.options, key=lambda item: item.symbol):
            digest.update(
                (
                    f"|{quote.symbol}|{quote.bid:.12g}|{quote.ask:.12g}|"
                    f"{quote.implied_volatility:.12g}|{quote.volume}|{quote.open_interest}"
                ).encode("utf-8")
            )
    return digest.hexdigest()


def locked_holdout_partition(
    frames: Sequence[HistoricalFrame],
    *,
    holdout: timedelta,
    purge: timedelta,
) -> ResearchPartition:
    """Reserve the newest period as a final holdout with an embargo before it.

    The returned `development` sample is the only data that should be used for
    model/parameter iteration. The locked holdout should be evaluated only after
    candidate selection is finished.
    """

    if holdout.total_seconds() <= 0:
        raise ValueError("holdout must be positive")
    if purge.total_seconds() < 0:
        raise ValueError("purge cannot be negative")

    ordered = _ordered_unique(frames)
    if not ordered:
        raise ValueError("frames cannot be empty")

    holdout_start = ordered[-1].timestamp - holdout
    development_end = holdout_start - purge
    development = tuple(frame for frame in ordered if frame.timestamp < development_end)
    locked = tuple(frame for frame in ordered if frame.timestamp >= holdout_start)

    if not development:
        raise ValueError("holdout/purge leaves no development data")
    if not locked:
        raise ValueError("holdout contains no frames")

    return ResearchPartition(
        development=development,
        locked_holdout=locked,
        purge=purge,
        dataset_fingerprint=dataset_fingerprint(ordered),
    )


def conservative_selection_score(
    result: BacktestResult,
    *,
    minimum_trades: int = 5,
    drawdown_penalty: float = 1.0,
    cvar_penalty: float = 0.5,
) -> float:
    """Risk-aware validation objective with a hard minimum sample size.

    This is intentionally less gameable than raw return alone. It rewards
    compounded growth while penalizing drawdown and observed left-tail losses.
    """

    if minimum_trades < 1:
        raise ValueError("minimum_trades must be positive")
    metrics = result.metrics
    if metrics.trades < minimum_trades:
        return -math.inf
    wealth_ratio = max(1e-12, metrics.ending_equity / metrics.starting_equity)
    return (
        math.log(wealth_ratio)
        - max(0.0, drawdown_penalty) * metrics.max_drawdown
        - max(0.0, cvar_penalty) * metrics.trade_return_cvar_95
    )


def _choose_candidate(
    evaluations: Sequence[CandidateValidation],
    *,
    tolerance: float,
) -> CandidateValidation:
    if tolerance < 0:
        raise ValueError("selection tolerance cannot be negative")
    finite = [item for item in evaluations if math.isfinite(item.score)]
    if not finite:
        raise ValueError("no candidate met the validation requirements")
    best_score = max(item.score for item in finite)
    near_best = [item for item in finite if item.score >= best_score - tolerance]
    return min(near_best, key=lambda item: (item.complexity, item.name))


def walk_forward_candidate_selection(
    frames: Sequence[HistoricalFrame],
    candidates: Sequence[StrategyCandidate],
    *,
    train: timedelta,
    validation: timedelta,
    test: timedelta,
    purge: timedelta,
    step: timedelta | None = None,
    config: BacktestConfig = BacktestConfig(),
    score: SelectionScore | None = None,
    selection_tolerance: float = 0.0,
    minimum_trades: int = 5,
) -> WalkForwardEvaluation:
    """Nested chronological model selection with untouched outer test windows.

    For each fold, every candidate is fit only on TRAIN, ranked only on
    VALIDATION, and then the selected candidate is re-fit on TRAIN and measured
    on TEST. Test results never participate in that fold's selection.
    """

    ordered = _ordered_unique(frames)
    if not candidates:
        raise ValueError("at least one candidate is required")
    names = [candidate.name for candidate in candidates]
    if len(set(names)) != len(names):
        raise ValueError("candidate names must be unique")

    splits = walk_forward_splits(
        ordered,
        train=train,
        validation=validation,
        test=test,
        purge=purge,
        step=step,
    )
    if not splits:
        raise ValueError("not enough data for any walk-forward split")

    score_fn = score or (lambda result: conservative_selection_score(result, minimum_trades=minimum_trades))
    folds: list[FoldEvaluation] = []
    counts = {candidate.name: 0 for candidate in candidates}
    validation_scores_by_name: dict[str, list[float]] = {candidate.name: [] for candidate in candidates}

    for index, split in enumerate(splits):
        validations: list[CandidateValidation] = []
        for candidate in candidates:
            validation_strategy = candidate.fit(split.train)
            validation_result = run_backtest(split.validation, validation_strategy, config=config)
            candidate_score = float(score_fn(validation_result))
            validations.append(
                CandidateValidation(
                    name=candidate.name,
                    complexity=candidate.complexity,
                    score=candidate_score,
                    result=validation_result,
                )
            )
            if math.isfinite(candidate_score):
                validation_scores_by_name[candidate.name].append(candidate_score)

        chosen = _choose_candidate(validations, tolerance=selection_tolerance)
        counts[chosen.name] += 1
        selected = next(candidate for candidate in candidates if candidate.name == chosen.name)
        test_strategy = selected.fit(split.train)
        test_result = run_backtest(split.test, test_strategy, config=config)
        folds.append(
            FoldEvaluation(
                index=index,
                split=split,
                validation=tuple(validations),
                selected_candidate=chosen.name,
                selected_validation_score=chosen.score,
                test_result=test_result,
            )
        )

    def champion_key(candidate: StrategyCandidate) -> tuple[float, float, int, str]:
        scores = validation_scores_by_name[candidate.name]
        median_score = statistics.median(scores) if scores else -math.inf
        return (-counts[candidate.name], -median_score, candidate.complexity, candidate.name)

    champion = min(candidates, key=champion_key)
    test_returns = [fold.test_result.metrics.total_return for fold in folds]
    test_drawdowns = [fold.test_result.metrics.max_drawdown for fold in folds]

    return WalkForwardEvaluation(
        folds=tuple(folds),
        champion_candidate=champion.name,
        selection_counts=tuple(sorted(counts.items())),
        median_test_return=statistics.median(test_returns),
        worst_test_return=min(test_returns),
        median_test_drawdown=statistics.median(test_drawdowns),
        total_test_trades=sum(fold.test_result.metrics.trades for fold in folds),
    )


def evaluate_locked_holdout(
    partition: ResearchPartition,
    candidate: StrategyCandidate,
    *,
    config: BacktestConfig = BacktestConfig(),
) -> HoldoutEvaluation:
    """Run the final untouched holdout after research decisions are frozen."""

    strategy = candidate.fit(partition.development)
    result = run_backtest(partition.locked_holdout, strategy, config=config)
    return HoldoutEvaluation(
        candidate=candidate.name,
        dataset_fingerprint=partition.dataset_fingerprint,
        result=result,
    )
