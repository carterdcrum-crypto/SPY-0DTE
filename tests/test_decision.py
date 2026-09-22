from engine.decision import evaluate_trade
from engine.models import DecisionConfig, RiskState, TradeCandidate


def candidate(**overrides):
    values = dict(
        option_symbol="SPY-TEST-CALL",
        entry_cost_per_contract=50.0,
        scenario_returns=(-1.0, -0.25, 0.40, 1.20),
        scenario_probabilities=(0.08, 0.12, 0.45, 0.35),
        lower_confidence_edge=0.03,
        model_confidence=0.85,
        calibration_score=0.90,
        agreement_score=0.88,
        regime_match_score=0.90,
        liquidity_score=0.90,
        spread_fraction=0.03,
        data_age_seconds=0.5,
        ood_score=0.10,
    )
    values.update(overrides)
    return TradeCandidate(**values)


def state(**overrides):
    values = dict(
        account_equity=10_000.0,
        high_watermark=10_000.0,
        settled_cash=5_000.0,
        daily_start_equity=10_000.0,
        daily_realized_pnl=0.0,
    )
    values.update(overrides)
    return RiskState(**values)


def test_stale_data_is_hard_veto():
    result = evaluate_trade(candidate(data_age_seconds=10.0), state(), DecisionConfig())
    assert not result.allowed
    assert result.reason == "stale_market_data"


def test_drawdown_halt_is_hard_veto():
    result = evaluate_trade(
        candidate(),
        state(account_equity=8_900.0, high_watermark=10_000.0),
        DecisionConfig(maximum_drawdown_fraction=0.10),
    )
    assert not result.allowed
    assert result.reason == "drawdown_halt"


def test_unsettled_money_cannot_be_reused():
    result = evaluate_trade(
        candidate(entry_cost_per_contract=100.0),
        state(settled_cash=50.0),
        DecisionConfig(),
    )
    assert not result.allowed
    assert result.reason == "insufficient_settled_cash"


def test_good_candidate_can_be_approved_but_is_bounded():
    config = DecisionConfig(maximum_position_fraction=0.02)
    result = evaluate_trade(candidate(), state(), config)
    assert result.allowed
    assert result.contracts >= 1
    assert result.account_fraction <= config.maximum_position_fraction + 1e-12
