from __future__ import annotations

from datetime import datetime, timedelta, timezone

from engine.paper_account import PaperAccountStore
from engine.paper_autotrader import MarketCycle, PaperAutoSettings, QuoteRow
from engine.paper_dynamic_autotrader import risk_config_from_env
from engine.paper_dynamic_exit import (
    DynamicExitEnsemblePaperAutoTrader,
    DynamicExitMetrics,
    classify_dynamic_exit,
)


class FakeReader:
    def __init__(self, quote: QuoteRow):
        self.quote = quote

    def latest_quote(self, symbol: str):
        return self.quote if symbol == self.quote.symbol else None

    def latest_cycles(self, limit: int = 2):
        return (MarketCycle(self.quote.cycle, (self.quote,)),)


def _settings() -> PaperAutoSettings:
    return PaperAutoSettings(
        tick_seconds=1.0,
        max_data_age_seconds=4.0,
        minimum_spot_move_fraction=0.0,
        minimum_premium_move_fraction=0.0,
        minimum_edge_proxy=0.0,
        minimum_liquidity_score=0.0,
        maximum_spread_fraction=0.20,
        maximum_moneyness_fraction=0.05,
        maximum_position_fraction=1.0,
        maximum_contracts=10,
        stop_loss_fraction=0.50,
        take_profit_fraction=0.50,
        max_hold_seconds=1800,
        cooldown_seconds=0,
        force_exit_minutes_before_close=10.0,
        fee_per_contract=0.65,
    )


def _quote(now: datetime) -> QuoteRow:
    return QuoteRow(
        cycle=now,
        symbol="SPY260925C00770000",
        expiration="2026-09-25",
        right="call",
        strike=770.0,
        bid=0.50,
        ask=0.52,
        underlying_price=769.5,
        volume=5000,
        open_interest=15000,
        delta=0.50,
        feed_delay_seconds=0.0,
    )


def _metrics(*, support: float, ev: float, lower: float, p_positive: float) -> DynamicExitMetrics:
    return DynamicExitMetrics(
        direction_support=support,
        expected_return=ev,
        lower_confidence_edge=lower,
        positive_return_probability=p_positive,
        forecast_probability_up=support,
        agreement_score=0.70,
        calibration_score=0.70,
        regime_match_score=0.70,
        horizon_minutes=2.0,
    )


def test_dynamic_exit_holds_when_remaining_edge_is_positive(monkeypatch):
    monkeypatch.delenv("PAPER_DYNAMIC_EXIT_REVERSAL_SUPPORT", raising=False)
    metrics = _metrics(support=0.63, ev=0.08, lower=0.03, p_positive=0.62)
    assert classify_dynamic_exit(metrics) is None


def test_dynamic_exit_detects_reversal_and_edge_decay(monkeypatch):
    monkeypatch.delenv("PAPER_DYNAMIC_EXIT_REVERSAL_SUPPORT", raising=False)
    assert classify_dynamic_exit(
        _metrics(support=0.40, ev=0.03, lower=0.01, p_positive=0.60)
    ) == "direction_reversal"
    assert classify_dynamic_exit(
        _metrics(support=0.60, ev=-0.03, lower=-0.02, p_positive=0.40)
    ) == "edge_decay"


def test_dynamic_exit_requires_confirmation_before_selling(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPER_DYNAMIC_EXIT_MIN_HOLD_SECONDS", "10")
    monkeypatch.setenv("PAPER_DYNAMIC_EXIT_CONFIRM_TICKS", "2")
    now = datetime(2026, 9, 25, 15, 0, tzinfo=timezone.utc)
    quote = _quote(now)
    account = PaperAccountStore(tmp_path / "paper.sqlite", default_starting_cash=115.0)
    position = account.open_long(
        symbol=quote.symbol,
        quantity=1,
        fill_price=40.0,
        opened_at=now - timedelta(seconds=60),
        entry_spot=769.0,
        strategy="test",
        stop_price=20.0,
        target_price=60.0,
        max_hold_seconds=1800,
        reason="test",
    )
    trader = DynamicExitEnsemblePaperAutoTrader(
        mode_getter=lambda: "PAPER",
        market_reader=FakeReader(quote),
        account_store=account,
        settings=_settings(),
        risk_config=risk_config_from_env(),
        event_multiplier_getter=lambda: 1.0,
    )
    trader._remaining_edge = lambda symbol, tick_now: _metrics(  # type: ignore[method-assign]
        support=0.60, ev=-0.03, lower=-0.02, p_positive=0.40
    )

    first = trader._manage_position(position, now, "PAPER")
    assert first["state"] == "POSITION_OPEN"
    assert first["position"]["dynamic_exit"]["confirmation_ticks"] == 1
    assert account.snapshot().open_positions == 1

    second = trader._manage_position(account.positions()[0], now + timedelta(seconds=1), "PAPER")
    assert second["state"] == "POSITION_CLOSED"
    assert second["reason"] == "edge_decay"
    assert account.snapshot().open_positions == 0


def test_hard_max_hold_remains_independent_ceiling(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPER_DYNAMIC_EXIT_MIN_HOLD_SECONDS", "10")
    now = datetime(2026, 9, 25, 15, 0, tzinfo=timezone.utc)
    quote = _quote(now)
    account = PaperAccountStore(tmp_path / "paper.sqlite", default_starting_cash=115.0)
    position = account.open_long(
        symbol=quote.symbol,
        quantity=1,
        fill_price=40.0,
        opened_at=now - timedelta(seconds=121),
        entry_spot=769.0,
        strategy="test",
        stop_price=20.0,
        target_price=60.0,
        max_hold_seconds=120,
        reason="test",
    )
    trader = DynamicExitEnsemblePaperAutoTrader(
        mode_getter=lambda: "PAPER",
        market_reader=FakeReader(quote),
        account_store=account,
        settings=_settings(),
        risk_config=risk_config_from_env(),
        event_multiplier_getter=lambda: 1.0,
    )
    trader._remaining_edge = lambda symbol, tick_now: _metrics(  # type: ignore[method-assign]
        support=0.70, ev=0.10, lower=0.05, p_positive=0.70
    )

    result = trader._manage_position(position, now, "PAPER")
    assert result["state"] == "POSITION_CLOSED"
    assert result["reason"] == "hard_max_hold"
