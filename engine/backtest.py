from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal, Protocol, Sequence, Tuple

from .data import HistoricalFrame
from .metrics import EquityPoint, PerformanceMetrics, performance_metrics


Action = Literal["hold", "open", "close"]


@dataclass(frozen=True)
class BacktestSignal:
    action: Action
    option_symbol: str | None = None
    quantity: int = 1
    reason: str = ""


@dataclass(frozen=True)
class PositionView:
    option_symbol: str
    quantity: int
    entry_price: float
    entry_cost: float
    entry_timestamp: datetime


@dataclass(frozen=True)
class BacktestContext:
    settled_cash: float
    unsettled_cash: float
    equity: float
    position: PositionView | None


class BacktestStrategy(Protocol):
    def decide(self, frame: HistoricalFrame, context: BacktestContext) -> BacktestSignal: ...


@dataclass(frozen=True)
class BacktestConfig:
    starting_cash: float = 10_000.0
    fee_per_contract: float = 0.65
    slippage_spread_fraction: float = 0.25
    liquidate_at_end: bool = True
    maximum_contracts: int = 100


@dataclass(frozen=True)
class BacktestTrade:
    option_symbol: str
    quantity: int
    entry_timestamp: datetime
    exit_timestamp: datetime
    entry_price: float
    exit_price: float
    entry_cost: float
    exit_value: float
    pnl: float
    return_on_cost: float
    entry_reason: str
    exit_reason: str


@dataclass(frozen=True)
class BacktestResult:
    trades: Tuple[BacktestTrade, ...]
    equity_curve: Tuple[EquityPoint, ...]
    metrics: PerformanceMetrics


@dataclass
class _Position:
    option_symbol: str
    quantity: int
    entry_price: float
    entry_cost: float
    entry_timestamp: datetime
    entry_reason: str


@dataclass
class _Pending:
    signal: BacktestSignal


def _next_business_day(day: date) -> date:
    nxt = day + timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    return nxt


def run_backtest(
    frames: Sequence[HistoricalFrame],
    strategy: BacktestStrategy,
    *,
    config: BacktestConfig = BacktestConfig(),
) -> BacktestResult:
    """Event-driven long-option cash-account backtest.

    Decisions use frame T and can execute only on frame T+1. This is the key
    anti-lookahead invariant. BUY fills cross the next ask plus modeled slippage;
    SELL fills cross the next bid minus modeled slippage. Sale proceeds settle
    on the next business day and cannot be reused before then.
    """

    if config.starting_cash <= 0:
        raise ValueError("starting_cash must be positive")
    if config.maximum_contracts < 1:
        raise ValueError("maximum_contracts must be positive")

    ordered = tuple(sorted(frames, key=lambda f: f.timestamp))
    if any(a.timestamp == b.timestamp for a, b in zip(ordered, ordered[1:])):
        raise ValueError("backtest frames must have unique timestamps")

    settled_cash = float(config.starting_cash)
    unsettled: list[tuple[date, float]] = []
    position: _Position | None = None
    pending: _Pending | None = None
    trades: list[BacktestTrade] = []
    curve: list[EquityPoint] = []

    def release_settled(as_of: date) -> None:
        nonlocal settled_cash, unsettled
        matured = sum(amount for settle_day, amount in unsettled if settle_day <= as_of)
        settled_cash += matured
        unsettled = [(d, a) for d, a in unsettled if d > as_of]

    def quote_for(frame: HistoricalFrame, symbol: str):
        return frame.option(symbol)

    def mark_equity(frame: HistoricalFrame) -> float:
        value = settled_cash + sum(amount for _, amount in unsettled)
        if position is not None:
            quote = quote_for(frame, position.option_symbol)
            if quote is not None:
                liquidation = max(
                    0.0,
                    quote.bid - quote.spread * max(0.0, config.slippage_spread_fraction),
                )
                value += max(
                    0.0,
                    liquidation * 100.0 * position.quantity
                    - config.fee_per_contract * position.quantity,
                )
        return value

    for frame in ordered:
        release_settled(frame.timestamp.date())

        # Execute only orders generated from a previous frame.
        if pending is not None:
            signal = pending.signal
            pending = None

            if signal.action == "open" and position is None and signal.option_symbol:
                quote = quote_for(frame, signal.option_symbol)
                if quote is not None:
                    quantity = min(max(0, signal.quantity), config.maximum_contracts)
                    fill = quote.ask + quote.spread * max(0.0, config.slippage_spread_fraction)
                    total_cost = fill * 100.0 * quantity + config.fee_per_contract * quantity
                    if quantity > 0 and total_cost <= settled_cash + 1e-12:
                        settled_cash -= total_cost
                        position = _Position(
                            option_symbol=signal.option_symbol,
                            quantity=quantity,
                            entry_price=fill,
                            entry_cost=total_cost,
                            entry_timestamp=frame.timestamp,
                            entry_reason=signal.reason,
                        )

            elif signal.action == "close" and position is not None:
                quote = quote_for(frame, position.option_symbol)
                if quote is not None:
                    fill = max(
                        0.0,
                        quote.bid - quote.spread * max(0.0, config.slippage_spread_fraction),
                    )
                    exit_value = max(
                        0.0,
                        fill * 100.0 * position.quantity
                        - config.fee_per_contract * position.quantity,
                    )
                    pnl = exit_value - position.entry_cost
                    ret = pnl / position.entry_cost if position.entry_cost > 0 else 0.0
                    trades.append(
                        BacktestTrade(
                            option_symbol=position.option_symbol,
                            quantity=position.quantity,
                            entry_timestamp=position.entry_timestamp,
                            exit_timestamp=frame.timestamp,
                            entry_price=position.entry_price,
                            exit_price=fill,
                            entry_cost=position.entry_cost,
                            exit_value=exit_value,
                            pnl=pnl,
                            return_on_cost=ret,
                            entry_reason=position.entry_reason,
                            exit_reason=signal.reason,
                        )
                    )
                    unsettled.append((_next_business_day(frame.timestamp.date()), exit_value))
                    position = None

        equity = mark_equity(frame)
        curve.append(EquityPoint(frame.timestamp, equity))

        view = None
        if position is not None:
            view = PositionView(
                option_symbol=position.option_symbol,
                quantity=position.quantity,
                entry_price=position.entry_price,
                entry_cost=position.entry_cost,
                entry_timestamp=position.entry_timestamp,
            )

        signal = strategy.decide(
            frame,
            BacktestContext(
                settled_cash=settled_cash,
                unsettled_cash=sum(amount for _, amount in unsettled),
                equity=equity,
                position=view,
            ),
        )

        if signal.action == "open" and position is None and signal.option_symbol:
            pending = _Pending(signal)
        elif signal.action == "close" and position is not None:
            pending = _Pending(signal)

    if ordered and position is not None and config.liquidate_at_end:
        frame = ordered[-1]
        quote = quote_for(frame, position.option_symbol)
        if quote is not None:
            fill = max(
                0.0,
                quote.bid - quote.spread * max(0.0, config.slippage_spread_fraction),
            )
            exit_value = max(
                0.0,
                fill * 100.0 * position.quantity
                - config.fee_per_contract * position.quantity,
            )
            pnl = exit_value - position.entry_cost
            ret = pnl / position.entry_cost if position.entry_cost > 0 else 0.0
            trades.append(
                BacktestTrade(
                    option_symbol=position.option_symbol,
                    quantity=position.quantity,
                    entry_timestamp=position.entry_timestamp,
                    exit_timestamp=frame.timestamp,
                    entry_price=position.entry_price,
                    exit_price=fill,
                    entry_cost=position.entry_cost,
                    exit_value=exit_value,
                    pnl=pnl,
                    return_on_cost=ret,
                    entry_reason=position.entry_reason,
                    exit_reason="forced_end_of_backtest",
                )
            )
            unsettled.append((_next_business_day(frame.timestamp.date()), exit_value))
            position = None
            final_equity = settled_cash + sum(amount for _, amount in unsettled)
            curve[-1] = EquityPoint(frame.timestamp, final_equity)

    metrics = performance_metrics(
        starting_equity=config.starting_cash,
        equity_curve=curve,
        trade_returns=[t.return_on_cost for t in trades],
    )
    return BacktestResult(tuple(trades), tuple(curve), metrics)
