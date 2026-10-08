from __future__ import annotations

from datetime import datetime, timedelta, timezone

from engine.backtest import BacktestContext
from engine.data import HistoricalFrame
from engine.event_alpha import EventAlphaConfig, EventAlphaStrategy, _select_call
from engine.market import MarketSnapshot, OptionQuote


def _option(symbol: str, delta: float, bid: float = 0.95, ask: float = 1.00):
    return OptionQuote(
        symbol=symbol,
        right="call",
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


def _frame(minute: int, spot: float, options=()):
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
            data_age_seconds=0.1,
        ),
        options=tuple(options),
    )


def test_event_alpha_contract_selector_uses_affordable_near_target_delta():
    frame = _frame(
        0,
        700.0,
        (
            _option("CALL35", 0.35),
            _option("CALL45", 0.45),
            _option("WIDE", 0.45, bid=0.50, ask=1.00),
        ),
    )
    chosen = _select_call(
        frame,
        settled_cash=150.0,
        config=EventAlphaConfig(),
    )
    assert chosen is not None
    assert chosen.symbol == "CALL45"


def test_event_alpha_requires_prior_day_threshold_history():
    config = EventAlphaConfig(
        threshold_days=2,
        momentum_lookback=2,
        trend_lookback=3,
        breakout_lookback=3,
        hold_minutes=2,
        minimum_minutes_to_close=5,
    )
    strategy = EventAlphaStrategy(config)
    context = BacktestContext(
        settled_cash=1000.0,
        unsettled_cash=0.0,
        equity=1000.0,
        position=None,
    )

    signals = []
    for minute, spot in enumerate((700.0, 700.1, 700.2, 700.5, 700.9, 701.4)):
        signals.append(strategy.decide(_frame(minute, spot, (_option("CALL45", 0.45),)), context))

    assert all(signal.action == "hold" for signal in signals)
