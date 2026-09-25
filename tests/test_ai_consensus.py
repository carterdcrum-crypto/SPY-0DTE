from __future__ import annotations

from engine.ai_consensus import (
    AIAdvisoryEngine,
    AIProviderSignal,
    _parse_signal,
    combine_ai_signals,
    providers_from_env,
)


def _signal(provider: str, probability_up: float, confidence: float, risk: float) -> AIProviderSignal:
    return AIProviderSignal(
        provider=provider,
        model="test",
        probability_up=probability_up,
        confidence=confidence,
        risk_multiplier=risk,
        regime="test",
        event_risk="none",
        rationale="test",
        latency_ms=10,
    )


def test_ai_consensus_penalizes_provider_disagreement() -> None:
    aligned = combine_ai_signals(
        (
            _signal("a", 0.70, 0.8, 1.0),
            _signal("b", 0.68, 0.8, 1.0),
        )
    )
    split = combine_ai_signals(
        (
            _signal("a", 0.80, 0.8, 1.0),
            _signal("b", 0.20, 0.8, 1.0),
        )
    )
    assert aligned is not None and split is not None
    assert aligned.risk_multiplier <= 1.0
    assert split.risk_multiplier < aligned.risk_multiplier
    assert split.disagreement > aligned.disagreement


def test_ai_signal_parser_accepts_structured_provider_output() -> None:
    signal = _parse_signal(
        "openai",
        "test-model",
        {
            "output": [
                {
                    "content": [
                        {
                            "text": '{"probability_up":0.61,"confidence":0.72,"risk_multiplier":0.8,"regime":"trend","event_risk":"low","rationale":"aligned"}'
                        }
                    ]
                }
            ]
        },
        125,
    )
    assert signal.provider == "openai"
    assert signal.probability_up == 0.61
    assert signal.risk_multiplier == 0.8


def test_unconfigured_ai_engine_cleanly_falls_back_to_quant(monkeypatch) -> None:
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    engine = AIAdvisoryEngine(providers=())
    status = engine.status()
    assert status["configured"] is False
    assert status["providers"] == []
    assert status["latest"] is None


def test_provider_configuration_is_key_driven(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic")
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini")
    names = {provider.name for provider in providers_from_env()}
    assert names == {"openai", "anthropic", "gemini"}
