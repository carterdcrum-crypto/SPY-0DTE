from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Tuple


@dataclass(frozen=True)
class ModelForecast:
    name: str
    probability_up: float
    expected_log_return: float
    volatility: float
    validation_loss: float
    calibration_score: float
    regime_match_score: float


@dataclass(frozen=True)
class EnsembleForecast:
    probability_up: float
    expected_log_return: float
    volatility: float
    agreement_score: float
    calibration_score: float
    regime_match_score: float
    model_weights: Tuple[Tuple[str, float], ...]


def _clip01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def combine_forecasts(
    forecasts: Iterable[ModelForecast],
    *,
    loss_sensitivity: float = 3.0,
) -> EnsembleForecast:
    """Combine forecasts using exponential recent-loss weighting.

    Each model's directional probability is additionally shrunk toward 0.5 by
    its calibration and regime-match quality. This prevents an overconfident but
    poorly calibrated model from dominating simply because it emits extreme
    probabilities.
    """

    items = tuple(forecasts)
    if not items:
        raise ValueError("at least one forecast is required")

    raw_weights = [math.exp(-max(0.0, loss_sensitivity) * max(0.0, f.validation_loss)) for f in items]
    total_weight = sum(raw_weights)
    if total_weight <= 0:
        raise ValueError("forecast weights collapsed to zero")
    weights = [w / total_weight for w in raw_weights]

    adjusted_probabilities = []
    for forecast in items:
        p = _clip01(forecast.probability_up)
        reliability = _clip01(forecast.calibration_score) * _clip01(forecast.regime_match_score)
        adjusted_probabilities.append(0.5 + (p - 0.5) * reliability)

    p_up = sum(w * p for w, p in zip(weights, adjusted_probabilities))
    expected_return = sum(w * f.expected_log_return for w, f in zip(weights, items))
    volatility = sum(w * max(1e-9, f.volatility) for w, f in zip(weights, items))
    calibration = sum(w * _clip01(f.calibration_score) for w, f in zip(weights, items))
    regime = sum(w * _clip01(f.regime_match_score) for w, f in zip(weights, items))

    # Probability variance maxes at 0.25 on [0, 1]. Normalizing by that gives a
    # simple 0..1 agreement score: unanimous -> 1, maximally split -> 0.
    variance = sum(w * (p - p_up) ** 2 for w, p in zip(weights, adjusted_probabilities))
    agreement = _clip01(1.0 - variance / 0.25)

    return EnsembleForecast(
        probability_up=_clip01(p_up),
        expected_log_return=expected_return,
        volatility=max(1e-9, volatility),
        agreement_score=agreement,
        calibration_score=calibration,
        regime_match_score=regime,
        model_weights=tuple((f.name, w) for f, w in zip(items, weights)),
    )
