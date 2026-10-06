from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Iterator, Sequence, Tuple

from .backtest import BacktestConfig, BacktestContext, BacktestResult, BacktestSignal, run_backtest, run_backtest_stream
from .data import HistoricalFrame, load_canonical_path
from .market import OptionQuote


@dataclass(frozen=True)
class CausalBurstConfig:
    """Pre-declared quant-first SPY 0DTE burst research rules.

    Thresholds are evaluated against PRIOR observations only. This is an
    intentionally simple baseline for rebuilding the V9/V10 idea outside
    QuantConnect; it is not a claim of profitability.
    """

    acceleration_quantile: float = 0.90
    minimum_prior_accelerations: int = 390
    acceleration_history: int = 10_000
    breakout_lookback: int = 60
    momentum_lookback: int = 15
    reclaim_window: int = 5
    hold_minutes: int = 15
    max_spread_fraction: float = 0.20
    target_abs_delta: float = 0.50
    minimum_minutes_to_close: float = 20.0
    minimum_volume_ratio: float = 0.0

    def __post_init__(self) -> None:
        if not 0.50 < self.acceleration_quantile < 1.0:
            raise ValueError("acceleration_quantile must be in (0.50, 1)")
        if self.minimum_prior_accelerations < 20:
            raise ValueError("minimum_prior_accelerations must be at least 20")
        if self.acceleration_history < self.minimum_prior_accelerations:
            raise ValueError("acceleration_history must cover warmup")
        if self.breakout_lookback < 2 or self.momentum_lookback < 2:
            raise ValueError("lookbacks must be at least 2")
        if self.reclaim_window < 1 or self.hold_minutes < 1:
            raise ValueError("reclaim_window and hold_minutes must be positive")
        if not 0.0 < self.max_spread_fraction <= 1.0:
            raise ValueError("max_spread_fraction must be in (0, 1]")
        if not 0.0 < self.target_abs_delta < 1.0:
            raise ValueError("target_abs_delta must be in (0, 1)")


@dataclass(frozen=True)
class BurstStressResult:
    adverse_ticks_per_side: int
    result: BacktestResult


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


def _research_files(path: str | Path) -> Tuple[Path, ...]:
    root = Path(path)
    compressed = {item.name.removesuffix(".gz"): item for item in root.glob("spy_0dte_*.csv.gz")}
    plain = {item.name: item for item in root.glob("spy_0dte_*.csv")}
    selected = list(compressed.values())
    selected.extend(item for name, item in plain.items() if name not in compressed)
    return tuple(sorted(selected))


def iter_research_directory(path: str | Path) -> Iterator[HistoricalFrame]:
    """Yield canonical history in strict time order without materializing all days."""

    last_timestamp: datetime | None = None
    for item in _research_files(path):
        for frame in load_canonical_path(item):
            if last_timestamp is not None and frame.timestamp <= last_timestamp:
                raise ValueError(
                    f"research frames must be strictly increasing: {frame.timestamp.isoformat()}"
                )
            last_timestamp = frame.timestamp
            yield frame


def load_research_directory(path: str | Path) -> Tuple[HistoricalFrame, ...]:
    """Materialized compatibility loader; use iter_research_directory for large runs."""

    return tuple(iter_research_directory(path))


def _affordable_call(
    frame: HistoricalFrame,
    *,
    settled_cash: float,
    config: CausalBurstConfig,
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


class CausalBurstStrategy:
    """Quant-only causal burst + reclaim call baseline.

    The strategy has two call families:
    * BULL_RAW: positive acceleration above a trailing percentile, confirmed by
      a fresh 60-minute breakout and positive 15-minute momentum.
    * RECLAIM_CALL: a large negative acceleration is observed first; a call is
      eligible only if price later reclaims the pre-burst level within a fixed
      causal window.

    No put engine is included because the earlier development evidence showed
    downside bursts mean-reverting rather than continuing. That hypothesis must
    be re-tested on the new independent dataset before puts are reintroduced.
    """

    def __init__(self, config: CausalBurstConfig = CausalBurstConfig()) -> None:
        self.config = config
        self._prior_accelerations: deque[float] = deque(maxlen=config.acceleration_history)
        self._day: date | None = None
        self._spots: deque[float] = deque(maxlen=max(config.breakout_lookback, config.momentum_lookback) + 2)
        self._last_return: float | None = None
        self._reclaim_reference: float | None = None
        self._reclaim_expires_after: int = 0

    def _reset_day(self, day: date) -> None:
        self._day = day
        self._spots.clear()
        self._last_return = None
        self._reclaim_reference = None
        self._reclaim_expires_after = 0

    def decide(self, frame: HistoricalFrame, context: BacktestContext) -> BacktestSignal:
        day = frame.timestamp.date()
        if self._day != day:
            self._reset_day(day)

        spot = frame.market.spot
        prior_spots = tuple(self._spots)
        current_return: float | None = None
        acceleration: float | None = None
        if prior_spots and prior_spots[-1] > 0:
            current_return = spot / prior_spots[-1] - 1.0
            if self._last_return is not None:
                acceleration = current_return - self._last_return

        threshold: float | None = None
        if len(self._prior_accelerations) >= self.config.minimum_prior_accelerations:
            magnitudes = tuple(abs(value) for value in self._prior_accelerations)
            threshold = _percentile(magnitudes, self.config.acceleration_quantile)

        signal_reason: str | None = None
        if (
            acceleration is not None
            and threshold is not None
            and frame.market.minutes_to_close >= self.config.minimum_minutes_to_close
            and frame.market.volume_ratio >= self.config.minimum_volume_ratio
        ):
            breakout_window = prior_spots[-self.config.breakout_lookback :]
            momentum_window = prior_spots[-self.config.momentum_lookback :]

            bullish_breakout = (
                len(breakout_window) >= self.config.breakout_lookback
                and spot > max(breakout_window)
            )
            bullish_momentum = (
                len(momentum_window) >= self.config.momentum_lookback
                and momentum_window[0] > 0
                and spot / momentum_window[0] - 1.0 > 0
            )

            if acceleration >= threshold and bullish_breakout and bullish_momentum:
                signal_reason = "BULL_RAW"

            if acceleration <= -threshold:
                self._reclaim_reference = prior_spots[-1] if prior_spots else spot
                self._reclaim_expires_after = self.config.reclaim_window

        if self._reclaim_reference is not None:
            if self._reclaim_expires_after <= 0:
                self._reclaim_reference = None
            elif current_return is not None and current_return > 0 and spot >= self._reclaim_reference:
                signal_reason = signal_reason or "RECLAIM_CALL"
                self._reclaim_reference = None
                self._reclaim_expires_after = 0
            else:
                self._reclaim_expires_after -= 1

        # Every market frame updates the causal feature state, including frames
        # observed while a position is open. The current acceleration is added
        # only AFTER today's threshold/signals are evaluated, so it cannot
        # influence its own percentile.
        if acceleration is not None:
            self._prior_accelerations.append(acceleration)
        if current_return is not None:
            self._last_return = current_return
        self._spots.append(spot)

        if context.position is not None:
            held_minutes = (frame.timestamp - context.position.entry_timestamp).total_seconds() / 60.0
            if held_minutes >= self.config.hold_minutes or frame.market.minutes_to_close <= 2.0:
                return BacktestSignal("close", reason="burst_time_exit")
            return BacktestSignal("hold")

        if signal_reason is None:
            return BacktestSignal("hold")

        option = _affordable_call(frame, settled_cash=context.settled_cash, config=self.config)
        if option is None:
            return BacktestSignal("hold")
        return BacktestSignal("open", option.symbol, 1, signal_reason)


def run_burst_stress(
    frames: Sequence[HistoricalFrame],
    *,
    starting_cash: float,
    config: CausalBurstConfig = CausalBurstConfig(),
    adverse_ticks: Iterable[int] = (0, 1, 2),
) -> Tuple[BurstStressResult, ...]:
    """Run frozen burst rules under executable bid/ask and adverse-tick stress."""

    evaluations: list[BurstStressResult] = []
    for ticks in adverse_ticks:
        if ticks < 0:
            raise ValueError("adverse ticks cannot be negative")
        strategy = CausalBurstStrategy(config)
        result = run_backtest(
            frames,
            strategy,
            config=BacktestConfig(
                starting_cash=starting_cash,
                # Webull equity options are modeled commission-free here; the
                # bid/ask crossing and adverse ticks remain explicit.
                fee_per_contract=0.0,
                slippage_spread_fraction=0.0,
                adverse_ticks_per_side=ticks,
                option_tick_size=0.01,
                maximum_contracts=1,
            ),
        )
        evaluations.append(BurstStressResult(ticks, result))
    return tuple(evaluations)


def run_burst_stress_directory(
    path: str | Path,
    *,
    starting_cash: float,
    config: CausalBurstConfig = CausalBurstConfig(),
    adverse_ticks: Iterable[int] = (0, 1, 2),
) -> Tuple[BurstStressResult, ...]:
    """Stream large research datasets from disk for each execution-stress pass."""

    evaluations: list[BurstStressResult] = []
    for ticks in adverse_ticks:
        if ticks < 0:
            raise ValueError("adverse ticks cannot be negative")
        strategy = CausalBurstStrategy(config)
        result = run_backtest_stream(
            iter_research_directory(path),
            strategy,
            config=BacktestConfig(
                starting_cash=starting_cash,
                fee_per_contract=0.0,
                slippage_spread_fraction=0.0,
                adverse_ticks_per_side=ticks,
                option_tick_size=0.01,
                maximum_contracts=1,
            ),
        )
        evaluations.append(BurstStressResult(ticks, result))
    return tuple(evaluations)
