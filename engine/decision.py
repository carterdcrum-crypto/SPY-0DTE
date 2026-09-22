from __future__ import annotations

from .models import DecisionConfig, DecisionResult, RiskState, TradeCandidate
from .risk import constrained_fractional_kelly


def _blocked(reason: str) -> DecisionResult:
    return DecisionResult(allowed=False, reason=reason)


def evaluate_trade(
    candidate: TradeCandidate,
    state: RiskState,
    config: DecisionConfig,
) -> DecisionResult:
    """Apply non-negotiable vetoes, then constrained capital sizing.

    This function is deliberately deterministic. Forecast/LLM components may
    populate the candidate, but none can bypass these gates.
    """

    if candidate.entry_cost_per_contract <= 0:
        return _blocked("invalid_entry_cost")
    if len(candidate.scenario_returns) != len(candidate.scenario_probabilities):
        return _blocked("invalid_scenarios")
    if not candidate.scenario_returns:
        return _blocked("missing_scenarios")
    if any(r < -1.0 for r in candidate.scenario_returns):
        return _blocked("invalid_long_option_return")

    if candidate.lower_confidence_edge <= config.minimum_lower_confidence_edge:
        return _blocked("insufficient_lower_confidence_edge")
    if candidate.liquidity_score < config.minimum_liquidity_score:
        return _blocked("insufficient_liquidity")
    if candidate.spread_fraction > config.maximum_spread_fraction:
        return _blocked("spread_too_wide")
    if candidate.data_age_seconds > config.maximum_data_age_seconds:
        return _blocked("stale_market_data")
    if candidate.ood_score > config.maximum_ood_score:
        return _blocked("out_of_distribution_market")
    if state.drawdown_fraction >= config.maximum_drawdown_fraction:
        return _blocked("drawdown_halt")
    if state.daily_loss_fraction >= config.maximum_daily_loss_fraction:
        return _blocked("daily_loss_halt")
    if state.account_equity <= 0:
        return _blocked("no_equity")
    if state.settled_cash < candidate.entry_cost_per_contract:
        return _blocked("insufficient_settled_cash")

    fraction, raw_kelly, cvar_dollars = constrained_fractional_kelly(
        account_equity=state.account_equity,
        returns=candidate.scenario_returns,
        probabilities=candidate.scenario_probabilities,
        model_confidence=candidate.model_confidence,
        calibration_score=candidate.calibration_score,
        agreement_score=candidate.agreement_score,
        regime_match_score=candidate.regime_match_score,
        drawdown_fraction=state.drawdown_fraction,
        drawdown_halt_fraction=config.maximum_drawdown_fraction,
        safety_fraction=config.kelly_safety_fraction,
        maximum_position_fraction=config.maximum_position_fraction,
        maximum_cvar_fraction_of_equity=config.maximum_cvar_fraction_of_equity,
        cvar_confidence_level=config.cvar_confidence_level,
    )

    if fraction <= config.minimum_trade_fraction:
        return DecisionResult(
            allowed=False,
            reason="non_positive_growth_after_risk",
            raw_kelly_fraction=raw_kelly,
            cvar_dollars=cvar_dollars,
        )

    capital_budget = min(state.account_equity * fraction, state.settled_cash)
    contracts = int(capital_budget // candidate.entry_cost_per_contract)
    if contracts < 1:
        return DecisionResult(
            allowed=False,
            reason="position_below_one_contract",
            account_fraction=fraction,
            raw_kelly_fraction=raw_kelly,
            cvar_dollars=cvar_dollars,
        )

    capital_to_deploy = contracts * candidate.entry_cost_per_contract
    final_fraction = capital_to_deploy / state.account_equity

    return DecisionResult(
        allowed=True,
        reason="approved",
        contracts=contracts,
        capital_to_deploy=capital_to_deploy,
        account_fraction=final_fraction,
        raw_kelly_fraction=raw_kelly,
        cvar_dollars=cvar_dollars,
    )
