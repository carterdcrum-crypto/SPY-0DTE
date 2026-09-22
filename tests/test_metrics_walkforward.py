from __future__ import annotations

from datetime import datetime, timedelta, timezone

from engine.data import HistoricalFrame
from engine.market import MarketSnapshot
from engine.metrics import EquityPoint, bootstrap_probability_of_ruin, performance_metrics
from engine.walkforward import walk_forward_splits


def f(day: int) -> HistoricalFrame:
    return HistoricalFrame(
        datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=day),
        MarketSnapshot(600, 599.9, 600.1, .15, .18, 1.0, 100),
        (),
    )


def test_performance_metrics_measure_drawdown_and_geometric_growth():
    points = (
        EquityPoint(0, 1000),
        EquityPoint(1, 1100),
        EquityPoint(2, 990),
        EquityPoint(3, 1210),
    )
    metrics = performance_metrics(
        starting_equity=1000,
        equity_curve=points,
        trade_returns=(.10, -.10, .2222222222),
    )
    assert abs(metrics.total_return - .21) < 1e-12
    assert abs(metrics.max_drawdown - .10) < 1e-12
    assert metrics.trades == 3
    assert metrics.win_rate == 2/3


def test_bootstrap_ruin_is_deterministic_and_bounded():
    p1 = bootstrap_probability_of_ruin(
        (-1.0, .25, .25, .25),
        risk_fraction=.10,
        trades_per_path=50,
        paths=500,
        seed=123,
    )
    p2 = bootstrap_probability_of_ruin(
        (-1.0, .25, .25, .25),
        risk_fraction=.10,
        trades_per_path=50,
        paths=500,
        seed=123,
    )
    assert p1 == p2
    assert 0.0 <= p1 <= 1.0


def test_walk_forward_has_purged_nonoverlapping_phases():
    frames = tuple(f(i) for i in range(40))
    splits = walk_forward_splits(
        frames,
        train=timedelta(days=10),
        validation=timedelta(days=5),
        test=timedelta(days=5),
        purge=timedelta(days=1),
        step=timedelta(days=5),
    )
    assert splits
    first = splits[0]
    assert first.train[-1].timestamp < first.validation[0].timestamp
    assert first.validation[-1].timestamp < first.test[0].timestamp
    assert first.validation[0].timestamp - first.train[-1].timestamp >= timedelta(days=2)
    assert first.test[0].timestamp - first.validation[-1].timestamp >= timedelta(days=2)
