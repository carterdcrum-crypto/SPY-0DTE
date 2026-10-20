from __future__ import annotations

from datetime import datetime, timedelta, timezone

from engine.backtest import BacktestContext
from engine.data import HistoricalFrame
from engine.market import MarketSnapshot, OptionQuote
from engine.regime_long_option import (
    RegimeLongOptionConfig,
    RegimeLongOptionStrategy,
    _annualized_realized_vol,
    _select_contract,
)


def _option(symbol: str, right: str, delta: float, bid: float = 0.95, ask: float = 1.00):
    return OptionQuote(
        symbol=symbol,
        right=right,
        strike=700.0,
        bid=bid,
        ask=ask,
        delta=delta,
        gamma=0.05,
        theta=-1.0,
        vega=0.03,
        implied_volatility=0.20,
        volume=1000,
        open_interest=3000,
        underlying_price=700.0,
        minutes_to_expiry=120.0,
    )


def _frame(minute: int, spot: float, options=None):
    return HistoricalFrame(
        timestamp=datetime(2026, 10, 2, 14, 0, tzinfo=timezone.utc) + timedelta(minutes=minute),
        market=MarketSnapshot(
            spot=spot,
            bid=spot - 0.01,
            ask=spot + 0.01,
            realized_volatility=0.20,
            implied_volatility=0.20,
            volume_ratio=1.0,
            minutes_to_close=300.0 - minute,
            data_age_seconds=0.2,
        ),
        options=tuple(options or ()),
    )


def test_realized_volatility_is_positive_for_moving_series():
    assert _annualized_realized_vol((0.001, -0.001, 0.002, -0.002)) > 0


def test_contract_selector_respects_right_cash_spread_and_delta():
    frame = _frame(
        0,
        700.0,
        (
            _option("CALL50", "call", 0.50),
            _option("CALL55", "call", 0.55),
            _option("PUT55", "put", -0.55),
            _option("WIDE", "call", 0.55, bid=0.50, ask=1.00),
        ),
    )
    chosen = _select_contract(
        frame,
        right="call",
        settled_cash=150.0,
        config=RegimeLongOptionConfig(minimum_history=30),
    )
    assert chosen is not None
    assert chosen.symbol == "CALL55"


def test_hard_drawdown_blocks_new_entries():
    config = RegimeLongOptionConfig(
        fast_lookback=2,
        slow_lookback=3,
        rv_lookback=3,
        breakout_lookback=3,
        minimum_history=3,
        trend_threshold=0.0001,
        volatility_edge_ratio=0.01,
        hard_drawdown=0.05,
        earliest_entry_minutes_to_close=350,
        latest_entry_minutes_to_close=30,
    )
    strategy = RegimeLongOptionStrategy(config)

    for i, spot in enumerate((700.0, 700.5, 701.0, 701.5)):
        strategy.decide(
            _frame(i, spot, (_option("CALL55", "call", 0.55),)),
            BacktestContext(
                settled_cash=1000.0,
                unsettled_cash=0.0,
                equity=1000.0,
                position=None,
            ),
        )

    blocked = strategy.decide(
        _frame(5, 702.0, (_option("CALL55", "call", 0.55),)),
        BacktestContext(
            settled_cash=900.0,
            unsettled_cash=0.0,
            equity=900.0,
            position=None,
        ),
    )
    assert blocked.action == "hold"
