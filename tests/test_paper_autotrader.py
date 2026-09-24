from __future__ import annotations

from datetime import datetime, timedelta, timezone

from engine.paper_account import PaperAccountStore
from engine.paper_autotrader import (
    MarketCycle,
    PaperAutoSettings,
    PaperAutoTrader,
    QuoteRow,
)


class FakeReader:
    def __init__(self, cycles: tuple[MarketCycle, ...], latest_quote: QuoteRow):
        self.cycles = cycles
        self.quote = latest_quote

    def latest_cycles(self, limit: int = 2):
        return self.cycles[:limit]

    def latest_quote(self, symbol: str):
        return self.quote if self.quote.symbol == symbol else None


def _quote(*, cycle: datetime, spot: float, bid: float, ask: float) -> QuoteRow:
    return QuoteRow(
        cycle=cycle,
        symbol="SPY260924C00671000",
        expiration="2026-09-24",
        right="call",
        strike=671.0,
        bid=bid,
        ask=ask,
        underlying_price=spot,
        volume=1000,
        open_interest=5000,
        delta=0.35,
        feed_delay_seconds=0.0,
    )


def _settings() -> PaperAutoSettings:
    return PaperAutoSettings(
        tick_seconds=1.0,
        max_data_age_seconds=15.0,
        minimum_spot_move_fraction=0.0002,
        minimum_premium_move_fraction=0.005,
        minimum_edge_proxy=0.0,
        minimum_liquidity_score=0.40,
        maximum_spread_fraction=0.15,
        maximum_moneyness_fraction=0.03,
        maximum_position_fraction=1.0,
        maximum_contracts=1,
        stop_loss_fraction=0.35,
        take_profit_fraction=0.50,
        max_hold_seconds=600,
        cooldown_seconds=0,
        force_exit_minutes_before_close=10.0,
        fee_per_contract=0.65,
    )


def test_paper_mode_opens_and_closes_position(tmp_path):
    now = datetime(2026, 9, 24, 15, 0, tzinfo=timezone.utc)
    previous = _quote(cycle=now - timedelta(seconds=2), spot=670.0, bid=0.49, ask=0.51)
    latest = _quote(cycle=now - timedelta(seconds=1), spot=671.0, bid=0.73, ask=0.75)
    reader = FakeReader(
        cycles=(MarketCycle(latest.cycle, (latest,)), MarketCycle(previous.cycle, (previous,))),
        latest_quote=latest,
    )
    account = PaperAccountStore(tmp_path / "paper.sqlite", default_starting_cash=115.0)
    trader = PaperAutoTrader(
        mode_getter=lambda: "PAPER",
        market_reader=reader,
        account_store=account,
        settings=_settings(),
    )

    opened = trader.tick(now)
    assert opened["state"] == "POSITION_OPENED"
    snapshot = account.snapshot()
    assert snapshot.open_positions == 1
    assert snapshot.trade_count == 1
    assert snapshot.settled_cash < 115.0

    winning = _quote(cycle=now + timedelta(seconds=2), spot=672.0, bid=1.25, ask=1.27)
    reader.quote = winning
    closed = trader.tick(now + timedelta(seconds=3))
    assert closed["state"] == "POSITION_CLOSED"
    assert closed["reason"] == "take_profit"

    snapshot = account.snapshot()
    assert snapshot.open_positions == 0
    assert snapshot.trade_count == 2
    assert snapshot.realized_pnl > 0
    assert snapshot.unsettled_cash > 0


def test_shadow_mode_records_signal_without_position(tmp_path):
    now = datetime(2026, 9, 24, 15, 0, tzinfo=timezone.utc)
    previous = _quote(cycle=now - timedelta(seconds=2), spot=670.0, bid=0.49, ask=0.51)
    latest = _quote(cycle=now - timedelta(seconds=1), spot=671.0, bid=0.73, ask=0.75)
    reader = FakeReader(
        cycles=(MarketCycle(latest.cycle, (latest,)), MarketCycle(previous.cycle, (previous,))),
        latest_quote=latest,
    )
    account = PaperAccountStore(tmp_path / "paper.sqlite", default_starting_cash=115.0)
    trader = PaperAutoTrader(
        mode_getter=lambda: "SHADOW",
        market_reader=reader,
        account_store=account,
        settings=_settings(),
    )

    result = trader.tick(now)
    assert result["state"] == "SHADOW_SIGNAL"
    assert result["last_action"] == "WOULD_BUY"
    snapshot = account.snapshot()
    assert snapshot.open_positions == 0
    assert snapshot.trade_count == 0
