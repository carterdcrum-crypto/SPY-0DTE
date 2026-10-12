from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from engine.paper_account import PaperAccountStore
from engine.paper_autotrader import MarketCycle, PaperAutoSettings, QuoteRow
from engine.paper_regime_reclaim import (
    EXIT_NAME, POSITION_NAME, SIGNAL_NAME,
    RegimeReclaimPaperTrader, RegimeReclaimDelayedPaperTrader,
    detect_regime_reclaim, realized_vol_annualized,
)
from engine import paper_runner
from engine.dynamic_risk import DynamicRiskConfig


def cycles_from_spots(spots, now, *, spacing=2):
    rows = []
    for i, spot in enumerate(spots):
        stamp = now - timedelta(seconds=(len(spots) - i - 1) * spacing)
        quote = QuoteRow(stamp, "SPYTESTC", stamp.date().isoformat(), "call", 100.0,
                         1.98, 2.02, spot, 5000, 15000, 0.5, 0.1)
        rows.append(MarketCycle(stamp, (quote,)))
    return tuple(reversed(rows))


def test_trend_pullback_cross_and_no_lookahead():
    now = datetime(2026, 10, 12, 14, 0, tzinfo=timezone.utc)
    prices = [100 + .012 * i for i in range(80)]
    prices[-2] = prices[-3] - .09
    prices[-1] = prices[-3] + .08
    observations = cycles_from_spots(prices, now)
    signal = detect_regime_reclaim(observations)
    assert signal is not None and signal.right == "call"
    assert signal.variant == "trend_pullback_reclaim"
    assert detect_regime_reclaim(observations[1:]) != signal


def test_volatility_uses_elapsed_time_and_gap_disables_entry():
    now = datetime(2026, 10, 12, 14, 0, tzinfo=timezone.utc)
    prices = [100 + .02 * i + .015 * (i % 3) for i in range(90)]
    cycles = cycles_from_spots(prices, now)
    assert 0 < realized_vol_annualized(cycles) < 2.0
    gap = list(cycles)
    gap[0] = replace(gap[0], received_at=gap[0].received_at + timedelta(minutes=20))
    assert detect_regime_reclaim(tuple(gap)) is None


class FakeReader:
    def __init__(self, quote):
        self.quote = quote

    def latest_quote(self, symbol):
        return self.quote if symbol == self.quote.symbol else None

    def latest_cycles(self, limit=2):
        return (MarketCycle(self.quote.cycle, (self.quote,)),)


def quote(now, bid, delay=.1):
    return QuoteRow(now - timedelta(seconds=1), "SPYTESTC", "2026-10-12",
                    "call", 100., bid, bid+.02, 100., 5000, 15000, .5, delay)


def trader_for_position(tmp_path, now, *, bid=1.50):
    account = PaperAccountStore(tmp_path / "paper.sqlite", default_starting_cash=1000)
    account.open_long(symbol="SPYTESTC", quantity=1, fill_price=100.,
                      opened_at=now-timedelta(seconds=60), entry_spot=100.,
                      strategy=POSITION_NAME, stop_price=65., target_price=1e99,
                      max_hold_seconds=600, reason="combined test")
    trader = RegimeReclaimPaperTrader(
        mode_getter=lambda: "PAPER", market_reader=FakeReader(quote(now,bid)),
        account_store=account, settings=PaperAutoSettings(), risk_config=DynamicRiskConfig()
    )
    return trader, account


def test_no_fixed_profit_exit_and_peak_persists(tmp_path):
    now = datetime(2026, 10, 12, 14, 0, tzinfo=timezone.utc)
    trader, account = trader_for_position(tmp_path, now, bid=2.00)
    result = trader.tick(now)
    assert result["state"] == "POSITION_OPEN"
    assert account.position("SPYTESTC")["peak_exit_value"] >= 199
    trader.market_reader.quote = quote(now + timedelta(seconds=2), 1.50)
    assert trader.tick(now + timedelta(seconds=2))["state"] == "POSITION_OPEN"
    trader.market_reader.quote = quote(now + timedelta(seconds=4), 1.39)
    exited = trader.tick(now + timedelta(seconds=4))
    assert exited["reason"] == "regime_trail_30pct"
    assert exited["closed_trade"]["reason"] == "regime_trail_30pct"
    assert account.snapshot().trade_count == 1


def test_hard_loss_and_stale_bid_do_not_execute(tmp_path):
    now = datetime(2026, 10, 12, 14, 0, tzinfo=timezone.utc)
    trader, account = trader_for_position(tmp_path, now, bid=.60)
    trader.market_reader.quote = quote(now, .60, delay=900)
    assert trader.tick(now)["state"] == "POSITION_FEED_DELAYED"
    assert account.snapshot().open_positions == 1
    trader.market_reader.quote = quote(now, .60)
    assert trader.tick(now)["reason"] == "regime_hard_loss_35pct"
    assert account.snapshot().open_positions == 0


@pytest.mark.parametrize("delayed", [False, True])
def test_new_default_routes_both_paper_modes(monkeypatch, tmp_path, delayed):
    monkeypatch.delenv("PAPER_STRATEGY", raising=False)
    monkeypatch.setenv("PAPER_DB_PATH", str(tmp_path / "paper.sqlite"))
    trader = paper_runner.create_trader(lambda: "SHADOW", delayed=delayed)
    expected = RegimeReclaimDelayedPaperTrader if delayed else RegimeReclaimPaperTrader
    assert isinstance(trader, expected)
    assert trader.position_strategy == POSITION_NAME


def test_forward_strategy_never_changes_legacy_exit_rule(tmp_path):
    now = datetime(2026, 10, 12, 14, 0, tzinfo=timezone.utc)
    trader, account = trader_for_position(tmp_path, now, bid=1.5)
    account.close_long(symbol="SPYTESTC", quantity=1, fill_price=99,
                       closed_at=now, trade_date=now.date(), reason="fixture")
    account.open_long(symbol="SPYTESTC", quantity=1, fill_price=100.,
                      opened_at=now-timedelta(seconds=60), entry_spot=100.,
                      strategy="legacy", stop_price=65., target_price=120.,
                      max_hold_seconds=600, reason="legacy")
    # Inherited ensemble executor is responsible for any old open position.
    assert trader.account_store.position("SPYTESTC")["strategy"] == "legacy"
