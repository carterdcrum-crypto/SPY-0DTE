"""Research-only adaptive profit ratchet for aggressive Event Alpha trading.

This model compounds when at a realized high, automatically reduces exposure
after an equity drawdown or losing streak, and commits a monotone part of
REALIZED gains to a non-trading reserve. It does not change any live order
executor or claim that options straddles hedge trading-account losses.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable, Tuple

from .backtest import BacktestConfig, BacktestContext, BacktestResult, BacktestSignal, run_backtest_stream
from .burst_research import iter_research_directory
from .event_alpha import EventAlphaConfig
from .rocket_vault import RocketVaultConfig, RocketVaultEventAlphaStrategy


@dataclass(frozen=True)
class AdaptiveVaultConfig:
    # Ratchet starts at +10% realized account growth, not +100%.
    activation_multiple: float = 1.10
    first_lock_fraction: float = 0.35
    max_lock_fraction: float = 0.90
    peak_exposure_fraction: float = 0.90
    maximum_contracts: int = 10
    # Exposure shrinks continuously with drawdown, without arbitrary
    # discretionary re-arming, then expands as the account recovers.
    drawdown_exponent: float = 3.0
    loss_streak_penalty: float = 0.25
    # Executable-bid trailing exit after a sizable move in our favor.
    trailing_arm_return: float = 0.30
    trailing_profit_retention: float = 0.40

    def __post_init__(self) -> None:
        RocketVaultConfig(
            activation_multiple=self.activation_multiple,
            first_lock_fraction=self.first_lock_fraction,
            max_lock_fraction=self.max_lock_fraction,
            growth_exposure_fraction=self.peak_exposure_fraction,
            maximum_contracts=self.maximum_contracts,
        )
        if not math.isfinite(self.drawdown_exponent) or self.drawdown_exponent <= 0:
            raise ValueError("drawdown_exponent must be positive finite")
        if not math.isfinite(self.loss_streak_penalty) or self.loss_streak_penalty < 0:
            raise ValueError("loss_streak_penalty must be finite and nonnegative")
        if not math.isfinite(self.trailing_arm_return) or self.trailing_arm_return <= 0:
            raise ValueError("trailing_arm_return must be positive finite")
        if not 0.0 <= self.trailing_profit_retention <= 1.0:
            raise ValueError("trailing_profit_retention must be between 0 and 1")


@dataclass(frozen=True)
class AdaptiveVaultStressResult:
    adverse_ticks_per_side: int
    result: BacktestResult
    locked_profit: float
    realized_high_watermark: float
    final_exposure_fraction: float
    losing_streak: int


class AdaptiveVaultEventAlphaStrategy(RocketVaultEventAlphaStrategy):
    """A profit reserve plus a continuous exposure controller.

    Locks are based only on realized flat account equity. For an open position,
    trailing exit decisions are computed from actual historical bid quotes
    but execute on the NEXT frame; no stop-fill is guaranteed.
    """

    def __init__(
        self,
        *,
        starting_cash: float,
        event_config: EventAlphaConfig = EventAlphaConfig(),
        adaptive_config: AdaptiveVaultConfig = AdaptiveVaultConfig(),
        trade_start_date: date | None = None,
    ) -> None:
        self.adaptive_config = adaptive_config
        super().__init__(
            starting_cash=starting_cash,
            event_config=event_config,
            trade_start_date=trade_start_date,
            vault_config=RocketVaultConfig(
                activation_multiple=adaptive_config.activation_multiple,
                first_lock_fraction=adaptive_config.first_lock_fraction,
                max_lock_fraction=adaptive_config.max_lock_fraction,
                growth_exposure_fraction=adaptive_config.peak_exposure_fraction,
                maximum_contracts=adaptive_config.maximum_contracts,
            ),
        )
        self._position_was_open = False
        self._last_flat_equity = starting_cash
        self._entry_anchor_equity = starting_cash
        self.losing_streak = 0
        self.last_exposure_fraction = adaptive_config.peak_exposure_fraction
        self._trailing_symbol: str | None = None
        self._peak_bid: float | None = None

    def _entry_budget(self, context: BacktestContext) -> float:
        spendable = min(context.settled_cash, max(0.0, context.equity - self.locked_profit))
        if spendable <= 0:
            self.last_exposure_fraction = 0
            return 0
        peak = max(self.realized_high_watermark, self.starting_cash)
        drawdown_ratio = min(1.0, max(0.0, context.equity / peak))
        exposure = (
            self.adaptive_config.peak_exposure_fraction
            * drawdown_ratio ** self.adaptive_config.drawdown_exponent
            / (1.0 + self.adaptive_config.loss_streak_penalty * self.losing_streak)
        )
        self.last_exposure_fraction = exposure
        return spendable * exposure

    def decide(self, frame, context: BacktestContext) -> BacktestSignal:
        if context.position is not None:
            if not self._position_was_open:
                self._entry_anchor_equity = self._last_flat_equity
                self._trailing_symbol = context.position.option_symbol
                self._peak_bid = None
            self._position_was_open = True
        else:
            if self._position_was_open:
                # An exit has actually settled into the paper ledger; don't
                # infer a loss from temporarily marked-but-unrealized prices.
                if context.equity < self._entry_anchor_equity - 1e-8:
                    self.losing_streak += 1
                elif context.equity > self._entry_anchor_equity + 1e-8:
                    self.losing_streak = 0
            self._position_was_open = False
            self._trailing_symbol = None
            self._peak_bid = None
            self._last_flat_equity = context.equity

        # Calling the parent first ALWAYS updates the causal momentum stream,
        # including while holding a position and when a trailing exit fires.
        signal = super().decide(frame, context)
        position = context.position
        if position is None or signal.action != "hold":
            return signal

        quote = frame.option(position.option_symbol)
        if quote is None or quote.bid <= 0:
            return signal
        if self._trailing_symbol != position.option_symbol:
            self._trailing_symbol = position.option_symbol
            self._peak_bid = None
        self._peak_bid = max(self._peak_bid or 0.0, quote.bid)
        if self._peak_bid < position.entry_price * (1.0 + self.adaptive_config.trailing_arm_return):
            return signal
        protected_bid = position.entry_price + (
            self._peak_bid - position.entry_price
        ) * self.adaptive_config.trailing_profit_retention
        if quote.bid <= protected_bid:
            return BacktestSignal("close", reason="adaptive_vault_profit_trail")
        return signal


def run_adaptive_vault_stress_directory(
    path: str | Path,
    *,
    starting_cash: float,
    event_config: EventAlphaConfig = EventAlphaConfig(),
    adaptive_config: AdaptiveVaultConfig = AdaptiveVaultConfig(),
    adverse_ticks: Iterable[int] = (0, 1, 2),
    start_date: date | None = None,
    end_date: date | None = None,
    trade_start_date: date | None = None,
) -> Tuple[AdaptiveVaultStressResult, ...]:
    results: list[AdaptiveVaultStressResult] = []
    for ticks in adverse_ticks:
        if ticks < 0:
            raise ValueError("adverse ticks cannot be negative")
        strategy = AdaptiveVaultEventAlphaStrategy(
            starting_cash=starting_cash,
            event_config=event_config,
            adaptive_config=adaptive_config,
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
                maximum_contracts=adaptive_config.maximum_contracts,
            ),
        )
        results.append(AdaptiveVaultStressResult(
            ticks, result, strategy.locked_profit,
            strategy.realized_high_watermark, strategy.last_exposure_fraction,
            strategy.losing_streak,
        ))
    return tuple(results)
