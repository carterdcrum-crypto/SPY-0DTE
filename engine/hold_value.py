from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Iterable, Tuple

from .ensemble import EnsembleForecast
from .market import OptionQuote
from .options import reprice_from_greeks
from .scenario import ReturnScenario


@dataclass(frozen=True)
class HoldValueResult:
    """Incremental value of continuing to hold an already-owned option.

    The correct baseline for an open position is the cash available by selling
    *now* at the executable bid, not the ask that would be paid to enter a new
    position. This prevents the exit model from charging the already-sunk entry
    spread a second time when deciding whether to keep holding.
    """

    sell_now_value: float
    expected_future_exit_value: float
    expected_advantage_dollars: float
    expected_advantage_fraction: float
    lower_confidence_advantage_dollars: float
    lower_confidence_advantage_fraction: float
    probability_hold_beats_sell_now: float
    dispersion_dollars: float
    uncertainty_penalty_dollars: float
    reliability: float
    scenario_advantages_dollars: Tuple[float, ...]
    scenario_probabilities: Tuple[float, ...]

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _weighted_mean(values: tuple[float, ...], probabilities: tuple[float, ...]) -> float:
    total = sum(probabilities)
    if total <= 0.0:
        raise ValueError("scenario probabilities must sum to a positive value")
    return sum(value * probability for value, probability in zip(values, probabilities)) / total


def evaluate_hold_vs_sell_now(
    quote: OptionQuote,
    scenarios: Iterable[ReturnScenario],
    forecast: EnsembleForecast,
    *,
    horizon_minutes: float,
    fee_per_contract: float = 0.65,
    future_exit_slippage_spread_fraction: float = 0.35,
    uncertainty_aversion: float = 0.90,
) -> HoldValueResult:
    """Compare holding an open long option with liquidating at the current bid.

    Scenario values model a future liquidation after the requested horizon. The
    baseline is the executable cash value of selling now. All outputs are
    therefore incremental to the actual choice faced by an existing holder.
    """

    scenario_items = tuple(scenarios)
    if not scenario_items:
        raise ValueError("at least one scenario is required")
    if quote.bid <= 0.0 or quote.ask <= 0.0:
        raise ValueError("hold-value comparison requires a two-sided option quote")

    probabilities = tuple(max(0.0, float(item.probability)) for item in scenario_items)
    if sum(probabilities) <= 0.0:
        raise ValueError("scenario probabilities must sum to a positive value")

    sell_now_value = max(0.0, quote.bid * 100.0 - fee_per_contract)
    future_values: list[float] = []
    advantages: list[float] = []

    for scenario in scenario_items:
        theoretical_exit = reprice_from_greeks(
            quote,
            scenario,
            horizon_minutes=horizon_minutes,
        )
        modeled_spread = max(
            quote.spread,
            theoretical_exit * quote.spread_fraction,
        )
        future_exit_price = max(
            0.0,
            theoretical_exit
            - modeled_spread * (0.5 + max(0.0, future_exit_slippage_spread_fraction)),
        )
        future_exit_value = max(0.0, future_exit_price * 100.0 - fee_per_contract)
        future_values.append(future_exit_value)
        advantages.append(future_exit_value - sell_now_value)

    future_tuple = tuple(future_values)
    advantage_tuple = tuple(advantages)
    expected_future = _weighted_mean(future_tuple, probabilities)
    expected_advantage = _weighted_mean(advantage_tuple, probabilities)
    variance = _weighted_mean(
        tuple((value - expected_advantage) ** 2 for value in advantage_tuple),
        probabilities,
    )
    dispersion = math.sqrt(max(0.0, variance))

    reliability = (
        max(0.0, min(1.0, forecast.agreement_score))
        * max(0.0, min(1.0, forecast.calibration_score))
        * max(0.0, min(1.0, forecast.regime_match_score))
    )
    uncertainty_penalty = (
        dispersion
        * (1.0 - reliability)
        * max(0.0, uncertainty_aversion)
    )
    lower_advantage = expected_advantage - uncertainty_penalty

    total_probability = sum(probabilities)
    probability_hold_wins = sum(
        probability
        for advantage, probability in zip(advantage_tuple, probabilities)
        if advantage > 0.0
    ) / total_probability

    # Express the edge as a fraction of cash we could lock in immediately.
    # The $1 floor keeps near-worthless options numerically well behaved.
    denominator = max(1.0, sell_now_value)
    return HoldValueResult(
        sell_now_value=sell_now_value,
        expected_future_exit_value=expected_future,
        expected_advantage_dollars=expected_advantage,
        expected_advantage_fraction=expected_advantage / denominator,
        lower_confidence_advantage_dollars=lower_advantage,
        lower_confidence_advantage_fraction=lower_advantage / denominator,
        probability_hold_beats_sell_now=max(0.0, min(1.0, probability_hold_wins)),
        dispersion_dollars=dispersion,
        uncertainty_penalty_dollars=uncertainty_penalty,
        reliability=reliability,
        scenario_advantages_dollars=advantage_tuple,
        scenario_probabilities=probabilities,
    )
