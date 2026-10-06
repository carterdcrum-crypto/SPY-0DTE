from __future__ import annotations

import math
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from engine.backtest import BacktestResult
from engine.metrics import EquityPoint, PerformanceMetrics
from engine.research_objective import (
    ExecutionStressPolicy,
    GrowthDrawdownPolicy,
    growth_drawdown_evaluation,
    return_drawdown_ratio,
    stressed_growth_drawdown_evaluation,
)


def _result(*, total_return: float, max_drawdown: float, trades: int = 40, cvar: float = 0.10):
    starting = 1000.0
    ending = starting * (1.0 + total_return)
    metrics = PerformanceMetrics(
        starting_equity=starting,
        ending_equity=ending,
        total_return=total_return,
        geometric_growth_per_trade=0.0,
        max_drawdown=max_drawdown,
        win_rate=0.5,
        profit_factor=1.5,
        average_trade_return=0.02,
        trade_return_cvar_95=cvar,
        trades=trades,
    )
    curve = (
        EquityPoint(datetime(2026, 1, 1, tzinfo=timezone.utc), starting),
        EquityPoint(datetime(2026, 1, 2, tzinfo=timezone.utc), ending),
    )
    return BacktestResult((), curve, metrics)


def test_return_drawdown_ratio_is_primary_requirement():
    strong = _result(total_return=0.80, max_drawdown=0.20)
    weak = _result(total_return=0.20, max_drawdown=0.30)

    assert return_drawdown_ratio(strong) == pytest.approx(4.0)
    assert return_drawdown_ratio(weak) == pytest.approx(2 / 3)

    assert growth_drawdown_evaluation(strong).accepted
    rejected = growth_drawdown_evaluation(weak)
    assert not rejected.accepted
    assert rejected.reason == "return_does_not_exceed_drawdown"


def test_score_rewards_growth_not_tiny_return_with_huge_ratio():
    policy = GrowthDrawdownPolicy(maximum_drawdown=0.60, trade_cvar_penalty=0.0)
    big = _result(total_return=0.80, max_drawdown=0.20)
    tiny = _result(total_return=0.01, max_drawdown=0.001)

    assert growth_drawdown_evaluation(big, policy=policy).score > growth_drawdown_evaluation(
        tiny, policy=policy
    ).score


def test_execution_stress_requires_positive_two_tick_and_ratio_at_one_tick():
    zero = _result(total_return=0.60, max_drawdown=0.20)
    one = _result(total_return=0.30, max_drawdown=0.20)
    two = _result(total_return=0.05, max_drawdown=0.20)

    policy = GrowthDrawdownPolicy(maximum_drawdown=0.60)
    evaluation = stressed_growth_drawdown_evaluation(
        {0: zero, 1: one, 2: two},
        policy=policy,
    )
    assert not evaluation.accepted
    assert evaluation.reason == "return_does_not_exceed_drawdown"

    relaxed = stressed_growth_drawdown_evaluation(
        {0: zero, 1: one, 2: two},
        policy=policy,
        stress_policy=ExecutionStressPolicy(
            ratio_required_ticks=(0, 1),
            positive_return_required_ticks=(0, 1, 2),
            scoring_ticks=(0, 1),
        ),
    )
    assert relaxed.accepted


def test_negative_two_tick_return_fails_even_if_zero_and_one_tick_are_strong():
    zero = _result(total_return=0.80, max_drawdown=0.20)
    one = _result(total_return=0.50, max_drawdown=0.20)
    two = _result(total_return=-0.01, max_drawdown=0.10)

    evaluation = stressed_growth_drawdown_evaluation(
        {0: zero, 1: one, 2: two},
        policy=GrowthDrawdownPolicy(maximum_drawdown=0.60),
    )
    assert not evaluation.accepted
    assert evaluation.reason == "nonpositive_return_at_2_ticks"
