from __future__ import annotations

from datetime import datetime, timedelta, timezone

from engine.dynamic_risk import DynamicRiskConfig
from engine.paper_account import PaperAccountStore
from engine.paper_autotrader import MarketCycle, PaperAutoSettings, QuoteRow
from engine.paper_dynamic_autotrader import DynamicPaperAutoTrader


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
        symbol="SPY260924C00766000",
        expiration="2026-09-24",
        right="call",
        strike=766.0,
        bid=bid,
        ask=ask,
        underlying_price=spot,
        volume=1500,
        open_interest=7000,
        delta=0.35,
        feed_delay_seconds=0.0,
    )


def _settings() -> PaperAutoSettings:
    return PaperAutoSettings(
        tick_seconds=1.0,
        max_data_age_seconds=4.0,
        minimum_spot_move_fraction=0.0002,
        minimum_premium_move_fraction=0.005,
        minimum_edge_proxy=0.0,
        minimum_liquidity_score=0.40,
        maximum_spread_fraction=0.15,
        maximum_moneyness_fraction=0.03,
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
        kelly_fraction=0.20,
        minimum_positive_edge_probability=0.55,
        maximum_contracts=10,
        trade_es99_fraction=0.30,
        aggregate_es99_fraction=0.35,
        maximum_premium_loss_fraction=0.35,
        maximum_capital_fraction=0.90,
        maximum_quote_age_seconds=4.0,
        maximum_spread_fraction=0.15,
        minimum_liquidity_score=0.40,
        minimum_minutes_to_expiry=20.0,
        allow_minimum_viable_contract=True,
    )


def test_dynamic_paper_trader_sizes_and_opens_one_micro_account_contract(tmp_path):
    now = datetime(2026, 9, 24, 15, 0, tzinfo=timezone.utc)
    previous = _quote(cycle=now - timedelta(seconds=2), spot=765.5, bid=0.19, ask=0.21)
    latest = _quote(cycle=now - timedelta(seconds=1), spot=766.2, bid=0.29, ask=0.31)
    reader = FakeReader(
        cycles=(MarketCycle(latest.cycle, (latest,)), MarketCycle(previous.cycle, (previous,))),
        latest_quote=latest,
    )
    account = PaperAccountStore(tmp_path / "paper.sqlite", default_starting_cash=115.0)
    trader = DynamicPaperAutoTrader(
        mode_getter=lambda: "PAPER",
        market_reader=reader,
        account_store=account,
        settings=_settings(),
        risk_config=_risk_config(),
        event_multiplier_getter=lambda: 1.0,
    )

    result = trader.tick(now)

    assert result["state"] == "POSITION_OPENED"
    assert result["last_action"] == "BUY"
    assert result["risk"]["allowed"] is True
    assert result["risk"]["contracts"] == 1
    assert result["risk"]["sizing_mode"] in {"minimum_viable_contract", "fractional_kelly"}
    assert account.snapshot().open_positions == 1


def test_dynamic_shadow_reports_risk_without_touching_cash(tmp_path):
    now = datetime(2026, 9, 24, 15, 0, tzinfo=timezone.utc)
    previous = _quote(cycle=now - timedelta(seconds=2), spot=765.5, bid=0.19, ask=0.21)
    latest = _quote(cycle=now - timedelta(seconds=1), spot=766.2, bid=0.29, ask=0.31)
    reader = FakeReader(
        cycles=(MarketCycle(latest.cycle, (latest,)), MarketCycle(previous.cycle, (previous,))),
        latest_quote=latest,
    )
    account = PaperAccountStore(tmp_path / "paper.sqlite", default_starting_cash=115.0)
    trader = DynamicPaperAutoTrader(
        mode_getter=lambda: "SHADOW",
        market_reader=reader,
        account_store=account,
        settings=_settings(),
        risk_config=_risk_config(),
        event_multiplier_getter=lambda: 1.0,
    )

    result = trader.tick(now)

    assert result["state"] == "SHADOW_SIGNAL"
    assert result["last_action"] == "WOULD_BUY"
    assert result["risk"]["contracts"] == 1
    snapshot = account.snapshot()
    assert snapshot.open_positions == 0
    assert snapshot.settled_cash == 115.0


def test_dynamic_event_throttle_can_block_otherwise_valid_entry(tmp_path):
    now = datetime(2026, 9, 24, 15, 0, tzinfo=timezone.utc)
    previous = _quote(cycle=now - timedelta(seconds=2), spot=765.5, bid=0.19, ask=0.21)
    latest = _quote(cycle=now - timedelta(seconds=1), spot=766.2, bid=0.29, ask=0.31)
    reader = FakeReader(
        cycles=(MarketCycle(latest.cycle, (latest,)), MarketCycle(previous.cycle, (previous,))),
        latest_quote=latest,
    )
    account = PaperAccountStore(tmp_path / "paper.sqlite", default_starting_cash=115.0)
    trader = DynamicPaperAutoTrader(
        mode_getter=lambda: "PAPER",
        market_reader=reader,
        account_store=account,
        settings=_settings(),
        risk_config=_risk_config(),
        event_multiplier_getter=lambda: 0.25,
    )

    result = trader.tick(now)

    assert result["state"] == "RISK_REJECTED"
    assert result["risk"]["reason"] == "adaptive_throttle_below_one_contract"
    assert account.snapshot().open_positions == 0
