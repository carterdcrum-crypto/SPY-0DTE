from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from .backtest import BacktestResult


@dataclass(frozen=True)
class GrowthDrawdownPolicy:
    """Hard gates and ranking objective for aggressive but survivable growth.

    The primary requirement is exactly what the project is optimizing for:
    account return must exceed maximum drawdown. Candidates that fail that
    requirement are rejected instead of being rescued by a high win rate or
    attractive raw return.
    """

    minimum_trades: int = 20
    minimum_total_return: float = 0.0
    minimum_return_drawdown_ratio: float = 1.0
    maximum_drawdown: float = 0.50
    trade_cvar_penalty: float = 0.25
    ratio_cap: float = 8.0

    def __post_init__(self) -> None:
        if self.minimum_trades < 1:
            raise ValueError("minimum_trades must be positive")
        if self.minimum_total_return < 0:
            raise ValueError("minimum_total_return cannot be negative")
        if self.minimum_return_drawdown_ratio <= 0:
            raise ValueError("minimum_return_drawdown_ratio must be positive")
        if not 0 < self.maximum_drawdown < 1:
            raise ValueError("maximum_drawdown must be in (0, 1)")
        if self.trade_cvar_penalty < 0:
            raise ValueError("trade_cvar_penalty cannot be negative")
        if self.ratio_cap < self.minimum_return_drawdown_ratio:
            raise ValueError("ratio_cap cannot be below the minimum ratio")


@dataclass(frozen=True)
class ExecutionStressPolicy:
    """Promotion gates across executable-fill assumptions.

    0- and 1-tick scenarios must both beat drawdown. The 2-tick scenario must
    at least remain profitable by default. This prevents a candidate from
    winning research because it only works under perfect fills.
    """

    ratio_required_ticks: tuple[int, ...] = (0, 1)
    positive_return_required_ticks: tuple[int, ...] = (0, 1, 2)
    scoring_ticks: tuple[int, ...] = (0, 1, 2)

    def __post_init__(self) -> None:
        for name, values in (
            ("ratio_required_ticks", self.ratio_required_ticks),
            ("positive_return_required_ticks", self.positive_return_required_ticks),
            ("scoring_ticks", self.scoring_ticks),
        ):
            if not values:
                raise ValueError(f"{name} cannot be empty")
            if any(tick < 0 for tick in values):
                raise ValueError(f"{name} cannot contain negative ticks")


@dataclass(frozen=True)
class GrowthDrawdownEvaluation:
    accepted: bool
    score: float
    total_return: float
    max_drawdown: float
    return_drawdown_ratio: float
    trades: int
    reason: str = ""


@dataclass(frozen=True)
class StressGrowthDrawdownEvaluation:
    accepted: bool
    score: float
    worst_tick: int | None
    evaluations: tuple[tuple[int, GrowthDrawdownEvaluation], ...]
    reason: str = ""


def return_drawdown_ratio(result: BacktestResult) -> float:
    metrics = result.metrics
    if metrics.max_drawdown <= 0:
        if metrics.total_return > 0:
            return math.inf
        return 0.0
    return metrics.total_return / metrics.max_drawdown


def growth_drawdown_evaluation(
    result: BacktestResult,
    *,
    policy: GrowthDrawdownPolicy = GrowthDrawdownPolicy(),
) -> GrowthDrawdownEvaluation:
    metrics = result.metrics
    ratio = return_drawdown_ratio(result)

    if metrics.trades < policy.minimum_trades:
        return GrowthDrawdownEvaluation(
            False,
            -math.inf,
            metrics.total_return,
            metrics.max_drawdown,
            ratio,
            metrics.trades,
            "insufficient_trades",
        )
    if metrics.total_return <= policy.minimum_total_return:
        return GrowthDrawdownEvaluation(
            False,
            -math.inf,
            metrics.total_return,
            metrics.max_drawdown,
            ratio,
            metrics.trades,
            "nonpositive_or_too_small_return",
        )
    if metrics.max_drawdown > policy.maximum_drawdown:
        return GrowthDrawdownEvaluation(
            False,
            -math.inf,
            metrics.total_return,
            metrics.max_drawdown,
            ratio,
            metrics.trades,
            "drawdown_ceiling_exceeded",
        )
    if ratio < policy.minimum_return_drawdown_ratio:
        return GrowthDrawdownEvaluation(
            False,
            -math.inf,
            metrics.total_return,
            metrics.max_drawdown,
            ratio,
            metrics.trades,
            "return_does_not_exceed_drawdown",
        )

    wealth_ratio = metrics.ending_equity / metrics.starting_equity
    capped_ratio = min(policy.ratio_cap, ratio)
    score = (
        math.log(max(1e-12, wealth_ratio)) * capped_ratio
        - policy.trade_cvar_penalty * metrics.trade_return_cvar_95
    )
    return GrowthDrawdownEvaluation(
        True,
        score,
        metrics.total_return,
        metrics.max_drawdown,
        ratio,
        metrics.trades,
    )


def stressed_growth_drawdown_evaluation(
    results: Mapping[int, BacktestResult] | Sequence[tuple[int, BacktestResult]],
    *,
    policy: GrowthDrawdownPolicy = GrowthDrawdownPolicy(),
    stress_policy: ExecutionStressPolicy = ExecutionStressPolicy(),
) -> StressGrowthDrawdownEvaluation:
    by_tick = dict(results)
    required = set(stress_policy.scoring_ticks)
    required.update(stress_policy.ratio_required_ticks)
    required.update(stress_policy.positive_return_required_ticks)
    missing = sorted(required.difference(by_tick))
    if missing:
        return StressGrowthDrawdownEvaluation(
            False,
            -math.inf,
            None,
            (),
            f"missing_execution_stress_ticks:{','.join(map(str, missing))}",
        )

    evaluations: list[tuple[int, GrowthDrawdownEvaluation]] = []
    for tick in stress_policy.scoring_ticks:
        evaluation = growth_drawdown_evaluation(by_tick[tick], policy=policy)
        evaluations.append((tick, evaluation))

    for tick in stress_policy.positive_return_required_ticks:
        if by_tick[tick].metrics.total_return <= 0:
            return StressGrowthDrawdownEvaluation(
                False,
                -math.inf,
                tick,
                tuple(evaluations),
                f"nonpositive_return_at_{tick}_ticks",
            )

    for tick in stress_policy.ratio_required_ticks:
        ratio = return_drawdown_ratio(by_tick[tick])
        if ratio < policy.minimum_return_drawdown_ratio:
            return StressGrowthDrawdownEvaluation(
                False,
                -math.inf,
                tick,
                tuple(evaluations),
                f"return_drawdown_ratio_failed_at_{tick}_ticks",
            )

    finite = [(tick, evaluation) for tick, evaluation in evaluations if math.isfinite(evaluation.score)]
    if len(finite) != len(evaluations):
        failed_tick, failed = next(
            (tick, evaluation)
            for tick, evaluation in evaluations
            if not math.isfinite(evaluation.score)
        )
        return StressGrowthDrawdownEvaluation(
            False,
            -math.inf,
            failed_tick,
            tuple(evaluations),
            failed.reason or f"growth_drawdown_gate_failed_at_{failed_tick}_ticks",
        )

    # Worst-case selection is intentional: a candidate cannot hide fragile fill
    # sensitivity behind an excellent perfect-fill result.
    worst_tick, worst = min(finite, key=lambda item: item[1].score)
    return StressGrowthDrawdownEvaluation(
        True,
        worst.score,
        worst_tick,
        tuple(evaluations),
    )
