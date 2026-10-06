from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable, Sequence, Tuple

from .regime_long_option import RegimeLongOptionConfig, run_regime_stress_directory
from .research_objective import (
    ExecutionStressPolicy,
    GrowthDrawdownPolicy,
    StressGrowthDrawdownEvaluation,
    stressed_growth_drawdown_evaluation,
)


@dataclass(frozen=True)
class RegimeCandidate:
    name: str
    config: RegimeLongOptionConfig


@dataclass(frozen=True)
class FrontierPoint:
    candidate: str
    drawdown_budget: float
    starting_cash: float
    accepted: bool
    score: float
    worst_tick: int | None
    zero_tick_return: float
    zero_tick_drawdown: float
    one_tick_return: float
    one_tick_drawdown: float
    two_tick_return: float
    two_tick_drawdown: float
    reason: str


def default_regime_candidates() -> Tuple[RegimeCandidate, ...]:
    """Small, economically motivated candidate set.

    This is intentionally not a Cartesian parameter sweep. Each profile encodes
    a distinct hypothesis so every tested configuration can be counted and
    audited for multiple-testing control.
    """

    base = RegimeLongOptionConfig()
    return (
        RegimeCandidate("balanced", base),
        RegimeCandidate(
            "selective",
            replace(
                base,
                volatility_edge_ratio=1.15,
                max_spread_fraction=0.10,
                trend_threshold=0.0020,
                stop_loss_return=-0.20,
            ),
        ),
        RegimeCandidate(
            "fast_exit",
            replace(
                base,
                volatility_edge_ratio=1.10,
                target_abs_delta=0.50,
                max_hold_minutes=10,
                take_profit_return=0.35,
                stop_loss_return=-0.20,
                max_spread_fraction=0.10,
            ),
        ),
        RegimeCandidate(
            "convex",
            replace(
                base,
                volatility_edge_ratio=1.15,
                target_abs_delta=0.45,
                take_profit_return=0.80,
                stop_loss_return=-0.30,
                max_spread_fraction=0.12,
            ),
        ),
        RegimeCandidate(
            "affordable",
            replace(
                base,
                volatility_edge_ratio=1.15,
                target_abs_delta=0.35,
                take_profit_return=0.75,
                stop_loss_return=-0.30,
                max_spread_fraction=0.15,
            ),
        ),
    )


def _evaluate_candidate(
    path: str | Path,
    *,
    candidate: RegimeCandidate,
    drawdown_budget: float,
    starting_cash: float,
) -> FrontierPoint:
    config = replace(candidate.config, hard_drawdown=drawdown_budget)
    stresses = run_regime_stress_directory(
        path,
        starting_cash=starting_cash,
        config=config,
        adverse_ticks=(0, 1, 2),
    )
    by_tick = {item.adverse_ticks_per_side: item.result for item in stresses}
    evaluation: StressGrowthDrawdownEvaluation = stressed_growth_drawdown_evaluation(
        by_tick,
        policy=GrowthDrawdownPolicy(
            minimum_trades=10,
            minimum_total_return=0.0,
            minimum_return_drawdown_ratio=1.0,
            maximum_drawdown=drawdown_budget,
            trade_cvar_penalty=0.25,
        ),
        stress_policy=ExecutionStressPolicy(
            ratio_required_ticks=(0, 1),
            positive_return_required_ticks=(0, 1, 2),
            scoring_ticks=(0, 1),
        ),
    )

    zero = by_tick[0].metrics
    one = by_tick[1].metrics
    two = by_tick[2].metrics
    return FrontierPoint(
        candidate=candidate.name,
        drawdown_budget=drawdown_budget,
        starting_cash=starting_cash,
        accepted=evaluation.accepted,
        score=evaluation.score,
        worst_tick=evaluation.worst_tick,
        zero_tick_return=zero.total_return,
        zero_tick_drawdown=zero.max_drawdown,
        one_tick_return=one.total_return,
        one_tick_drawdown=one.max_drawdown,
        two_tick_return=two.total_return,
        two_tick_drawdown=two.max_drawdown,
        reason=evaluation.reason,
    )


def run_regime_frontier(
    path: str | Path,
    *,
    starting_cash: float,
    candidates: Sequence[RegimeCandidate] | None = None,
    drawdown_budgets: Iterable[float] = (0.05, 0.10, 0.15, 0.20),
) -> Tuple[FrontierPoint, ...]:
    selected = tuple(candidates or default_regime_candidates())
    budgets = tuple(float(value) for value in drawdown_budgets)
    if not selected:
        raise ValueError("at least one candidate is required")
    if any(value <= 0 or value >= 1 for value in budgets):
        raise ValueError("drawdown budgets must be in (0, 1)")

    points: list[FrontierPoint] = []
    for budget in budgets:
        for candidate in selected:
            points.append(
                _evaluate_candidate(
                    path,
                    candidate=candidate,
                    drawdown_budget=budget,
                    starting_cash=starting_cash,
                )
            )
    return tuple(points)


def pareto_frontier(points: Sequence[FrontierPoint]) -> Tuple[FrontierPoint, ...]:
    """Return accepted points not dominated on return and max drawdown.

    Return uses the 1-tick executable-stress scenario, not perfect fills.
    """

    accepted = [point for point in points if point.accepted]
    frontier: list[FrontierPoint] = []
    for point in accepted:
        dominated = any(
            other is not point
            and other.one_tick_return >= point.one_tick_return
            and other.one_tick_drawdown <= point.one_tick_drawdown
            and (
                other.one_tick_return > point.one_tick_return
                or other.one_tick_drawdown < point.one_tick_drawdown
            )
            for other in accepted
        )
        if not dominated:
            frontier.append(point)
    return tuple(
        sorted(
            frontier,
            key=lambda point: (
                point.one_tick_drawdown,
                -point.one_tick_return,
                point.candidate,
            ),
        )
    )
