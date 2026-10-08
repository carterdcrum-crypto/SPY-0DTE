from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable, Tuple

from .backtest import BacktestConfig, BacktestContext, BacktestResult, BacktestSignal, run_backtest_stream
from .burst_research import iter_research_directory
from .data import HistoricalFrame
from .market import OptionQuote


@dataclass(frozen=True)
class EventAlphaConfig:
    threshold_days: int = 5
    momentum_lookback: int = 5
    trend_lookback: int = 15
    breakout_lookback: int = 60
    acceleration_quantile: float = 0.90
    hold_minutes: int = 15
    target_abs_delta: float = 0.45
    max_spread_fraction: float = 0.15
    minimum_minutes_to_close: float = 25.0

    def __post_init__(self) -> None:
        if self.threshold_days < 2:
            raise ValueError("threshold_days must be at least 2")
        if min(self.momentum_lookback, self.trend_lookback, self.breakout_lookback) < 2:
            raise ValueError("lookbacks must be at least 2")
        if not 0.50 < self.acceleration_quantile < 1.0:
            raise ValueError("acceleration_quantile must be in (0.50, 1)")
        if self.hold_minutes < 1:
            raise ValueError("hold_minutes must be positive")
        if not 0.0 < self.target_abs_delta < 1.0:
            raise ValueError("target_abs_delta must be in (0, 1)")
        if not 0.0 < self.max_spread_fraction <= 1.0:
            raise ValueError("max_spread_fraction must be in (0, 1]")
        if self.minimum_minutes_to_close <= self.hold_minutes:
            raise ValueError("minimum_minutes_to_close must exceed hold_minutes")


@dataclass(frozen=True)
class EventAlphaStressResult:
    adverse_ticks_per_side: int
    result: BacktestResult


def _percentile(values: Iterable[float], q: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise ValueError("values cannot be empty")
    position = q * (len(ordered) - 1)
    low = int(math.floor(position))
    high = int(math.ceil(position))
    if low == high:
        return ordered[low]
    weight = position - low
    return ordered[low] * (1.0 - weight) + ordered[high] * weight


def _select_call(
    frame: HistoricalFrame,
    *,
    settled_cash: float,
    config: EventAlphaConfig,
) -> OptionQuote | None:
    candidates = [
        quote
        for quote in frame.options
        if quote.right == "call"
        and quote.bid > 0
        and quote.ask > quote.bid
        and quote.ask * 100.0 <= settled_cash + 1e-12
        and quote.spread_fraction <= config.max_spread_fraction
        and quote.minutes_to_expiry >= config.minimum_minutes_to_close
    ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda quote: (
            abs(abs(quote.delta) - config.target_abs_delta),
            quote.spread_fraction,
            abs(quote.strike - frame.market.spot),
            quote.ask,
        ),
    )


class EventAlphaStrategy:
    """Frozen call-only mapping of the strongest causal event-study family.

    Entry requires:
      * 5-minute momentum acceleration >= trailing 90th percentile,
      * threshold computed from the previous 5 trading days only,
      * fresh 60-minute breakout,
      * positive 15-minute trend.

    The strategy intentionally excludes reclaim calls and puts because the
    development event study found those families materially weaker.
    """

    def __init__(self, config: EventAlphaConfig = EventAlphaConfig()) -> None:
        self.config = config
        self._day: date | None = None
        self._spots: list[float] = []
        self._day_accels: list[float] = []
        self._prior_day_accels: deque[tuple[float, ...]] = deque(maxlen=config.threshold_days)

    def _roll_day(self, day: date) -> None:
        if self._day is not None and self._day_accels:
            self._prior_day_accels.append(tuple(self._day_accels))
        self._day = day
        self._spots = []
        self._day_accels = []

    def _threshold(self) -> float | None:
        if len(self._prior_day_accels) < self.config.threshold_days:
            return None
        values = [
            abs(value)
            for day_values in self._prior_day_accels
            for value in day_values
            if math.isfinite(value)
        ]
        if not values:
            return None
        return _percentile(values, self.config.acceleration_quantile)

    def decide(self, frame: HistoricalFrame, context: BacktestContext) -> BacktestSignal:
        day = frame.timestamp.date()
        if self._day != day:
            self._roll_day(day)

        spot = frame.market.spot
        i = len(self._spots)

        # Update the causal acceleration stream on EVERY market frame, including
        # frames observed while a position is open. Otherwise the next day's
        # 5-day threshold would depend on whether we happened to be trading.
        acceleration: float | None = None
        if i >= self.config.momentum_lookback + 1:
            momentum_now = spot / self._spots[i - self.config.momentum_lookback] - 1.0
            momentum_prev = (
                self._spots[i - 1]
                / self._spots[i - 1 - self.config.momentum_lookback]
                - 1.0
            )
            acceleration = momentum_now - momentum_prev
            self._day_accels.append(acceleration)

        if context.position is not None:
            held_minutes = (
                frame.timestamp - context.position.entry_timestamp
            ).total_seconds() / 60.0
            self._spots.append(spot)
            if held_minutes >= self.config.hold_minutes or frame.market.minutes_to_close <= 2.0:
                return BacktestSignal("close", reason="event_alpha_time_exit")
            return BacktestSignal("hold")

        signal = BacktestSignal("hold")
        minimum_index = max(
            self.config.momentum_lookback + 1,
            self.config.trend_lookback,
            self.config.breakout_lookback,
        )

        if i >= minimum_index and acceleration is not None:
            threshold = self._threshold()

            trend = spot / self._spots[i - self.config.trend_lookback] - 1.0
            breakout_window = self._spots[i - self.config.breakout_lookback : i]
            event = (
                threshold is not None
                and acceleration >= threshold
                and trend > 0
                and breakout_window
                and spot > max(breakout_window)
                and frame.market.minutes_to_close >= self.config.minimum_minutes_to_close
            )

            if event:
                option = _select_call(
                    frame,
                    settled_cash=context.settled_cash,
                    config=self.config,
                )
                if option is not None:
                    signal = BacktestSignal(
                        "open",
                        option.symbol,
                        1,
                        "BULL_ACCEL90_BREAKOUT",
                    )
        self._spots.append(spot)
        return signal


def run_event_alpha_stress_directory(
    path: str | Path,
    *,
    starting_cash: float,
    config: EventAlphaConfig = EventAlphaConfig(),
    adverse_ticks: Iterable[int] = (0, 1, 2),
    start_date: date | None = None,
    end_date: date | None = None,
) -> Tuple[EventAlphaStressResult, ...]:
    evaluations: list[EventAlphaStressResult] = []
    for ticks in adverse_ticks:
        if ticks < 0:
            raise ValueError("adverse ticks cannot be negative")
        result = run_backtest_stream(
            iter_research_directory(path, start_date=start_date, end_date=end_date),
            EventAlphaStrategy(config),
            config=BacktestConfig(
                starting_cash=starting_cash,
                fee_per_contract=0.0,
                slippage_spread_fraction=0.0,
                adverse_ticks_per_side=ticks,
                option_tick_size=0.01,
                maximum_contracts=1,
            ),
        )
        evaluations.append(EventAlphaStressResult(ticks, result))
    return tuple(evaluations)
