"""Research only: invert the bullish breakout CALL purchases into PUTs.

This preserves the SPY signal timing and, where available, uses the same
strike/expiration for its put counterpart. The account is independently
re-simulated with actual next-frame option bid/ask quotes, so due to sizing
and differing exits it may not execute precisely the same trades.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable

from .adaptive_vault import AdaptiveVaultConfig, AdaptiveVaultEventAlphaStrategy
from .backtest import BacktestConfig, BacktestResult, BacktestSignal, run_backtest_stream
from .burst_research import iter_research_directory
from .event_alpha import EventAlphaConfig
from .market import OptionQuote


@dataclass(frozen=True)
class InversionBacktest:
    adverse_ticks_per_side: int
    original: BacktestResult
    inverted: BacktestResult
    inverse_entries_without_put: int


def _matched_put(call: OptionQuote, options: tuple[OptionQuote, ...]) -> OptionQuote | None:
    # Do not invent a synthetic inverse return. Put premiums, volatility, and
    # executable spreads are independently quoted by the historical market.
    if call.right != "call":
        return None
    matching = [
        item for item in options
        if item.right == "put"
        and item.strike == call.strike
        and abs(item.minutes_to_expiry - call.minutes_to_expiry) < 1.01
    ]
    return min(matching, key=lambda q: (abs(abs(q.delta)-abs(call.delta)), q.spread_fraction)) if matching else None


class InvertedAdaptiveVaultStrategy(AdaptiveVaultEventAlphaStrategy):
    """Same detected bullish breakouts but buys matching puts, not calls."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._current_options: tuple[OptionQuote, ...] = ()
        self.inverse_entries_without_put = 0

    def decide(self, frame, context):
        self._current_options = frame.options
        return super().decide(frame, context)

    def _entry_signal(self, call: OptionQuote, budget: float) -> BacktestSignal:
        put = _matched_put(call, self._current_options)
        if (
            put is None
            or put.bid <= 0
            or put.ask <= put.bid
            or put.spread_fraction > self.config.max_spread_fraction
            or put.minutes_to_expiry < self.config.minimum_minutes_to_close
            or put.ask * 100 > budget + 1e-12
        ):
            self.inverse_entries_without_put += 1
            return BacktestSignal("hold")
        proposed = super()._entry_signal(put, budget)
        if proposed.action != "open":
            self.inverse_entries_without_put += 1
        return proposed


def run_inverted_adaptive_comparison(
    path: str | Path,
    *,
    starting_cash: float,
    event_config: EventAlphaConfig = EventAlphaConfig(),
    adaptive_config: AdaptiveVaultConfig = AdaptiveVaultConfig(),
    adverse_ticks: Iterable[int] = (0, 1, 2),
    start_date: date | None = None,
    end_date: date | None = None,
    trade_start_date: date | None = None,
) -> tuple[InversionBacktest, ...]:
    results: list[InversionBacktest] = []
    for ticks in adverse_ticks:
        original_strategy = AdaptiveVaultEventAlphaStrategy(
            starting_cash=starting_cash, event_config=event_config,
            adaptive_config=adaptive_config, trade_start_date=trade_start_date,
        )
        inverse_strategy = InvertedAdaptiveVaultStrategy(
            starting_cash=starting_cash, event_config=event_config,
            adaptive_config=adaptive_config, trade_start_date=trade_start_date,
        )
        cfg = BacktestConfig(
            starting_cash=starting_cash, fee_per_contract=0.0,
            slippage_spread_fraction=0.0, adverse_ticks_per_side=ticks,
            option_tick_size=0.01, maximum_contracts=adaptive_config.maximum_contracts,
        )
        original = run_backtest_stream(
            iter_research_directory(path, start_date=start_date, end_date=end_date),
            original_strategy, config=cfg,
        )
        inverted = run_backtest_stream(
            iter_research_directory(path, start_date=start_date, end_date=end_date),
            inverse_strategy, config=cfg,
        )
        results.append(InversionBacktest(
            adverse_ticks_per_side=ticks, original=original, inverted=inverted,
            inverse_entries_without_put=inverse_strategy.inverse_entries_without_put,
        ))
    return tuple(results)
