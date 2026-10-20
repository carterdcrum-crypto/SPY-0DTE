from __future__ import annotations

import math
import statistics
from collections import deque
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable, Tuple

from .backtest import BacktestConfig, BacktestContext, BacktestResult, BacktestSignal, run_backtest_stream
from .burst_research import iter_research_directory
from .market import OptionQuote


@dataclass(frozen=True)
class RegimeLongOptionConfig:
    """Causal long-option regime strategy for the SPY cash-account mandate.

    This intentionally preserves the project's current execution constraint:
    buy-to-open one SPY 0DTE call OR put, then sell-to-close that same contract.
    No spreads, short options, or multi-leg positions are introduced here.
    """

    fast_lookback: int = 5
    slow_lookback: int = 30
    rv_lookback: int = 30
    breakout_lookback: int = 30
    minimum_history: int = 60
    trend_threshold: float = 0.0015
    volatility_edge_ratio: float = 1.05
    target_abs_delta: float = 0.55
    max_spread_fraction: float = 0.15
    max_data_age_seconds: float = 5.0
    earliest_entry_minutes_to_close: float = 350.0
    latest_entry_minutes_to_close: float = 30.0
    mandatory_exit_minutes_to_close: float = 5.0
    max_hold_minutes: int = 20
    take_profit_return: float = 0.50
    stop_loss_return: float = -0.25
    hard_drawdown: float = 0.10
    daily_loss_limit: float = 0.02
    allow_puts: bool = True

    def __post_init__(self) -> None:
        if min(self.fast_lookback, self.slow_lookback, self.rv_lookback, self.breakout_lookback) < 2:
            raise ValueError("lookbacks must be at least 2")
        if self.minimum_history < max(self.slow_lookback, self.rv_lookback, self.breakout_lookback):
            raise ValueError("minimum_history must cover all lookbacks")
        if self.trend_threshold < 0:
            raise ValueError("trend_threshold cannot be negative")
        if self.volatility_edge_ratio <= 0:
            raise ValueError("volatility_edge_ratio must be positive")
        if not 0 < self.target_abs_delta < 1:
            raise ValueError("target_abs_delta must be in (0, 1)")
        if not 0 < self.max_spread_fraction <= 1:
            raise ValueError("max_spread_fraction must be in (0, 1]")
        if self.max_data_age_seconds < 0:
            raise ValueError("max_data_age_seconds cannot be negative")
        if self.latest_entry_minutes_to_close <= self.mandatory_exit_minutes_to_close:
            raise ValueError("latest entry must precede mandatory exit")
        if self.earliest_entry_minutes_to_close <= self.latest_entry_minutes_to_close:
            raise ValueError("earliest entry must be earlier in the session")
        if self.max_hold_minutes < 1:
            raise ValueError("max_hold_minutes must be positive")
        if self.take_profit_return <= 0:
            raise ValueError("take_profit_return must be positive")
        if not -1 < self.stop_loss_return < 0:
            raise ValueError("stop_loss_return must be between -1 and 0")
        if not 0 < self.hard_drawdown < 1:
            raise ValueError("hard_drawdown must be in (0, 1)")
        if not 0 < self.daily_loss_limit < 1:
            raise ValueError("daily_loss_limit must be in (0, 1)")


@dataclass(frozen=True)
class RegimeStressResult:
    adverse_ticks_per_side: int
    result: BacktestResult


def _annualized_realized_vol(returns: Iterable[float]) -> float:
    values = tuple(float(value) for value in returns)
    if len(values) < 2:
        return 0.0
    return statistics.pstdev(values) * math.sqrt(252.0 * 390.0)


def _near_atm_iv(frame) -> float:
    liquid = [
        quote
        for quote in frame.options
        if quote.bid > 0
        and quote.ask > quote.bid
        and quote.implied_volatility > 0
        and quote.spread_fraction <= 0.30
    ]
    if not liquid:
        return max(0.0, frame.market.implied_volatility)
    near = sorted(liquid, key=lambda q: abs(q.strike - frame.market.spot))[:12]
    return statistics.median(q.implied_volatility for q in near)


def _select_contract(
    frame,
    *,
    right: str,
    settled_cash: float,
    config: RegimeLongOptionConfig,
) -> OptionQuote | None:
    candidates = [
        quote
        for quote in frame.options
        if quote.right == right
        and quote.bid > 0
        and quote.ask > quote.bid
        and quote.ask * 100.0 <= settled_cash + 1e-12
        and quote.spread_fraction <= config.max_spread_fraction
        and quote.minutes_to_expiry >= config.mandatory_exit_minutes_to_close
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


class RegimeLongOptionStrategy:
    """Trend + realized/implied volatility gated long-option strategy.

    The signal is deliberately sparse. It requires aligned fast/slow direction,
    a breakout in that direction, and forecast realized volatility sufficiently
    above near-ATM implied volatility. Risk state can disable new entries even
    when a signal exists.
    """

    def __init__(self, config: RegimeLongOptionConfig = RegimeLongOptionConfig()) -> None:
        self.config = config
        history = max(
            config.minimum_history,
            config.slow_lookback + 2,
            config.rv_lookback + 2,
            config.breakout_lookback + 2,
        )
        self._spots: deque[float] = deque(maxlen=history)
        self._returns: deque[float] = deque(maxlen=history)
        self._day: date | None = None
        self._day_start_equity: float | None = None
        self._high_water_equity: float | None = None

    def _reset_day(self, day: date, equity: float) -> None:
        self._day = day
        self._day_start_equity = equity

    def _risk_allows_entry(self, context: BacktestContext) -> bool:
        equity = context.equity
        if self._high_water_equity is None:
            self._high_water_equity = equity
        else:
            self._high_water_equity = max(self._high_water_equity, equity)

        if self._high_water_equity > 0:
            drawdown = 1.0 - equity / self._high_water_equity
            if drawdown >= self.config.hard_drawdown:
                return False

        if self._day_start_equity and self._day_start_equity > 0:
            daily_return = equity / self._day_start_equity - 1.0
            if daily_return <= -self.config.daily_loss_limit:
                return False
        return True

    def _direction(self, spot: float) -> str | None:
        if len(self._spots) < self.config.minimum_history:
            return None
        prior = tuple(self._spots)
        fast_base = prior[-self.config.fast_lookback]
        slow_base = prior[-self.config.slow_lookback]
        if fast_base <= 0 or slow_base <= 0:
            return None

        fast = spot / fast_base - 1.0
        slow = spot / slow_base - 1.0
        breakout = prior[-self.config.breakout_lookback :]

        if (
            fast > 0
            and slow >= self.config.trend_threshold
            and breakout
            and spot > max(breakout)
        ):
            return "call"

        if (
            self.config.allow_puts
            and fast < 0
            and slow <= -self.config.trend_threshold
            and breakout
            and spot < min(breakout)
        ):
            return "put"
        return None

    def decide(self, frame, context: BacktestContext) -> BacktestSignal:
        day = frame.timestamp.date()
        if self._day != day:
            self._reset_day(day, context.equity)

        spot = frame.market.spot
        current_return: float | None = None
        if self._spots and self._spots[-1] > 0:
            current_return = spot / self._spots[-1] - 1.0

        risk_ok = self._risk_allows_entry(context)

        if context.position is not None:
            quote = frame.option(context.position.option_symbol)
            held_minutes = (
                frame.timestamp - context.position.entry_timestamp
            ).total_seconds() / 60.0

            if frame.market.minutes_to_close <= self.config.mandatory_exit_minutes_to_close:
                signal = BacktestSignal("close", reason="mandatory_time_exit")
            elif quote is None or quote.bid <= 0:
                signal = BacktestSignal("hold")
            else:
                mark_return = quote.bid / context.position.entry_price - 1.0
                if mark_return >= self.config.take_profit_return:
                    signal = BacktestSignal("close", reason="dynamic_take_profit")
                elif mark_return <= self.config.stop_loss_return:
                    signal = BacktestSignal("close", reason="dynamic_risk_exit")
                elif held_minutes >= self.config.max_hold_minutes:
                    signal = BacktestSignal("close", reason="max_hold_exit")
                else:
                    direction = self._direction(spot)
                    held_right = "put" if quote.right == "put" else "call"
                    if direction is not None and direction != held_right:
                        signal = BacktestSignal("close", reason="signal_invalidation")
                    else:
                        signal = BacktestSignal("hold")

            if current_return is not None:
                self._returns.append(current_return)
            self._spots.append(spot)
            return signal

        direction = self._direction(spot)

        forecast_rv = _annualized_realized_vol(tuple(self._returns)[-self.config.rv_lookback :])
        implied = _near_atm_iv(frame)
        volatility_edge = forecast_rv / implied if implied > 0 else 0.0

        eligible_time = (
            self.config.latest_entry_minutes_to_close
            <= frame.market.minutes_to_close
            <= self.config.earliest_entry_minutes_to_close
        )
        data_healthy = frame.market.data_age_seconds <= self.config.max_data_age_seconds

        signal = BacktestSignal("hold")
        if (
            risk_ok
            and direction is not None
            and eligible_time
            and data_healthy
            and volatility_edge >= self.config.volatility_edge_ratio
        ):
            option = _select_contract(
                frame,
                right=direction,
                settled_cash=context.settled_cash,
                config=self.config,
            )
            if option is not None:
                signal = BacktestSignal(
                    "open",
                    option.symbol,
                    1,
                    f"REGIME_{direction.upper()}_RV_IV",
                )

        if current_return is not None:
            self._returns.append(current_return)
        self._spots.append(spot)
        return signal


def run_regime_stress_directory(
    path: str | Path,
    *,
    starting_cash: float,
    config: RegimeLongOptionConfig = RegimeLongOptionConfig(),
    adverse_ticks: Iterable[int] = (0, 1, 2),
    start_date: date | None = None,
    end_date: date | None = None,
) -> Tuple[RegimeStressResult, ...]:
    evaluations: list[RegimeStressResult] = []
    for ticks in adverse_ticks:
        if ticks < 0:
            raise ValueError("adverse ticks cannot be negative")
        result = run_backtest_stream(
            iter_research_directory(path, start_date=start_date, end_date=end_date),
            RegimeLongOptionStrategy(config),
            config=BacktestConfig(
                starting_cash=starting_cash,
                fee_per_contract=0.0,
                slippage_spread_fraction=0.0,
                adverse_ticks_per_side=ticks,
                option_tick_size=0.01,
                maximum_contracts=1,
            ),
        )
        evaluations.append(RegimeStressResult(ticks, result))
    return tuple(evaluations)
