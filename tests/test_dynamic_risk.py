from engine.dynamic_risk import (
    DynamicRiskConfig,
    DynamicRiskRequest,
    choose_dynamic_long_option_size,
    smooth_drawdown_multiplier,
)


def _request(**overrides):
    edge_probability = overrides.pop("positive_edge_probability", 0.62)
    loss_probability = 1.0 - edge_probability
    values = dict(
        account_equity=115.0,
        high_watermark=115.0,
        settled_cash=115.0,
        daily_start_equity=115.0,
        daily_realized_pnl=0.0,
        entry_cost_per_contract=31.65,
        scenario_returns=(-1.0, -0.375, -0.175, 0.08, 0.475),
        scenario_probabilities=(
            loss_probability * 0.05,
            loss_probability * 0.50,
            loss_probability * 0.45,
            edge_probability * 0.60,
            edge_probability * 0.40,
        ),
        positive_edge_probability=edge_probability,
        liquidity_score=0.91,
        spread_fraction=0.02,
        quote_age_seconds=0.5,
        minutes_to_expiry=220.0,
        ask_size=100,
        existing_portfolio_es99_dollars=0.0,
        event_multiplier=1.0,
        model_health_multiplier=0.85,
    )
    values.update(overrides)
    return DynamicRiskRequest(**values)


def test_micro_account_can_choose_one_minimum_viable_contract_in_paper_profile():
    result = choose_dynamic_long_option_size(_request())
    assert result.allowed
    assert result.contracts == 1
    assert result.sizing_mode == "minimum_viable_contract"
    assert result.hard_limited_contracts == 1
    assert result.es99_per_contract > 0


def test_stale_quote_is_hard_veto():
    result = choose_dynamic_long_option_size(_request(quote_age_seconds=5.0))
    assert not result.allowed
    assert result.reason == "stale_market_data"


def test_expiry_window_is_hard_veto():
    result = choose_dynamic_long_option_size(_request(minutes_to_expiry=10.0))
    assert not result.allowed
    assert result.reason == "expiry_window_block"


def test_event_multiplier_can_reduce_one_contract_to_zero():
    result = choose_dynamic_long_option_size(_request(event_multiplier=0.25))
    assert not result.allowed
    assert result.reason == "adaptive_throttle_below_one_contract"


def test_drawdown_halt_cannot_be_overridden_by_edge():
    result = choose_dynamic_long_option_size(
        _request(
            account_equity=85.0,
            high_watermark=115.0,
            settled_cash=85.0,
            positive_edge_probability=0.70,
        )
    )
    assert not result.allowed
    assert result.reason == "drawdown_halt"


def test_larger_account_uses_fractional_kelly_for_multi_contract_size():
    config = DynamicRiskConfig(
        maximum_contracts=20,
        trade_es99_fraction=0.10,
        aggregate_es99_fraction=0.20,
        maximum_premium_loss_fraction=0.20,
        maximum_capital_fraction=0.50,
        allow_minimum_viable_contract=False,
    )
    request = DynamicRiskRequest(
        account_equity=10_000.0,
        high_watermark=10_000.0,
        settled_cash=10_000.0,
        daily_start_equity=10_000.0,
        daily_realized_pnl=0.0,
        entry_cost_per_contract=50.0,
        scenario_returns=(-1.0, -0.20, 0.40, 1.00),
        scenario_probabilities=(0.05, 0.15, 0.45, 0.35),
        positive_edge_probability=0.80,
        liquidity_score=0.95,
        spread_fraction=0.02,
        quote_age_seconds=0.5,
        minutes_to_expiry=200.0,
        ask_size=100,
    )
    result = choose_dynamic_long_option_size(request, config)
    assert result.allowed
    assert result.sizing_mode == "fractional_kelly"
    assert result.contracts > 1
    assert result.contracts <= result.hard_limited_contracts


def test_drawdown_throttle_is_smooth():
    assert smooth_drawdown_multiplier(0.0, soft_start=0.03, zero_level=0.10) == 1.0
    assert 0.0 < smooth_drawdown_multiplier(0.06, soft_start=0.03, zero_level=0.10) < 1.0
    assert smooth_drawdown_multiplier(0.10, soft_start=0.03, zero_level=0.10) == 0.0
