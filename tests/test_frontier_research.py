from __future__ import annotations

from engine.frontier_research import FrontierPoint, default_regime_candidates, pareto_frontier


def _point(name: str, *, ret: float, dd: float, accepted: bool = True) -> FrontierPoint:
    return FrontierPoint(
        candidate=name,
        drawdown_budget=0.20,
        starting_cash=1000.0,
        accepted=accepted,
        score=1.0,
        worst_tick=1,
        zero_tick_return=ret,
        zero_tick_drawdown=dd,
        one_tick_return=ret,
        one_tick_drawdown=dd,
        two_tick_return=max(0.01, ret / 2),
        two_tick_drawdown=min(0.99, dd * 1.2),
        reason="",
    )


def test_default_frontier_candidates_are_small_and_predeclared():
    candidates = default_regime_candidates()
    assert 3 <= len(candidates) <= 8
    assert len({candidate.name for candidate in candidates}) == len(candidates)


def test_pareto_frontier_removes_dominated_and_rejected_points():
    points = (
        _point("A", ret=0.30, dd=0.10),
        _point("B", ret=0.20, dd=0.15),
        _point("C", ret=0.25, dd=0.05),
        _point("REJECT", ret=1.00, dd=0.01, accepted=False),
    )

    frontier = pareto_frontier(points)
    names = {point.candidate for point in frontier}
    assert names == {"A", "C"}
