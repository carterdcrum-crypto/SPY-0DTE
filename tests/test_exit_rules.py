"""Regression checks for the 20% bid trailing stop and fixed 100% profit target."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from engine.exit_rules import profit_exit_quantity, trailing_stop_hit
from engine.paper_account import PaperAccountStore
from engine.paper_autotrader import PaperAutoSettings, QuoteRow
from engine.paper_dynamic_autotrader import risk_config_from_env
from engine.paper_dynamic_exit import DynamicExitEnsemblePaperAutoTrader
from engine.paper_exit_policy_v2 import SaferDynamicExitPolicyMixin

NOW = datetime(2026, 9, 25, 15, 0, tzinfo=timezone.utc)
SYMBOL = "SPY260925C00770000"


class QuoteReader:
    def __init__(self, quote):
        self.quote = quote

    def latest_quote(self, symbol):
        return self.quote if symbol == SYMBOL else None


class TestTrader(SaferDynamicExitPolicyMixin, DynamicExitEnsemblePaperAutoTrader):
    __test__ = False


def quote(at=NOW, bid=0.50):
    return QuoteRow(
        cycle=at, symbol=SYMBOL, expiration="2026-09-25", right="call",
        strike=770, bid=bid, ask=bid + .02, underlying_price=770,
        volume=10000, open_interest=20000, delta=.50, feed_delay_seconds=0.0,
    )


def trader_for(account, reader):
    return TestTrader(
        mode_getter=lambda: "PAPER", market_reader=reader,
        account_store=account, risk_config=risk_config_from_env(),
        settings=PaperAutoSettings(
            take_profit_fraction=1.0, stop_loss_fraction=.35,
            max_hold_seconds=1800, fee_per_contract=0.0,
        ),
    )


def test_exact_thresholds_and_majority_rounding():
    assert trailing_stop_hit(80, 100)
    assert not trailing_stop_hit(80.01, 100)
    assert not trailing_stop_hit(0, 0)
    assert [(n, profit_exit_quantity(n, n)) for n in range(1, 7)] == [
        (1, 1), (2, 2), (3, 2), (4, 3), (5, 4), (6, 5)
    ]
    assert profit_exit_quantity(3, 1, already_sold=2) == 0
    assert profit_exit_quantity(3, 2, already_sold=1) == 1


def test_legacy_env_50_percent_cannot_override_fixed_target(monkeypatch):
    monkeypatch.setenv("PAPER_TAKE_PROFIT_FRACTION", ".50")
    assert PaperAutoSettings.from_env().take_profit_fraction == 1.0


def test_scale_out_100_percent_then_restart_and_trail_runner(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPER_DYNAMIC_EXIT_MIN_HOLD_SECONDS", "600")
    account = PaperAccountStore(tmp_path / "paper.sqlite", default_starting_cash=1000.0)
    reader = QuoteReader(quote())
    position = account.open_long(
        symbol=SYMBOL, quantity=3, fill_price=50.0,
        opened_at=NOW-timedelta(seconds=60), entry_spot=770,
        strategy="test", stop_price=32.5, target_price=75.0,
        max_hold_seconds=1800, reason="test",
    )
    trader = trader_for(account, reader)

    # Migrates the live position's obsolete +50% target; +75% gain holds.
    reader.quote = quote(bid=.875)
    first = trader._manage_position(position, NOW, "PAPER")
    assert first["state"] == "POSITION_OPEN"
    assert account.position(SYMBOL)["target_price"] == pytest.approx(100)
    assert account.position(SYMBOL)["peak_exit_value"] == pytest.approx(87.5)

    reader.quote = quote(NOW+timedelta(seconds=1), bid=1.01)
    partial = trader._manage_position(account.position(SYMBOL), NOW+timedelta(seconds=1), "PAPER")
    assert partial["state"] == "POSITION_REDUCED"
    assert partial["reason"] == "take_profit"
    assert partial["closed_trade"]["quantity"] == 2
    assert account.position(SYMBOL)["profit_sold"] == 2
    assert account.position(SYMBOL)["quantity"] == 1

    reader.quote = quote(NOW+timedelta(seconds=2), bid=1.20)
    hold = trader._manage_position(account.position(SYMBOL), NOW+timedelta(seconds=2), "PAPER")
    assert hold["state"] == "POSITION_OPEN"
    assert hold["position"]["peak_exit_value"] == pytest.approx(120)

    # Restart reloads the high water mark and profit_sold from SQLite.
    account = PaperAccountStore(tmp_path / "paper.sqlite", default_starting_cash=1000.0)
    trader = trader_for(account, reader)
    reader.quote = quote(NOW+timedelta(seconds=3), bid=.96)
    end = trader._manage_position(account.position(SYMBOL), NOW+timedelta(seconds=3), "PAPER")
    assert end["reason"] == "trailing_stop"
    assert end["state"] == "POSITION_CLOSED"
    assert end["closed_trade"]["quantity"] == 1
    assert account.position(SYMBOL) is None
    assert account.snapshot().sell_count == 2


def test_trail_20_percent_from_entry_before_target(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPER_DYNAMIC_EXIT_MIN_HOLD_SECONDS", "600")
    account = PaperAccountStore(tmp_path / "account.sqlite", default_starting_cash=500)
    opened = account.open_long(
        symbol=SYMBOL, quantity=1, fill_price=50,
        opened_at=NOW, entry_spot=770, strategy="test",
        stop_price=32.5, target_price=100, max_hold_seconds=1800,
        reason="test",
    )
    reader = QuoteReader(quote(bid=.40))
    output = trader_for(account, reader)._manage_position(opened, NOW, "PAPER")
    assert output["reason"] == "trailing_stop"
    assert output["closed_trade"]["quantity"] == 1
