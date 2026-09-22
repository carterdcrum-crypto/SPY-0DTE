from __future__ import annotations

import math
from typing import Iterable, Sequence, Tuple


def _normalize_probabilities(probabilities: Sequence[float]) -> Tuple[float, ...]:
    if not probabilities:
        raise ValueError("probabilities cannot be empty")
    if any(p < 0 for p in probabilities):
        raise ValueError("probabilities cannot be negative")
    total = float(sum(probabilities))
    if total <= 0:
        raise ValueError("probabilities must sum to a positive value")
    return tuple(float(p) / total for p in probabilities)


def expected_log_growth(
    fraction: float,
    returns: Sequence[float],
    probabilities: Sequence[float],
) -> float:
    """Expected log wealth growth for a fraction of equity exposed to a trade.

    Returns are decimal returns on the exposed capital. For long options, -1.0
    represents loss of the entire premium. If any scenario would reduce wealth to
    zero or below, that sizing is treated as inadmissible.
    """

    if fraction < 0:
        raise ValueError("fraction cannot be negative")
    if len(returns) != len(probabilities) or not returns:
        raise ValueError("returns and probabilities must be non-empty and aligned")

    probs = _normalize_probabilities(probabilities)
    total = 0.0
    for scenario_return, probability in zip(returns, probs):
        wealth_multiplier = 1.0 + fraction * float(scenario_return)
        if wealth_multiplier <= 0.0:
            return -math.inf
        total += probability * math.log(wealth_multiplier)
    return total


def optimal_kelly_fraction(
    returns: Sequence[float],
    probabilities: Sequence[float],
    maximum_fraction: float = 1.0,
    iterations: int = 100,
) -> float:
    """Numerically maximize expected log growth on [0, maximum_fraction].

    Expected log growth is concave in position fraction for a fixed return
    distribution, so a golden-section search is reliable here without SciPy.
    """

    if maximum_fraction <= 0:
        return 0.0

    lo = 0.0
    hi = float(maximum_fraction)
    phi = (1.0 + math.sqrt(5.0)) / 2.0

    c = hi - (hi - lo) / phi
    d = lo + (hi - lo) / phi
    fc = expected_log_growth(c, returns, probabilities)
    fd = expected_log_growth(d, returns, probabilities)

    for _ in range(iterations):
        if fc > fd:
            hi = d
            d = c
            fd = fc
            c = hi - (hi - lo) / phi
            fc = expected_log_growth(c, returns, probabilities)
        else:
            lo = c
            c = d
            fc = fd
            d = lo + (hi - lo) / phi
            fd = expected_log_growth(d, returns, probabilities)

    candidate = (lo + hi) / 2.0
    # Never force capital into a trade whose best log-growth is non-positive.
    if expected_log_growth(candidate, returns, probabilities) <= 0.0:
        return 0.0
    return max(0.0, min(candidate, maximum_fraction))


def weighted_cvar(
    losses: Sequence[float],
    probabilities: Sequence[float],
    confidence_level: float = 0.99,
) -> float:
    """Weighted CVaR: average loss in the worst (1-confidence) probability mass."""

    if len(losses) != len(probabilities) or not losses:
        raise ValueError("losses and probabilities must be non-empty and aligned")
    if not 0.0 < confidence_level < 1.0:
        raise ValueError("confidence_level must be between 0 and 1")
    if any(loss < 0 for loss in losses):
        raise ValueError("losses must be non-negative")

    probs = _normalize_probabilities(probabilities)
    tail_probability = 1.0 - confidence_level
    remaining = tail_probability
    weighted_tail_loss = 0.0

    ranked = sorted(zip(losses, probs), key=lambda pair: pair[0], reverse=True)
    for loss, probability in ranked:
        if remaining <= 1e-15:
            break
        taken = min(probability, remaining)
        weighted_tail_loss += float(loss) * taken
        remaining -= taken

    if tail_probability <= 0:
        return 0.0
    return weighted_tail_loss / tail_probability


def drawdown_multiplier(drawdown_fraction: float, halt_fraction: float) -> float:
    if halt_fraction <= 0:
        return 0.0
    if drawdown_fraction <= 0:
        return 1.0
    return max(0.0, 1.0 - drawdown_fraction / halt_fraction)


def constrained_fractional_kelly(
    *,
    account_equity: float,
    returns: Sequence[float],
    probabilities: Sequence[float],
    model_confidence: float,
    calibration_score: float,
    agreement_score: float,
    regime_match_score: float,
    drawdown_fraction: float,
    drawdown_halt_fraction: float,
    safety_fraction: float,
    maximum_position_fraction: float,
    maximum_cvar_fraction_of_equity: float,
    cvar_confidence_level: float,
) -> tuple[float, float, float]:
    """Return (approved_fraction, raw_kelly_fraction, CVaR dollars).

    The Kelly optimum is shrunk by reliability, regime quality, drawdown state,
    and an explicit safety fraction. A final CVaR cap can shrink it further.
    """

    if account_equity <= 0:
        return 0.0, 0.0, 0.0

    raw_kelly = optimal_kelly_fraction(
        returns,
        probabilities,
        maximum_fraction=min(1.0, max(0.0, maximum_position_fraction / max(safety_fraction, 1e-12) * 4.0)),
    )

    quality = 1.0
    for score in (model_confidence, calibration_score, agreement_score, regime_match_score):
        quality *= max(0.0, min(1.0, float(score)))

    fraction = raw_kelly
    fraction *= max(0.0, min(1.0, safety_fraction))
    fraction *= quality
    fraction *= drawdown_multiplier(drawdown_fraction, drawdown_halt_fraction)
    fraction = min(fraction, max(0.0, maximum_position_fraction))

    if fraction <= 0.0:
        return 0.0, raw_kelly, 0.0

    scenario_losses = [
        max(0.0, -float(r) * account_equity * fraction)
        for r in returns
    ]
    cvar = weighted_cvar(scenario_losses, probabilities, cvar_confidence_level)
    cvar_budget = account_equity * max(0.0, maximum_cvar_fraction_of_equity)

    if cvar > cvar_budget and cvar > 0.0:
        scale = cvar_budget / cvar
        fraction *= scale
        scenario_losses = [
            max(0.0, -float(r) * account_equity * fraction)
            for r in returns
        ]
        cvar = weighted_cvar(scenario_losses, probabilities, cvar_confidence_level)

    return max(0.0, fraction), raw_kelly, cvar
