from __future__ import annotations

from datetime import datetime, timedelta, timezone

from engine.ai_validation import AIValidationStore


def _record(store: AIValidationStore, when: datetime, *, quant: float, ai: float, hybrid: float) -> None:
    inserted = store.record(
        market_cycle=when,
        horizon_seconds=60.0,
        spot=100.0,
        quant_probability_up=quant,
        ai_probability_up=ai,
        hybrid_probability_up=hybrid,
        effective_ai_weight=0.30,
        ai_confidence=0.90,
        ai_disagreement=0.02,
        quant_action="call",
        old_veto_action="call",
        hybrid_action="call",
        providers=("openai", "gemini"),
    )
    assert inserted is True


def test_validation_does_not_resolve_before_horizon(tmp_path) -> None:
    store = AIValidationStore(tmp_path / "paper.sqlite")
    start = datetime(2026, 9, 25, 14, 0, tzinfo=timezone.utc)
    _record(store, start, quant=0.60, ai=0.80, hybrid=0.72)

    assert store.resolve_due(market_cycle=start + timedelta(seconds=59), spot=101.0) == 0
    assert store.summary()["resolved_samples"] == 0
    assert store.resolve_due(market_cycle=start + timedelta(seconds=61), spot=101.0) == 1
    assert store.summary()["resolved_samples"] == 1


def test_hybrid_brier_and_signal_summary_are_paired(tmp_path) -> None:
    store = AIValidationStore(tmp_path / "paper.sqlite")
    start = datetime(2026, 9, 25, 14, 0, tzinfo=timezone.utc)

    for index in range(6):
        when = start + timedelta(minutes=2 * index)
        _record(store, when, quant=0.60, ai=0.90, hybrid=0.80)
        store.resolve_due(
            market_cycle=when + timedelta(seconds=61),
            spot=101.0,
        )

    summary = store.summary()
    assert summary["resolved_samples"] == 6
    assert float(summary["hybrid_brier"]) < float(summary["quant_brier"])
    assert float(summary["hybrid_brier_improvement_vs_quant"]) > 0.0
    assert summary["hybrid_signals"] == 6
    assert summary["hybrid_signal_precision"] == 1.0
    assert summary["old_veto_signal_precision"] == 1.0

    ablation = store.paired_ablation()
    assert ablation is not None
    assert float(ablation["delta_quant_minus_hybrid"]) > 0.0
    assert ablation["samples"] == 6


def test_duplicate_market_cycle_is_not_double_counted(tmp_path) -> None:
    store = AIValidationStore(tmp_path / "paper.sqlite")
    start = datetime(2026, 9, 25, 14, 0, tzinfo=timezone.utc)
    _record(store, start, quant=0.60, ai=0.80, hybrid=0.72)

    duplicate = store.record(
        market_cycle=start,
        horizon_seconds=60.0,
        spot=100.0,
        quant_probability_up=0.55,
        ai_probability_up=0.55,
        hybrid_probability_up=0.55,
        effective_ai_weight=0.10,
        ai_confidence=0.50,
        ai_disagreement=0.20,
        quant_action="call",
        old_veto_action="flat",
        hybrid_action="call",
        providers=("openai",),
    )
    assert duplicate is False
    store.resolve_due(market_cycle=start + timedelta(seconds=61), spot=101.0)
    assert store.summary()["resolved_samples"] == 1
