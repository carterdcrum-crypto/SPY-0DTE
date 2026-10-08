"""Research-only 25%-per-trade / 100%-per-day premium allocation.

The day's gross premiums spent, NOT just simultaneously open positions,
are capped at 100% of equity measured at the first frame of that session.
A trader cannot recycle option-sale proceeds before cash settlement.
Never routed to the Webull/live executor.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Literal

from .adaptive_vault import AdaptiveVaultConfig, AdaptiveVaultEventAlphaStrategy
from .backtest import BacktestConfig, BacktestContext, BacktestResult, BacktestSignal, run_backtest_stream
from .burst_research import iter_research_directory
from .data import HistoricalFrame
from .event_alpha import EventAlphaConfig
from .market import OptionQuote

Direction = Literal["call", "put"]


@dataclass(frozen=True)
class QuarterRiskConfig:
    per_trade_equity_fraction: float = 0.25
    daily_gross_premium_fraction: float = 1.00
    side: Direction = "put"
    maximum_contracts: int = 10
    # Optional, predeclared risk-controller variant. These are trigger levels,
    # not guaranteed loss caps: close orders fill on a later market frame.
    guard_enabled: bool = False
    stop_loss_of_paid_premium: float = 0.30
    daily_loss_pause_fraction: float = 0.10
    weekly_loss_pause_fraction: float = 0.15
    lifetime_drawdown_lock_enabled: bool = False
    lifetime_drawdown_trigger_fraction: float = 0.15
    # Predeclared signal confidence filter: we use the exact same original
    # breakout-event detector in either direction to isolate direction risk.
    # No tuning against the 20-day test period is performed here.

    def __post_init__(self) -> None:
        if not (0 < self.per_trade_equity_fraction <= 1):
            raise ValueError("per-trade premium fraction must be in (0, 1]")
        if not (0 < self.daily_gross_premium_fraction <= 1):
            raise ValueError("daily gross premium fraction must be in (0, 1]")
        if self.side not in ("call", "put"):
            raise ValueError("side must be call or put")
        if self.maximum_contracts < 1:
            raise ValueError("maximum_contracts must be >= 1")
        for name in ("stop_loss_of_paid_premium", "daily_loss_pause_fraction", "weekly_loss_pause_fraction", "lifetime_drawdown_trigger_fraction"):
            value = getattr(self, name)
            if not math.isfinite(value) or not 0 < value < 1:
                raise ValueError(f"{name} must be finite and in (0, 1)")


@dataclass(frozen=True)
class QuarterRiskStressResult:
    adverse_ticks: int
    side: Direction
    result: BacktestResult
    total_entry_premium: float
    daily_spend_max_fraction: float
    filled_entry_count: int
    locked_profit: float
    skipped_for_affordability: int
    entry_pauses: int
    stop_triggers: int
    lifetime_lock_triggered: bool
    missing_position_quote_frames: int
    held_position_frames: int


class QuarterRiskStrategy(AdaptiveVaultEventAlphaStrategy):
    """One open position at a time, 25% gross paid premium per new entry.

    The day limit is a GROSS debit total including premiums from closed
    positions. The next-frame max_total_cost also includes slip/fees. We
    observe only filled positions to debit the daily allowance.
    """

    def __init__(
        self, *, starting_cash: float,
        quarter_config: QuarterRiskConfig = QuarterRiskConfig(),
        event_config: EventAlphaConfig = EventAlphaConfig(),
        trade_start_date: date | None = None,
    ) -> None:
        self.quarter_config = quarter_config
        # Default to fixed research side; tape-aware research may override per frame.
        self._signal_direction = quarter_config.side
        super().__init__(
            starting_cash=starting_cash,
            event_config=event_config,
            adaptive_config=AdaptiveVaultConfig(maximum_contracts=quarter_config.maximum_contracts),
            trade_start_date=trade_start_date,
        )
        self._session: date | None = None
        self._session_start_equity = starting_cash
        self._daily_premiums_spent = 0.0
        self._seen_position_entry: datetime | None = None
        self.max_daily_spend_fraction = 0.0
        self.total_entry_premium = 0.0
        self.filled_entry_count = 0
        self.skipped_for_affordability = 0
        self._week_id: tuple[int, int] | None = None
        self._week_start_equity = starting_cash
        self.entry_pauses = 0
        self.stop_triggers = 0
        self.lifetime_lock_triggered = False
        self.missing_position_quote_frames = 0
        self.held_position_frames = 0

    def _select_entry_option(self, frame: HistoricalFrame, budget: float) -> OptionQuote | None:
        eligible = [
            option for option in frame.options
            if option.right == self._signal_direction
            and option.ask > option.bid > 0
            and option.ask * 100 <= budget + 1e-12
            and option.spread_fraction <= self.config.max_spread_fraction
            and option.minutes_to_expiry >= self.config.minimum_minutes_to_close
            and math.isfinite(option.ask)
            and math.isfinite(option.bid)
            and math.isfinite(option.delta)
        ]
        if not eligible:
            if budget > 0:
                self.skipped_for_affordability += 1
            return None
        return min(
            eligible,
            key=lambda q: (
                abs(abs(q.delta) - self.config.target_abs_delta),
                q.spread_fraction, abs(q.strike - frame.market.spot), q.ask,
            ),
        )

    def _entry_budget(self, context: BacktestContext) -> float:
        original = super()._entry_budget(context)
        # Account equity is marked on the current frame, so we cannot spend
        # more than 25% of even today's diminished equity.
        single_cap = max(0.0, context.equity) * self.quarter_config.per_trade_equity_fraction
        day_cap = self._session_start_equity * self.quarter_config.daily_gross_premium_fraction
        remaining_day = max(0.0, day_cap - self._daily_premiums_spent)
        return max(0.0, min(original, single_cap, remaining_day))

    def _entry_signal(self, option: OptionQuote, budget: float) -> BacktestSignal:
        signal = super()._entry_signal(option, budget)
        if signal.action == "open":
            return BacktestSignal(
                "open", option.symbol, signal.quantity,
                f"EVENT_ALPHA_{self._signal_direction.upper()}_QUARTER_CAP",
                max_total_cost=budget,
            )
        return signal

    def decide(self, frame: HistoricalFrame, context: BacktestContext) -> BacktestSignal:
        today = frame.timestamp.date()
        if today != self._session:
            self._session = today
            self._session_start_equity = context.equity
            self._daily_premiums_spent = 0.0
        week = frame.timestamp.isocalendar()
        week_id = (week.year, week.week)
        if week_id != self._week_id:
            self._week_id = week_id
            self._week_start_equity = context.equity
        position = context.position
        if position is not None:
            self.held_position_frames += 1
            if frame.option(position.option_symbol) is None:
                self.missing_position_quote_frames += 1
        if position is not None and position.entry_timestamp != self._seen_position_entry:
            self._seen_position_entry = position.entry_timestamp
            self._daily_premiums_spent += position.entry_cost
            self.total_entry_premium += position.entry_cost
            self.filled_entry_count += 1
            day_cap = self._session_start_equity * self.quarter_config.daily_gross_premium_fraction
            if self._daily_premiums_spent > day_cap + 1e-6:
                raise ValueError("daily gross premium limit exceeded at fill")
            self.max_daily_spend_fraction = max(
                self.max_daily_spend_fraction,
                self._daily_premiums_spent / self._session_start_equity,
            )
        baseline_signal = super().decide(frame, context)
        if self.quarter_config.lifetime_drawdown_lock_enabled:
            peak = max(self.starting_cash, self.realized_high_watermark)
            threshold = peak * (1.0 - self.quarter_config.lifetime_drawdown_trigger_fraction)
            # Never mark a missing quote as proof of a position loss.
            valid_mark = position is None or frame.option(position.option_symbol) is not None
            if valid_mark and context.equity <= threshold:
                self.lifetime_lock_triggered = True
            if self.lifetime_lock_triggered:
                if position is None:
                    if baseline_signal.action == "open":
                        self.entry_pauses += 1
                    return BacktestSignal("hold")
                if baseline_signal.action == "hold" and valid_mark:
                    self.stop_triggers += 1
                    return BacktestSignal("close", reason="quarter_guard_lifetime_drawdown_lock")
        if not self.quarter_config.guard_enabled:
            return baseline_signal

        # Stop at the *observable* bid. The next-frame fill may be much lower;
        # no simulated/real broker guarantees a maximum loss at this trigger.
        if position is not None and baseline_signal.action == "hold":
            quote = frame.option(position.option_symbol)
            if quote is not None and quote.bid > 0:
                limit = position.entry_price * (1.0 - self.quarter_config.stop_loss_of_paid_premium)
                if quote.bid <= limit:
                    self.stop_triggers += 1
                    return BacktestSignal("close", reason="quarter_guard_premium_stop")

        if position is None and baseline_signal.action == "open":
            day_floor = self._session_start_equity * (1.0 - self.quarter_config.daily_loss_pause_fraction)
            week_floor = self._week_start_equity * (1.0 - self.quarter_config.weekly_loss_pause_fraction)
            if context.equity < day_floor or context.equity < week_floor:
                self.entry_pauses += 1
                return BacktestSignal("hold")
        return baseline_signal


def run_quarter_risk_directory(
    path: str | Path,
    *, starting_cash: float,
    quarter_config: QuarterRiskConfig = QuarterRiskConfig(),
    event_config: EventAlphaConfig = EventAlphaConfig(),
    adverse_ticks: Iterable[int] = (0, 1, 2),
    start_date: date | None = None,
    end_date: date | None = None,
    trade_start_date: date | None = None,
) -> tuple[QuarterRiskStressResult, ...]:
    results = []
    for ticks in adverse_ticks:
        if ticks < 0:
            raise ValueError("adverse ticks must be nonnegative")
        strategy = QuarterRiskStrategy(
            starting_cash=starting_cash,
            quarter_config=quarter_config,
            event_config=event_config,
            trade_start_date=trade_start_date,
        )
        result = run_backtest_stream(
            iter_research_directory(path, start_date=start_date, end_date=end_date),
            strategy,
            config=BacktestConfig(
                starting_cash=starting_cash,
                fee_per_contract=0.0,
                slippage_spread_fraction=0.0,
                adverse_ticks_per_side=ticks,
                option_tick_size=0.01,
                maximum_contracts=quarter_config.maximum_contracts,
            ),
        )
        results.append(QuarterRiskStressResult(
            ticks, quarter_config.side, result, strategy.total_entry_premium,
            strategy.max_daily_spend_fraction, strategy.filled_entry_count,
            strategy.locked_profit, strategy.skipped_for_affordability,
            strategy.entry_pauses, strategy.stop_triggers,
            strategy.lifetime_lock_triggered,
            strategy.missing_position_quote_frames, strategy.held_position_frames,
        ))
    return tuple(results)


def nonoverlapping_weekly_returns(result: BacktestResult, *, trade_start_date: date | None = None) -> tuple[float, ...]:
    """Five consecutive observed trading days per block, not 7 calendar days.

    The first partial block is deliberately excluded; includes only complete
    5-session blocks, avoiding a misleading high number from 1-2 days.
    """
    by_day: dict[date, float] = {}
    for point in result.equity_curve:
        day = point.timestamp.date()
        if trade_start_date is None or day >= trade_start_date:
            by_day[day] = point.equity
    keys = sorted(by_day)
    if not keys:
        return ()
    week_returns: list[float] = []
    # The full first 5-day trading block uses equity at its first day open,
    # approximated by starting equity before trading that session. For the
    # remaining blocks, previous block's final close is the baseline.
    if len(keys) >= 5:
        starting = result.equity_curve[0].equity
        if trade_start_date:
            for point in result.equity_curve:
                if point.timestamp.date() == keys[0]:
                    starting = point.equity
                    break
        for index in range(4, len(keys), 5):
            ending = by_day[keys[index]]
            if starting > 0:
                week_returns.append(ending / starting - 1)
            starting = ending
    return tuple(week_returns)
