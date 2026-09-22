from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class EquityPoint:
    timestamp: object
    equity: float


@dataclass(frozen=True)
class PerformanceMetrics:
    starting_equity: float
    ending_equity: float
    total_return: float
    geometric_growth_per_trade: float
    max_drawdown: float
    win_rate: float
    profit_factor: float
    average_trade_return: float
    trade_return_cvar_95: float
    trades: int


def maximum_drawdown(equities: Sequence[float]) -> float:
    if not equities:
        return 0.0
    peak = equities[0]
    worst = 0.0
    for equity in equities:
        peak = max(peak, equity)
        if peak > 0:
            worst = max(worst, 1.0 - equity / peak)
    return worst


def empirical_cvar_loss(returns: Sequence[float], confidence: float = 0.95) -> float:
    """Average positive loss in the worst tail of observed trade returns."""

    if not returns:
        return 0.0
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be between 0 and 1")
    losses = sorted((max(0.0, -float(r)) for r in returns), reverse=True)
    tail_n = max(1, math.ceil((1.0 - confidence) * len(losses)))
    return sum(losses[:tail_n]) / tail_n


def performance_metrics(
    *,
    starting_equity: float,
    equity_curve: Sequence[EquityPoint],
    trade_returns: Sequence[float],
) -> PerformanceMetrics:
    if starting_equity <= 0:
        raise ValueError("starting_equity must be positive")
    ending = equity_curve[-1].equity if equity_curve else starting_equity
    total_return = ending / starting_equity - 1.0
    count = len(trade_returns)

    if count and ending > 0:
        geometric = (ending / starting_equity) ** (1.0 / count) - 1.0
    else:
        geometric = 0.0

    wins = [r for r in trade_returns if r > 0]
    losses = [r for r in trade_returns if r < 0]
    gross_profit = sum(wins)
    gross_loss = -sum(losses)
    if gross_loss > 0:
        profit_factor = gross_profit / gross_loss
    elif gross_profit > 0:
        profit_factor = math.inf
    else:
        profit_factor = 0.0

    return PerformanceMetrics(
        starting_equity=starting_equity,
        ending_equity=ending,
        total_return=total_return,
        geometric_growth_per_trade=geometric,
        max_drawdown=maximum_drawdown([p.equity for p in equity_curve]),
        win_rate=(len(wins) / count) if count else 0.0,
        profit_factor=profit_factor,
        average_trade_return=(sum(trade_returns) / count) if count else 0.0,
        trade_return_cvar_95=empirical_cvar_loss(trade_returns, 0.95),
        trades=count,
    )


def bootstrap_probability_of_ruin(
    trade_returns: Sequence[float],
    *,
    risk_fraction: float,
    floor_fraction: float = 0.50,
    trades_per_path: int = 252,
    paths: int = 5000,
    seed: int = 7,
) -> float:
    """Bootstrap a probability of breaching the account floor.

    Each sampled historical trade return applies only to `risk_fraction` of the
    current account. This is intentionally a diagnostic, not a guarantee.
    """

    if not trade_returns:
        return 0.0
    if not 0.0 <= risk_fraction <= 1.0:
        raise ValueError("risk_fraction must be in [0, 1]")
    if not 0.0 < floor_fraction < 1.0:
        raise ValueError("floor_fraction must be in (0, 1)")
    if trades_per_path < 1 or paths < 1:
        raise ValueError("simulation sizes must be positive")

    rng = random.Random(seed)
    ruined = 0
    samples = tuple(float(r) for r in trade_returns)

    for _ in range(paths):
        wealth = 1.0
        for _ in range(trades_per_path):
            r = rng.choice(samples)
            wealth *= max(0.0, 1.0 + risk_fraction * r)
            if wealth <= floor_fraction:
                ruined += 1
                break

    return ruined / paths
