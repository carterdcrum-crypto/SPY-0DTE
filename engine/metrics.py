from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import date
from statistics import median
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


@dataclass(frozen=True)
class DailyAccountMetrics:
    days: int
    mean_return: float
    median_return: float
    geometric_return: float
    positive_day_rate: float
    hit_5_rate: float
    hit_10_rate: float
    hit_15_rate: float
    hit_20_rate: float
    hit_25_rate: float
    best_day_return: float
    worst_day_return: float
    daily_loss_cvar_99: float


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


def daily_account_metrics(
    equity_curve: Sequence[EquityPoint],
    *,
    start_date: date | None = None,
    end_date: date | None = None,
) -> DailyAccountMetrics:
    """Summarize start-to-end account returns for each trading day.

    The first and last marked account equities of each date define that day's
    account return. No-trade days remain in the denominator, which is important
    when evaluating account-level compounding targets.
    """

    daily_open: dict[date, float] = {}
    daily_close: dict[date, float] = {}

    for point in equity_curve:
        stamp = point.timestamp
        if not hasattr(stamp, "date"):
            raise TypeError("equity point timestamp must provide date()")
        day = stamp.date()
        if start_date is not None and day < start_date:
            continue
        if end_date is not None and day > end_date:
            continue
        daily_open.setdefault(day, float(point.equity))
        daily_close[day] = float(point.equity)

    returns: list[float] = []
    for day in sorted(daily_open):
        opening = daily_open[day]
        closing = daily_close[day]
        if opening <= 0:
            continue
        returns.append(closing / opening - 1.0)

    count = len(returns)
    if not count:
        return DailyAccountMetrics(
            days=0,
            mean_return=0.0,
            median_return=0.0,
            geometric_return=0.0,
            positive_day_rate=0.0,
            hit_5_rate=0.0,
            hit_10_rate=0.0,
            hit_15_rate=0.0,
            hit_20_rate=0.0,
            hit_25_rate=0.0,
            best_day_return=0.0,
            worst_day_return=0.0,
            daily_loss_cvar_99=0.0,
        )

    growth = 1.0
    for value in returns:
        growth *= max(0.0, 1.0 + value)
    geometric = growth ** (1.0 / count) - 1.0 if growth > 0 else -1.0

    def hit(threshold: float) -> float:
        return sum(value >= threshold for value in returns) / count

    return DailyAccountMetrics(
        days=count,
        mean_return=sum(returns) / count,
        median_return=float(median(returns)),
        geometric_return=geometric,
        positive_day_rate=sum(value > 0 for value in returns) / count,
        hit_5_rate=hit(0.05),
        hit_10_rate=hit(0.10),
        hit_15_rate=hit(0.15),
        hit_20_rate=hit(0.20),
        hit_25_rate=hit(0.25),
        best_day_return=max(returns),
        worst_day_return=min(returns),
        daily_loss_cvar_99=empirical_cvar_loss(returns, 0.99),
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
