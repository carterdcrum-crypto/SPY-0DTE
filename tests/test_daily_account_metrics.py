from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from engine.metrics import EquityPoint, daily_account_metrics


def _point(day: int, hour: int, equity: float) -> EquityPoint:
    return EquityPoint(
        datetime(2026, 10, day, hour, 0, tzinfo=timezone.utc),
        equity,
    )


def test_daily_account_metrics_include_no_trade_days_and_compounding_targets():
    curve = (
        _point(1, 14, 100.0),
        _point(1, 20, 110.0),
        _point(2, 14, 110.0),
        _point(2, 20, 99.0),
        _point(3, 14, 99.0),
        _point(3, 20, 123.75),
    )

    metrics = daily_account_metrics(curve)

    assert metrics.days == 3
    assert metrics.mean_return == pytest.approx((0.10 - 0.10 + 0.25) / 3.0)
    assert metrics.median_return == pytest.approx(0.10)
    assert metrics.geometric_return == pytest.approx((1.10 * 0.90 * 1.25) ** (1.0 / 3.0) - 1.0)
    assert metrics.positive_day_rate == pytest.approx(2.0 / 3.0)
    assert metrics.hit_5_rate == pytest.approx(2.0 / 3.0)
    assert metrics.hit_10_rate == pytest.approx(2.0 / 3.0)
    assert metrics.hit_15_rate == pytest.approx(1.0 / 3.0)
    assert metrics.hit_20_rate == pytest.approx(1.0 / 3.0)
    assert metrics.hit_25_rate == pytest.approx(1.0 / 3.0)
    assert metrics.best_day_return == pytest.approx(0.25)
    assert metrics.worst_day_return == pytest.approx(-0.10)
    assert metrics.daily_loss_cvar_99 == pytest.approx(0.10)


def test_daily_account_metrics_can_exclude_warmup_dates():
    curve = (
        _point(1, 14, 100.0),
        _point(1, 20, 200.0),
        _point(2, 14, 200.0),
        _point(2, 20, 210.0),
    )

    metrics = daily_account_metrics(curve, start_date=date(2026, 10, 2))

    assert metrics.days == 1
    assert metrics.mean_return == pytest.approx(0.05)
    assert metrics.hit_5_rate == pytest.approx(1.0)
