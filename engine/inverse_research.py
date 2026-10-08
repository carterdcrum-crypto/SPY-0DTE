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


@dataclass(frozen=True)
class ExactTradeMirror:
    """Retrospective same-contract-strike, same-entry/exit-time PUT replay.

    This keeps the original trade schedule and number of contracts; a
    *hypothetical* counterfactual, not a causal strategy. Execution uses
    actual PUT ask/bid at the original timestamps. It obeys settled cash
    and skips unfinanceable trades instead of borrowing. Incomplete put
    quotes are counted, never inferred from call option P&L.
    """
    starting_cash: float
    ending_cash_equity: float
    original_trades: int
    mirrored_trades: int
    missing_put_quote_pairs: int
    unaffordable_put_trades: int


def replay_original_trades_as_puts(
    path: str | Path,
    original: BacktestResult,
    *,
    starting_cash: float,
    adverse_ticks_per_side: int,
    start_date: date | None = None,
    end_date: date | None = None,
) -> ExactTradeMirror:
    from .backtest import _next_business_day

    if adverse_ticks_per_side < 0:
        raise ValueError("adverse ticks cannot be negative")

    wanted = {
        timestamp
        for trade in original.trades
        for timestamp in (trade.entry_timestamp, trade.exit_timestamp)
    }
    by_timestamp = {}
    for frame in iter_research_directory(path, start_date=start_date, end_date=end_date):
        if frame.timestamp in wanted:
            by_timestamp[frame.timestamp] = frame

    settled = float(starting_cash)
    unsettled: list[tuple[date,float]] = []
    completed = missing = unaffordable = 0
    for trade in sorted(original.trades, key=lambda t: t.entry_timestamp):
        matured = sum(value for dt, value in unsettled if dt <= trade.entry_timestamp.date())
        settled += matured
        unsettled = [(dt,value) for dt,value in unsettled if dt > trade.entry_timestamp.date()]

        entry_frame = by_timestamp.get(trade.entry_timestamp)
        exit_frame = by_timestamp.get(trade.exit_timestamp)
        if entry_frame is None or exit_frame is None:
            missing += 1
            continue
        call_quote = entry_frame.option(trade.option_symbol)
        if call_quote is None:
            missing += 1
            continue
        entry_put = _matched_put(call_quote, entry_frame.options)
        exit_put = exit_frame.option(entry_put.symbol) if entry_put is not None else None
        if (
            entry_put is None or exit_put is None
            or entry_put.ask <= entry_put.bid or entry_put.ask <= 0
            or exit_put.bid <= 0
        ):
            missing += 1
            continue

        buy_price = entry_put.ask + adverse_ticks_per_side * 0.01
        cost = buy_price * 100 * trade.quantity
        if cost > settled + 1e-12:
            unaffordable += 1
            continue
        settled -= cost
        sale_price = max(0.0, exit_put.bid - adverse_ticks_per_side * 0.01)
        proceeds = sale_price * 100 * trade.quantity
        unsettled.append((_next_business_day(trade.exit_timestamp.date()), proceeds))
        completed += 1

    final_equity = settled + sum(value for _, value in unsettled)
    return ExactTradeMirror(
        starting_cash=starting_cash,
        ending_cash_equity=final_equity,
        original_trades=len(original.trades),
        mirrored_trades=completed,
        missing_put_quote_pairs=missing,
        unaffordable_put_trades=unaffordable,
    )
