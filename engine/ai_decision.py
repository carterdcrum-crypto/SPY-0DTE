from __future__ import annotations

from dataclasses import dataclass

from .ai_consensus import AIConsensus
from .ensemble import EnsembleForecast


def _clip(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, float(value)))


@dataclass(frozen=True)
class AIDecisionBlend:
    forecast: EnsembleForecast
    effective_weight: float
    quant_probability_up: float
    ai_probability_up: float
    direction_shift: float
    cross_model_agreement: float

    def as_dict(self) -> dict[str, object]:
        return {
            "effective_weight": self.effective_weight,
            "quant_probability_up": self.quant_probability_up,
            "ai_probability_up": self.ai_probability_up,
            "hybrid_probability_up": self.forecast.probability_up,
            "direction_shift": self.direction_shift,
            "cross_model_agreement": self.cross_model_agreement,
            "model_weights": {name: weight for name, weight in self.forecast.model_weights},
        }


def blend_ai_consensus_into_forecast(
    forecast: EnsembleForecast,
    advice: AIConsensus | None,
    *,
    minimum_confidence: float,
    maximum_ai_weight: float,
) -> AIDecisionBlend:
    """Blend configured AI consensus into the quantitative market forecast.

    The AI is a real directional/model-health input before contract selection,
    scenario generation and dynamic sizing. Its influence is bounded by
    `maximum_ai_weight`, provider confidence, consensus disagreement and the
    consensus risk multiplier. Hard account/risk rails remain downstream.
    """

    quant_probability = _clip(forecast.probability_up, 0.0, 1.0)
    maximum_ai_weight = _clip(maximum_ai_weight, 0.0, 0.75)
    minimum_confidence = _clip(minimum_confidence, 0.0, 1.0)

    if advice is None or advice.confidence < minimum_confidence or not advice.signals:
        return AIDecisionBlend(
            forecast=forecast,
            effective_weight=0.0,
            quant_probability_up=quant_probability,
            ai_probability_up=0.5 if advice is None else advice.probability_up,
            direction_shift=0.0,
            cross_model_agreement=1.0,
        )

    disagreement_quality = _clip(1.0 - 1.5 * advice.disagreement, 0.20, 1.0)
    effective_weight = _clip(
        maximum_ai_weight
        * advice.confidence
        * advice.risk_multiplier
        * disagreement_quality,
        0.0,
        maximum_ai_weight,
    )
    ai_probability = _clip(advice.probability_up, 0.0, 1.0)
    hybrid_probability = (
        (1.0 - effective_weight) * quant_probability
        + effective_weight * ai_probability
    )

    # Agreement across the quant stack and AI stack becomes part of model
    # health. Both quant-vs-AI disagreement and disagreement between AI
    # providers can reduce sizing; agreement never inflates the original
    # calibration/regime scores above the quant model's values.
    cross_model_agreement = _clip(
        1.0 - 2.0 * abs(quant_probability - ai_probability),
        0.0,
        1.0,
    )
    provider_agreement_factor = 0.60 + 0.40 * disagreement_quality
    agreement = _clip(
        forecast.agreement_score
        * (0.65 + 0.35 * cross_model_agreement)
        * provider_agreement_factor,
        0.0,
        1.0,
    )
    calibration = min(
        forecast.calibration_score,
        forecast.calibration_score * (0.80 + 0.20 * advice.confidence),
    )
    regime_match = min(
        forecast.regime_match_score,
        forecast.regime_match_score * (0.50 + 0.50 * advice.risk_multiplier),
    )

    quant_weights = tuple(
        (name, weight * (1.0 - effective_weight))
        for name, weight in forecast.model_weights
    )
    provider_raw = [max(0.05, signal.confidence) for signal in advice.signals]
    provider_total = sum(provider_raw) or 1.0
    ai_weights = tuple(
        (
            f"ai_decision:{signal.provider}",
            effective_weight * raw / provider_total,
        )
        for signal, raw in zip(advice.signals, provider_raw)
    )

    hybrid = EnsembleForecast(
        probability_up=_clip(hybrid_probability, 0.0, 1.0),
        expected_log_return=forecast.expected_log_return,
        volatility=forecast.volatility,
        agreement_score=agreement,
        calibration_score=calibration,
        regime_match_score=regime_match,
        model_weights=quant_weights + ai_weights,
    )
    return AIDecisionBlend(
        forecast=hybrid,
        effective_weight=effective_weight,
        quant_probability_up=quant_probability,
        ai_probability_up=ai_probability,
        direction_shift=hybrid.probability_up - quant_probability,
        cross_model_agreement=cross_model_agreement,
    )
