from __future__ import annotations

from engine.event_study import EventObservation, _percentile, _summarize


def test_event_study_percentile_is_deterministic():
    assert _percentile((1.0, 2.0, 3.0, 4.0), 0.90) == 3.7


def test_event_summary_uses_signed_directional_returns():
    summary = _summarize(
        (
            EventObservation("BULL", "call", 15, 0.01),
            EventObservation("BULL", "call", 15, -0.005),
            EventObservation("BULL", "call", 15, 0.015),
        )
    )[0]

    assert summary.observations == 3
    assert summary.mean_bps > 0
    assert summary.win_rate == 2 / 3
    assert summary.p10_bps < 0
