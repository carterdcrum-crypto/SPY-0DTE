from __future__ import annotations

from engine.adaptive_weighting import AdaptiveQuantAIWeight, AdaptiveWeightConfig, replay_adaptive_weights


def _rows(*, quant: float, ai: float, outcome: float, count: int):
    return tuple(
        {
            "quant_probability_up": quant,
            "ai_probability_up": ai,
            "outcome_up": outcome,
        }
        for _ in range(count)
    )


def test_quant_keeps_strict_majority_even_when_ai_is_better() -> None:
    result = replay_adaptive_weights(
        _rows(quant=0.55, ai=0.90, outcome=1.0, count=80),
        config=AdaptiveWeightConfig(warmup_samples=4, shrinkage_samples=4.0),
    )
    assert result.final_ai_weight <= 0.45
    assert result.final_ai_weight < 0.5


def test_ai_weight_falls_when_quant_consistently_outperforms() -> None:
    result = replay_adaptive_weights(
        _rows(quant=0.90, ai=0.55, outcome=1.0, count=80),
        config=AdaptiveWeightConfig(warmup_samples=4, shrinkage_samples=4.0),
    )
    assert result.final_ai_weight < 0.30
    assert result.adaptive_brier is not None
    assert result.fixed_45_brier is not None
    assert result.adaptive_brier < result.fixed_45_brier


def test_current_outcome_cannot_change_its_own_weight() -> None:
    config = AdaptiveWeightConfig(warmup_samples=0, shrinkage_samples=0.0)
    learner_a = AdaptiveQuantAIWeight(config)
    learner_b = AdaptiveQuantAIWeight(config)

    for _ in range(20):
        learner_a.observe(quant_probability_up=0.8, ai_probability_up=0.55, outcome_up=1.0)
        learner_b.observe(quant_probability_up=0.8, ai_probability_up=0.55, outcome_up=1.0)

    before_a = learner_a.current_ai_weight
    before_b = learner_b.current_ai_weight
    assert before_a == before_b

    # The next forecast would use `before_*`. Only after its outcome resolves is
    # the weight allowed to change for a later forecast.
    learner_a.observe(quant_probability_up=0.8, ai_probability_up=0.2, outcome_up=0.0)
    learner_b.observe(quant_probability_up=0.8, ai_probability_up=0.2, outcome_up=1.0)
    assert learner_a.current_ai_weight != learner_b.current_ai_weight


def test_replay_reports_all_comparators() -> None:
    rows = (
        *_rows(quant=0.75, ai=0.60, outcome=1.0, count=30),
        *_rows(quant=0.25, ai=0.40, outcome=0.0, count=30),
    )
    result = replay_adaptive_weights(rows)
    payload = result.as_dict()
    assert payload["samples"] == 60
    assert payload["quant_brier"] is not None
    assert payload["ai_brier"] is not None
    assert payload["fixed_45_brier"] is not None
    assert payload["adaptive_brier"] is not None
    assert payload["mean_ai_weight"] is not None
    assert 0.05 <= float(payload["final_ai_weight"]) <= 0.45
