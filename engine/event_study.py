from __future__ import annotations

import math
import statistics
from collections import deque
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Dict, Iterable, Sequence, Tuple

from .burst_research import iter_research_directory


@dataclass(frozen=True)
class EventStudyConfig:
    momentum_lookback: int = 5
    trend_lookback: int = 15
    breakout_lookback: int = 60
    threshold_days: int = 5
    reclaim_window: int = 5
    horizons: tuple[int, ...] = (5, 10, 15, 30, 45, 60)
    quantiles: tuple[float, ...] = (0.90, 0.95)

    def __post_init__(self) -> None:
        if self.momentum_lookback < 2 or self.trend_lookback < 2 or self.breakout_lookback < 2:
            raise ValueError("lookbacks must be at least 2")
        if self.threshold_days < 2:
            raise ValueError("threshold_days must be at least 2")
        if self.reclaim_window < 1:
            raise ValueError("reclaim_window must be positive")
        if not self.horizons or any(h <= 0 for h in self.horizons):
            raise ValueError("horizons must be positive")
        if any(not 0.5 < q < 1.0 for q in self.quantiles):
            raise ValueError("quantiles must be in (0.5, 1)")


@dataclass(frozen=True)
class EventObservation:
    event: str
    direction: str
    horizon_minutes: int
    signed_forward_return: float


@dataclass(frozen=True)
class EventSummary:
    event: str
    direction: str
    horizon_minutes: int
    observations: int
    mean_bps: float
    median_bps: float
    win_rate: float
    p10_bps: float
    p90_bps: float
    mean_to_p10: float


def _percentile(values: Sequence[float], q: float) -> float:
    if not values:
        raise ValueError("values cannot be empty")
    ordered = sorted(float(value) for value in values)
    position = q * (len(ordered) - 1)
    low = int(math.floor(position))
    high = int(math.ceil(position))
    if low == high:
        return ordered[low]
    weight = position - low
    return ordered[low] * (1.0 - weight) + ordered[high] * weight


def _summarize(observations: Iterable[EventObservation]) -> Tuple[EventSummary, ...]:
    grouped: Dict[tuple[str, str, int], list[float]] = {}
    for obs in observations:
        grouped.setdefault((obs.event, obs.direction, obs.horizon_minutes), []).append(
            obs.signed_forward_return
        )

    summaries: list[EventSummary] = []
    for (event, direction, horizon), values in sorted(grouped.items()):
        mean = statistics.fmean(values)
        median = statistics.median(values)
        p10 = _percentile(values, 0.10)
        p90 = _percentile(values, 0.90)
        downside = abs(min(0.0, p10))
        ratio = mean / downside if downside > 0 else (math.inf if mean > 0 else 0.0)
        summaries.append(
            EventSummary(
                event=event,
                direction=direction,
                horizon_minutes=horizon,
                observations=len(values),
                mean_bps=mean * 10_000.0,
                median_bps=median * 10_000.0,
                win_rate=sum(value > 0 for value in values) / len(values),
                p10_bps=p10 * 10_000.0,
                p90_bps=p90 * 10_000.0,
                mean_to_p10=ratio,
            )
        )
    return tuple(summaries)


def run_causal_event_study(
    path: str | Path,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    config: EventStudyConfig = EventStudyConfig(),
) -> Tuple[EventSummary, ...]:
    """Mine causal SPY event families before mapping them to option P&L.

    Event thresholds use only PRIOR trading days. Forward returns are outcomes
    used for evaluation, never inputs to the event detector.
    """

    frames = iter_research_directory(path, start_date=start_date, end_date=end_date)
    by_day: dict[date, list] = {}
    for frame in frames:
        by_day.setdefault(frame.timestamp.date(), []).append(frame)

    prior_day_accelerations: deque[tuple[float, ...]] = deque(maxlen=config.threshold_days)
    observations: list[EventObservation] = []

    for trade_day in sorted(by_day):
        day_frames = sorted(by_day[trade_day], key=lambda frame: frame.timestamp)
        spots = [frame.market.spot for frame in day_frames]
        if len(spots) < max(config.breakout_lookback, max(config.horizons)) + 2:
            continue

        prior_accels = [
            abs(value)
            for day_values in prior_day_accelerations
            for value in day_values
            if math.isfinite(value)
        ]
        q90 = _percentile(prior_accels, 0.90) if len(prior_day_accelerations) >= config.threshold_days and prior_accels else None
        q95 = _percentile(prior_accels, 0.95) if len(prior_day_accelerations) >= config.threshold_days and prior_accels else None

        day_accels: list[float] = []
        reclaim_reference: float | None = None
        reclaim_expires_index: int | None = None

        for i, spot in enumerate(spots):
            if i < config.momentum_lookback + 1:
                continue

            momentum_now = spot / spots[i - config.momentum_lookback] - 1.0
            momentum_prev = spots[i - 1] / spots[i - 1 - config.momentum_lookback] - 1.0
            acceleration = momentum_now - momentum_prev
            day_accels.append(acceleration)

            if q90 is None or q95 is None:
                continue

            trend_ok = i >= config.trend_lookback
            breakout_ok = i >= config.breakout_lookback
            trend = (
                spot / spots[i - config.trend_lookback] - 1.0
                if trend_ok
                else 0.0
            )
            prior_breakout = (
                spots[i - config.breakout_lookback : i]
                if breakout_ok
                else ()
            )

            events: list[tuple[str, str]] = []
            if acceleration >= q90 and breakout_ok and spot > max(prior_breakout) and trend > 0:
                events.append(("BULL_ACCEL90_BREAKOUT", "call"))
            if acceleration >= q95 and trend > 0:
                events.append(("BULL_ACCEL95_MOMENTUM", "call"))
            if acceleration <= -q90 and breakout_ok and spot < min(prior_breakout) and trend < 0:
                events.append(("BEAR_ACCEL90_BREAKOUT", "put"))

            if acceleration <= -q90 and i > 0:
                reclaim_reference = spots[i - 1]
                reclaim_expires_index = i + config.reclaim_window

            if reclaim_reference is not None and reclaim_expires_index is not None:
                if i > reclaim_expires_index:
                    reclaim_reference = None
                    reclaim_expires_index = None
                elif i > 0 and spot >= reclaim_reference and spot > spots[i - 1]:
                    events.append(("DOWNSIDE_BURST_RECLAIM", "call"))
                    reclaim_reference = None
                    reclaim_expires_index = None

            for event, direction in events:
                sign = 1.0 if direction == "call" else -1.0
                for horizon in config.horizons:
                    j = i + horizon
                    if j >= len(spots):
                        continue
                    forward = spots[j] / spot - 1.0
                    observations.append(
                        EventObservation(
                            event=event,
                            direction=direction,
                            horizon_minutes=horizon,
                            signed_forward_return=sign * forward,
                        )
                    )

        prior_day_accelerations.append(tuple(day_accels))

    return _summarize(observations)
