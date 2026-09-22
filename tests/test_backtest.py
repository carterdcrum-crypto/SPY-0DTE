from __future__ import annotations

from datetime import datetime, timezone
from io import StringIO

import pytest

from engine.backtest import BacktestConfig, BacktestSignal, run_backtest
from engine.data import HistoricalFrame, load_canonical_csv
from engine.market import MarketSnapshot, OptionQuote


CSV = """timestamp,spot,spy_bid,spy_ask,realized_volatility,market_implied_volatility,volume_ratio,minutes_to_close,data_age_seconds,ood_score,option_symbol,right,strike,option_bid,option_ask,delta,gamma,theta,vega,option_implied_volatility,volume,open_interest,underlying_price,minutes_to_expiry
2026-09-21T15:00:00Z,670,669.99,670.01,0.15,0.18,1.2,90,0.1,0.1,SPY260921C00670000,call,670,1.00,1.10,0.52,0.08,-1.2,0.04,0.20,1000,4000,670,90
2026-09-21T15:00:00Z,670,669.99,670.01,0.15,0.18,1.2,90,0.1,0.1,SPY260921P00670000,put,670,0.95,1.05,-0.48,0.08,-1.1,0.04,0.21,900,3800,670,90
"""


def q(symbol: str, bid: float, ask: float) -> OptionQuote:
    return OptionQuote(
        symbol=symbol,
        right="call",
        strike=670,
        bid=bid,
        ask=ask,
        delta=.5,
        gamma=.08,
        theta=-1,
        vega=.04,
        implied_volatility=.2,
        volume=1000,
        open_interest=4000,
        underlying_price=670,
        minutes_to_expiry=60,
    )


def frame(minute: int, bid: float, ask: float) -> HistoricalFrame:
    return HistoricalFrame(
        timestamp=datetime(2026, 9, 21, 15, minute, tzinfo=timezone.utc),
        market=MarketSnapshot(670, 669.99, 670.01, .15, .18, 1.0, 60-minute),
        options=(q("SPY-C", bid, ask),),
    )


def test_canonical_loader_groups_options_by_timestamp():
    frames = load_canonical_csv(StringIO(CSV))
    assert len(frames) == 1
    assert frames[0].timestamp.tzinfo is not None
    assert len(frames[0].options) == 2
    assert frames[0].market.spot == 670


def test_loader_rejects_naive_timestamps():
    broken = CSV.replace("2026-09-21T15:00:00Z", "2026-09-21T15:00:00")
    with pytest.raises(ValueError):
        load_canonical_csv(StringIO(broken))


class OpenThenClose:
    def __init__(self):
        self.calls = 0

    def decide(self, frame, context):
        self.calls += 1
        if self.calls == 1:
            return BacktestSignal("open", "SPY-C", 1, "signal-at-t0")
        if context.position is not None:
            return BacktestSignal("close", reason="exit")
        return BacktestSignal("hold")


def test_signal_executes_on_next_frame_not_signal_frame():
    frames = (
        frame(0, .90, 1.00),
        frame(1, 1.90, 2.00),
        frame(2, 2.40, 2.50),
    )
    result = run_backtest(
        frames,
        OpenThenClose(),
        config=BacktestConfig(starting_cash=1000, slippage_spread_fraction=0.0),
    )
    assert len(result.trades) == 1
    assert result.trades[0].entry_price == 2.00
    assert result.trades[0].exit_price == 2.40
    assert result.trades[0].entry_timestamp == frames[1].timestamp
    assert result.trades[0].exit_timestamp == frames[2].timestamp


class ReenterImmediately:
    def decide(self, frame, context):
        if context.position is None:
            return BacktestSignal("open", "SPY-C", 1, "open")
        return BacktestSignal("close", reason="close")


def test_cash_sale_proceeds_are_not_reused_same_day():
    frames = (
        frame(0, .90, 1.00),
        frame(1, .90, 1.00),
        frame(2, 1.90, 2.00),
        frame(3, 1.90, 2.00),
        frame(4, .90, 1.00),
        frame(5, .90, 1.00),
    )
    result = run_backtest(
        frames,
        ReenterImmediately(),
        config=BacktestConfig(starting_cash=110, slippage_spread_fraction=0.0),
    )
    assert len(result.trades) == 1
