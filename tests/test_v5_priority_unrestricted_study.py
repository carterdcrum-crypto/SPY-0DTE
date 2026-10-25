"""Causal signal prioritization and drawdown semantics."""
from datetime import datetime, timezone
from types import SimpleNamespace

from engine.v5_priority_unrestricted_study import (
    L, M, V, rank_candidates, true_max_drawdown,
)

AT = datetime(2026, 10, 20, 15, tzinfo=timezone.utc)
def candidate(setup, symbol, score=4, trend=1., efficiency=.3):
    return {
        "entry_at": AT, "quote": SimpleNamespace(symbol=symbol),
        "score": score, "signal": {
            "setup": setup, "trend_bps": trend, "efficiency": efficiency,
        },
    }


def test_liquidity_priority_reorders_simultaneous_signals_only():
    entries = [candidate(M, "AAAA"), candidate(V, "BBBB"),
               candidate(L, "CCCC")]
    ordered = rank_candidates(entries, {"order": (L, V, M)})
    assert [x["signal"]["setup"] for x in ordered] == [L, V, M]


def test_quality_prefers_score_not_alphabetical_contract():
    entries = [candidate(M, "AAAA", score=2),
               candidate(V, "ZZZZ", score=5)]
    assert rank_candidates(entries, {"order": "quality"})[0]["score"] == 5


def test_regime_switches_using_asof_efficiency_and_trend():
    entries = [candidate(M, "AAAA", trend=7, efficiency=.6),
               candidate(L, "BBBB", trend=7, efficiency=.6)]
    assert rank_candidates(entries, {"order": "regime"})[0]["signal"]["setup"] == M
    entries = [candidate(M, "AAAA", trend=2, efficiency=.2),
               candidate(L, "BBBB", trend=2, efficiency=.2)]
    assert rank_candidates(entries, {"order": "regime"})[0]["signal"]["setup"] == L


def test_time_order_precedes_priority_no_lookahead():
    early = candidate(M, "AAAA")
    late = candidate(L, "ZZZZ")
    late["entry_at"] = datetime(2026, 10, 20, 15, 1, tzinfo=timezone.utc)
    assert rank_candidates([late, early], {"order": (L, M, V)})[0] is early


def test_true_percentage_drawdown_uses_worst_fraction_not_worst_dollars():
    # Worst dollar fall = 500 on peak 2000 (25%); other peak drawdown 50%
    # from 1000 to 500 is the maximum *percentage*, despite fewer dollars.
    days = [{"day":"1", "equity_usd":500},
            {"day":"2", "equity_usd":2000},
            {"day":"3", "equity_usd":1500}]
    x = true_max_drawdown(days, 1000)
    assert x["max_eod_drawdown_pct"] == 50
    assert x["trough_day"] == "1"
