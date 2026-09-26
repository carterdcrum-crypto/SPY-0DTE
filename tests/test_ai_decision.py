from __future__ import annotations

from engine.ai_consensus import AIConsensus, AIProviderSignal
from engine.ai_decision import blend_ai_consensus_into_forecast
from engine.ensemble import EnsembleForecast


def _forecast(probability_up: float = 0.54) -> EnsembleForecast:
    return EnsembleForecast(
        probability_up=probability_up,
        expected_log_return=0.0004,
        volatility=0.002,
        agreement_score=0.82,
        calibration_score=0.78,
        regime_match_score=0.80,
        model_weights=(("quant_a", 0.5), ("quant_b", 0.5)),
    )


def _signal(
    provider: str,
    probability_up: float,
    confidence: float = 0.90,
    risk_multiplier: float = 1.0,
) -> AIProviderSignal:
    return AIProviderSignal(
        provider=provider,
        model="test-model",
        probability_up=probability_up,
        confidence=confidence,
        risk_multiplier=risk_multiplier,
        regime="trend",
        event_risk="low",
        rationale="test",
        latency_ms=10,
    )


def _consensus(*signals: AIProviderSignal) -> AIConsensus:
    total_confidence = sum(signal.confidence for signal in signals)
    weights = [signal.confidence / total_confidence for signal in signals]
    probability_up = sum(weight * signal.probability_up for weight, signal in zip(weights, signals))
    confidence = sum(weight * signal.confidence for weight, signal in zip(weights, signals))
    risk_multiplier = sum(weight * signal.risk_multiplier for weight, signal in zip(weights, signals))
    disagreement = sum(
        weight * abs(signal.probability_up - probability_up)
        for weight, signal in zip(weights, signals)
    )
    return AIConsensus(
        signals=tuple(signals),
        probability_up=probability_up,
        confidence=confidence,
        risk_multiplier=risk_multiplier,
        disagreement=disagreement,
        generated_at="2026-09-26T00:00:00Z",
    )


def test_ai_consensus_can_change_directional_decision_before_contract_selection() -> None:
    result = blend_ai_consensus_into_forecast(
        _forecast(0.54),
        _consensus(_signal("openai", 0.82, confidence=0.95)),
        minimum_confidence=0.45,
        maximum_ai_weight=0.35,
    )

    assert result.effective_weight > 0.0
    assert result.forecast.probability_up > 0.55
    assert result.forecast.probability_up > result.quant_probability_up
    assert any(name == "ai_decision:openai" and weight > 0.0 for name, weight in result.forecast.model_weights)


def test_low_confidence_ai_falls_back_to_quant_forecast() -> None:
    quant = _forecast(0.61)
    result = blend_ai_consensus_into_forecast(
        quant,
        _consensus(_signal("openai", 0.20, confidence=0.20)),
        minimum_confidence=0.45,
        maximum_ai_weight=0.35,
    )

    assert result.effective_weight == 0.0
    assert result.forecast == quant


def test_disagreement_and_ai_risk_multiplier_reduce_influence_and_model_health() -> None:
    aligned = blend_ai_consensus_into_forecast(
        _forecast(),
        _consensus(
            _signal("openai", 0.75, confidence=0.9, risk_multiplier=1.0),
            _signal("gemini", 0.73, confidence=0.9, risk_multiplier=1.0),
        ),
        minimum_confidence=0.45,
        maximum_ai_weight=0.35,
    )
    split = blend_ai_consensus_into_forecast(
        _forecast(),
        _consensus(
            _signal("openai", 0.80, confidence=0.9, risk_multiplier=0.55),
            _signal("gemini", 0.20, confidence=0.9, risk_multiplier=0.55),
        ),
        minimum_confidence=0.45,
        maximum_ai_weight=0.35,
    )

    assert split.effective_weight < aligned.effective_weight
    assert split.forecast.regime_match_score < aligned.forecast.regime_match_score
    assert split.forecast.agreement_score < aligned.forecast.agreement_score
