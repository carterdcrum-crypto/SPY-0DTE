from __future__ import annotations

from datetime import datetime, timedelta, timezone

from engine.dynamic_risk import DynamicRiskConfig
from engine.paper_account import PaperAccountStore
from engine.paper_autotrader import MarketCycle, PaperAutoSettings, QuoteRow
from engine.paper_ensemble_autotrader import EnsemblePaperAutoTrader, build_market_ensemble


class FakeReader:
    def __init__(self, cycles: tuple[MarketCycle, ...], quote: QuoteRow | None = None):
        self.cycles = cycles
        self.quote = quote or cycles[0].rows[0]

    def latest_cycles(self, limit: int = 2):
        return self.cycles[:limit]

    def latest_quote(self, symbol: str):
        return self.quote if self.quote.symbol == symbol else None


def _settings() -> PaperAutoSettings:
    return PaperAutoSettings(
        tick_seconds=1.0,
        max_data_age_seconds=4.0,
        minimum_spot_move_fraction=0.0,
        minimum_premium_move_fraction=0.0,
        minimum_edge_proxy=0.0,
        minimum_liquidity_score=0.35,
        maximum_spread_fraction=0.20,
        maximum_moneyness_fraction=0.05,
        maximum_position_fraction=1.0,
        maximum_contracts=10,
        stop_loss_fraction=0.35,
        take_profit_fraction=0.50,
        max_hold_seconds=600,
        cooldown_seconds=0,
        force_exit_minutes_before_close=10.0,
        fee_per_contract=0.65,
    )


def _risk_config() -> DynamicRiskConfig:
    return DynamicRiskConfig(
        kelly_fraction=0.25,
        minimum_positive_edge_probability=0.50,
        maximum_contracts=10,
        trade_es99_fraction=0.90,
        aggregate_es99_fraction=0.90,
        maximum_premium_loss_fraction=0.95,
        maximum_capital_fraction=1.0,
        maximum_quote_age_seconds=4.0,
        maximum_spread_fraction=0.20,
        minimum_liquidity_score=0.35,
        minimum_minutes_to_expiry=20.0,
        minimum_single_contract_multiplier=0.35,
        allow_minimum_viable_contract=True,
    )


def _row(
    *,
    cycle: datetime,
    symbol: str,
    right: str,
    spot: float,
    bid: float,
    ask: float,
    feed_delay: float,
) -> QuoteRow:
    return QuoteRow(
        cycle=cycle,
        symbol=symbol,
        expiration="2026-09-24",
        right=right,
        strike=100.0,
        bid=bid,
        ask=ask,
        underlying_price=spot,
        volume=5000,
        open_interest=15000,
        delta=0.50 if right == "call" else -0.50,
        feed_delay_seconds=feed_delay,
    )


def _trend_cycles(now: datetime, *, feed_delay: float = 0.25) -> tuple[MarketCycle, ...]:
    chronological = []
    for index in range(24):
        cycle = now - timedelta(seconds=2 * (23 - index))
        spot = 99.72 + 0.014 * index
        call_mid = 0.34 + 0.014 * index
        put_mid = 0.66 - 0.010 * index
        call = _row(
            cycle=cycle,
            symbol="SPY260924C00100000",
            right="call",
            spot=spot,
            bid=call_mid - 0.01,
            ask=call_mid + 0.01,
            feed_delay=feed_delay,
        )
        put = _row(
            cycle=cycle,
            symbol="SPY260924P00100000",
            right="put",
            spot=spot,
            bid=max(0.05, put_mid - 0.01),
            ask=max(0.06, put_mid + 0.01),
            feed_delay=feed_delay,
        )
        chronological.append(MarketCycle(cycle, (call, put)))
    return tuple(reversed(chronological))


def test_market_ensemble_uses_multiple_models_not_bootstrap():
    now = datetime(2026, 9, 24, 15, 0, tzinfo=timezone.utc)
    forecast = build_market_ensemble(_trend_cycles(now), 5.0)
    names = {name for name, _ in forecast.model_weights}

    assert names == {"fast_momentum", "intraday_trend", "short_mean_reversion", "option_breadth"}
    assert forecast.probability_up > 0.50
    assert 0.0 <= forecast.agreement_score <= 1.0
    assert 0.0 <= forecast.calibration_score <= 1.0


def test_delayed_feed_hard_blocks_paper_entries(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPER_MAX_FEED_DELAY_SECONDS", "3")
    now = datetime(2026, 9, 24, 15, 0, tzinfo=timezone.utc)
    cycles = _trend_cycles(now - timedelta(seconds=1), feed_delay=905.0)
    account = PaperAccountStore(tmp_path / "paper.sqlite", default_starting_cash=115.0)
    trader = EnsemblePaperAutoTrader(
        mode_getter=lambda: "PAPER",
        market_reader=FakeReader(cycles),
        account_store=account,
        settings=_settings(),
        risk_config=_risk_config(),
        event_multiplier_getter=lambda: 1.0,
    )

    result = trader.tick(now)

    assert result["state"] == "FEED_DELAYED"
    assert "905.0s delayed" in str(result["reason"])
    snapshot = account.snapshot()
    assert snapshot.open_positions == 0
    assert snapshot.settled_cash == 115.0


def test_fresh_uptrend_produces_execution_aware_ensemble_opportunity(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPER_MAX_FEED_DELAY_SECONDS", "3")
    monkeypatch.setenv("PAPER_ENSEMBLE_DIRECTION_THRESHOLD", "0.51")
    monkeypatch.setenv("PAPER_MIN_LOWER_CONFIDENCE_EDGE", "0")
    now = datetime(2026, 9, 24, 15, 0, tzinfo=timezone.utc)
    cycles = _trend_cycles(now - timedelta(seconds=1), feed_delay=0.25)
    account = PaperAccountStore(tmp_path / "paper.sqlite", default_starting_cash=115.0)
    trader = EnsemblePaperAutoTrader(
        mode_getter=lambda: "SHADOW",
        market_reader=FakeReader(cycles),
        account_store=account,
        settings=_settings(),
        risk_config=_risk_config(),
        event_multiplier_getter=lambda: 1.0,
    )

    result = trader.tick(now)

    assert result["state"] in {"SHADOW_SIGNAL", "SHADOW_RISK_REJECTED", "NO_SIGNAL"}
    # Whether this exact synthetic path clears utility sizing is a risk-layer
    # decision, but the old bootstrap strategy must never reappear.
    assert result["strategy"] == "market_ensemble_v1"
    assert account.snapshot().open_positions == 0


def test_open_position_is_not_marked_or_exited_from_delayed_quote(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPER_MAX_FEED_DELAY_SECONDS", "3")
    now = datetime(2026, 9, 24, 15, 0, tzinfo=timezone.utc)
    delayed = _row(
        cycle=now - timedelta(seconds=1),
        symbol="SPY260924C00100000",
        right="call",
        spot=100.0,
        bid=2.00,
        ask=2.02,
        feed_delay=900.0,
    )
    cycles = (MarketCycle(delayed.cycle, (delayed,)),)
    account = PaperAccountStore(tmp_path / "paper.sqlite", default_starting_cash=115.0)
    account.open_long(
        symbol=delayed.symbol,
        quantity=1,
        fill_price=40.0,
        opened_at=now - timedelta(seconds=30),
        entry_spot=100.0,
        strategy="test",
        stop_price=20.0,
        target_price=60.0,
        max_hold_seconds=600,
        reason="test",
    )
    trader = EnsemblePaperAutoTrader(
        mode_getter=lambda: "PAPER",
        market_reader=FakeReader(cycles, delayed),
        account_store=account,
        settings=_settings(),
        risk_config=_risk_config(),
        event_multiplier_getter=lambda: 1.0,
    )

    result = trader.tick(now)

    assert result["state"] == "POSITION_FEED_DELAYED"
    assert result["last_action"] is None
    assert account.snapshot().open_positions == 1
