from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Tuple

from .ensemble import EnsembleForecast
from .market import MarketSnapshot, OptionQuote
from .models import TradeCandidate
from .options import reprice_from_greeks
from .scenario import ReturnScenario


@dataclass(frozen=True)
class OpportunityResult:
    candidate: TradeCandidate
    expected_return: float
    return_dispersion: float
    uncertainty_penalty: float


def _weighted_mean(values: Tuple[float, ...], probabilities: Tuple[float, ...]) -> float:
    total = sum(probabilities)
    if total <= 0:
        raise ValueError("probabilities must sum to a positive value")
    return sum(v * p for v, p in zip(values, probabilities)) / total


def evaluate_option_candidate(
    quote: OptionQuote,
    scenarios: Iterable[ReturnScenario],
    forecast: EnsembleForecast,
    market: MarketSnapshot,
    *,
    horizon_minutes: float,
    fee_per_contract: float = 0.65,
    exit_slippage_spread_fraction: float = 0.25,
    uncertainty_aversion: float = 0.75,
) -> OpportunityResult:
    """Convert a normalized option quote into a risk-engine TradeCandidate.

    Entry is conservatively assumed at the ask. Scenario exits cross a modeled
    fraction of the spread, so wide/illiquid contracts are naturally penalized.
    The lower-confidence edge subtracts a dispersion penalty scaled by ensemble
    reliability; a merely positive mean return is not enough.
    """

    scenario_items = tuple(scenarios)
    if not scenario_items:
        raise ValueError("at least one scenario is required")
    if quote.ask <= 0:
        raise ValueError("option ask must be positive")

    entry_cost = quote.ask * 100.0 + fee_per_contract
    scenario_returns = []
    probabilities = []

    for scenario in scenario_items:
        theoretical_exit = reprice_from_greeks(
            quote,
            scenario,
            horizon_minutes=horizon_minutes,
        )
        modeled_spread = max(quote.spread, theoretical_exit * quote.spread_fraction)
        exit_price = max(
            0.0,
            theoretical_exit - modeled_spread * (0.5 + max(0.0, exit_slippage_spread_fraction)),
        )
        exit_value = max(0.0, exit_price * 100.0 - fee_per_contract)
        pnl = exit_value - entry_cost
        scenario_returns.append(pnl / entry_cost)
        probabilities.append(max(0.0, scenario.probability))

    returns_tuple = tuple(scenario_returns)
    probs_tuple = tuple(probabilities)
    expected_return = _weighted_mean(returns_tuple, probs_tuple)
    variance = _weighted_mean(
        tuple((value - expected_return) ** 2 for value in returns_tuple),
        probs_tuple,
    )
    dispersion = math.sqrt(max(0.0, variance))

    reliability = (
        max(0.0, min(1.0, forecast.agreement_score))
        * max(0.0, min(1.0, forecast.calibration_score))
        * max(0.0, min(1.0, forecast.regime_match_score))
    )
    uncertainty_penalty = dispersion * (1.0 - reliability) * max(0.0, uncertainty_aversion)
    lower_confidence_edge = expected_return - uncertainty_penalty

    directional_confidence = 2.0 * abs(forecast.probability_up - 0.5)
    model_confidence = max(0.0, min(1.0, directional_confidence * reliability))

    candidate = TradeCandidate(
        option_symbol=quote.symbol,
        entry_cost_per_contract=entry_cost,
        scenario_returns=returns_tuple,
        scenario_probabilities=probs_tuple,
        lower_confidence_edge=lower_confidence_edge,
        model_confidence=model_confidence,
        calibration_score=forecast.calibration_score,
        agreement_score=forecast.agreement_score,
        regime_match_score=forecast.regime_match_score,
        liquidity_score=quote.liquidity_score,
        spread_fraction=quote.spread_fraction,
        data_age_seconds=market.data_age_seconds,
        ood_score=market.ood_score,
    )

    return OpportunityResult(
        candidate=candidate,
        expected_return=expected_return,
        return_dispersion=dispersion,
        uncertainty_penalty=uncertainty_penalty,
    )


def rank_opportunities(results: Iterable[OpportunityResult]) -> Tuple[OpportunityResult, ...]:
    return tuple(
        sorted(
            results,
            key=lambda item: (
                item.candidate.lower_confidence_edge,
                item.candidate.liquidity_score,
                -item.candidate.spread_fraction,
            ),
            reverse=True,
        )
    )
