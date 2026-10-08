"""Research-only fast-compounding Event Alpha with a realized-profit reserve.

This does not buy a long options straddle: a straddle costs two premiums and
cannot guarantee protection of previous profits. We explicitly leave a
non-trading reserve and cap each NEXT-FRAME fill to the growth sleeve.
No live trading path imports or activates this strategy.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable, Tuple

from .backtest import BacktestConfig, BacktestContext, BacktestResult, BacktestSignal, run_backtest_stream
from .burst_research import iter_research_directory
from .event_alpha import EventAlphaConfig, EventAlphaStrategy
from .market import OptionQuote


@dataclass(frozen=True)
class RocketVaultConfig:
    """Frozen research parameters; tune only on development data.

    Start by deploying most eligible settled cash (growth sleeve). Once the
    account has doubled, reserve a rising share of realized gains. Entries
    spend only non-reserved equity. Long option risk is capped at paid premium,
    but executions / outages / external account activity remain uncertain.
    """
    activation_multiple: float = 2.0
    first_lock_fraction: float = 0.65
    max_lock_fraction: float = 0.90
    growth_exposure_fraction: float = 0.90
    maximum_contracts: int = 5

    def __post_init__(self) -> None:
        if not math.isfinite(self.activation_multiple) or self.activation_multiple <= 1.0:
            raise ValueError("activation_multiple must exceed 1")
        if not (0.0 <= self.first_lock_fraction <= self.max_lock_fraction <= 1.0):
            raise ValueError("profit lock fractions must be between 0 and 1")
        if not (0.0 < self.growth_exposure_fraction <= 1.0):
            raise ValueError("growth_exposure_fraction must be in (0, 1]")
        if self.maximum_contracts < 1:
            raise ValueError("maximum_contracts must be positive")


@dataclass(frozen=True)
class RocketVaultStressResult:
    adverse_ticks_per_side: int
    result: BacktestResult
    locked_profit: float
    realized_high_watermark: float


class RocketVaultEventAlphaStrategy(EventAlphaStrategy):
    """Event Alpha entry signal + aggressive quantity + monotone cash reserve."""

    def __init__(
        self,
        *,
        starting_cash: float,
        event_config: EventAlphaConfig = EventAlphaConfig(),
        vault_config: RocketVaultConfig = RocketVaultConfig(),
        trade_start_date: date | None = None,
    ) -> None:
        if not math.isfinite(starting_cash) or starting_cash <= 0:
            raise ValueError("starting_cash must be positive finite")
        super().__init__(event_config, trade_start_date=trade_start_date)
        self.starting_cash = starting_cash
        self.vault_config = vault_config
        self.realized_high_watermark = starting_cash
        self.locked_profit = 0.0

    def _observe_flat_equity(self, equity: float) -> None:
        if not math.isfinite(equity) or equity < 0:
            raise ValueError("realized equity must be finite nonnegative")
        self.realized_high_watermark = max(self.realized_high_watermark, equity)
        multiple = self.realized_high_watermark / self.starting_cash
        if multiple < self.vault_config.activation_multiple:
            return

        # Smoothly increase the fraction protected with additional growth:
        # 65% of gains initially, asymptotically approaching 90% of gains.
        fraction = self.vault_config.first_lock_fraction + (
            self.vault_config.max_lock_fraction - self.vault_config.first_lock_fraction
        ) * (1.0 - self.vault_config.activation_multiple / multiple)
        locked = max(
            self.locked_profit,
            (self.realized_high_watermark - self.starting_cash) * fraction,
        )
        # This guard catches a broken invariant, rather than silently raiding
        # the previously ring-fenced reserve to fund another trade.
        if locked > equity + 1e-6:
            raise ValueError("realized equity breached protected reserve")
        self.locked_profit = locked

    def decide(self, frame, context: BacktestContext) -> BacktestSignal:
        # Ratchet ONLY on fully closed, realized account equity. Never lock
        # unrealized marks, which can vanish before the next bid-side fill.
        if context.position is None:
            self._observe_flat_equity(context.equity)
        return super().decide(frame, context)

    def _entry_budget(self, context: BacktestContext) -> float:
        growth_sleeve = max(0.0, context.equity - self.locked_profit)
        spendable = min(context.settled_cash, growth_sleeve)
        return max(0.0, spendable * self.vault_config.growth_exposure_fraction)

    def _entry_signal(self, option: OptionQuote, budget: float) -> BacktestSignal:
        cost_per_contract = option.ask * 100.0
        if cost_per_contract <= 0:
            return BacktestSignal("hold")
        quantity = min(
            self.vault_config.maximum_contracts,
            int(math.floor((budget + 1e-12) / cost_per_contract)),
        )
        if quantity <= 0:
            return BacktestSignal("hold")
        return BacktestSignal(
            "open",
            option.symbol,
            quantity,
            "BULL_ACCEL90_BREAKOUT_ROCKET_VAULT",
            max_total_cost=budget,
        )


def run_rocket_vault_stress_directory(
    path: str | Path,
    *,
    starting_cash: float,
    event_config: EventAlphaConfig = EventAlphaConfig(),
    vault_config: RocketVaultConfig = RocketVaultConfig(),
    adverse_ticks: Iterable[int] = (0, 1, 2),
    start_date: date | None = None,
    end_date: date | None = None,
    trade_start_date: date | None = None,
) -> Tuple[RocketVaultStressResult, ...]:
    output: list[RocketVaultStressResult] = []
    for ticks in adverse_ticks:
        if ticks < 0:
            raise ValueError("adverse ticks cannot be negative")
        strategy = RocketVaultEventAlphaStrategy(
            starting_cash=starting_cash,
            event_config=event_config,
            vault_config=vault_config,
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
                maximum_contracts=vault_config.maximum_contracts,
            ),
        )
        output.append(RocketVaultStressResult(
            adverse_ticks_per_side=ticks,
            result=result,
            locked_profit=strategy.locked_profit,
            realized_high_watermark=strategy.realized_high_watermark,
        ))
    return tuple(output)
