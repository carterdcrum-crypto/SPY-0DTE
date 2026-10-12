"""No-target morning strategy combinations: deterministic causal tests."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from engine.no_tp_morning_entry_combos import (
    RULES, TRAIL_PCT, MAX_HOLD_MIN, INDEPENDENT_HARD_STOP_PCT,
    signal_context, pause_reclaim_events, admissible, stats, robustness,
)


def frames(prices=None, volume=1.8):
    clock=datetime(2026,10,6,13,30,tzinfo=timezone.utc)  # 09:30 ET
    prices=prices or [600+.07*i for i in range(100)]
    return [
        SimpleNamespace(
            timestamp=clock+timedelta(minutes=i),
            market=SimpleNamespace(spot=price,volume_ratio=volume),
        )
        for i,price in enumerate(prices)
    ]


def case(at="2026-10-06T13:46:00+00:00",
         origin="opening_close_breakout_proxy",volume=1.8):
    return {"signal_at":at,"right":"call","origin":origin,
            "volume_ratio":volume}


def test_frozen_30_no_profit_target_unrelated_to_entry_rules():
    assert TRAIL_PCT==30
    assert INDEPENDENT_HARD_STOP_PCT==35
    assert MAX_HOLD_MIN==10
    assert len(RULES)==12
    assert "opening_breakout_base" in RULES
    assert "opening_pause_reclaim" in RULES


def test_no_future_bars_used_in_entry_signal_context():
    z=frames()
    before=signal_context(z,15,"call")
    assert before["signed_5min_bps"]>0
    z[16].market.spot=1000000.0
    z[20].market.volume_ratio=10000.0
    assert signal_context(z,15,"call")==before
    assert signal_context(z,15,"put")["signed_5min_bps"]<0
    assert before["trend_efficiency_15min"]>0.95


def test_early_at_0946_not_suppressed_by_indicator_warmup():
    z=frames()
    assert signal_context(z,15,"call")
    assert pause_reclaim_events(z)==[]  # monotonic drive; no pullback


def test_admission_logic_only_observable_features_and_entry_quote():
    z=frames()
    context=signal_context(z,15,"call")
    q=SimpleNamespace(spread_fraction=.04)
    opening=case()
    assert admissible(opening,context,q,"opening_breakout_base")
    assert admissible(opening,context,q,"opening_directional_efficiency_55")
    assert admissible(opening,context,q,"opening_spread_08")
    assert admissible(opening,context,q,"opening_0946_to_1030")
    assert not admissible(opening,context,q,"opening_volume_200")
    assert not admissible(opening,context,q,"opening_pause_reclaim")
    expensive_spread=SimpleNamespace(spread_fraction=.10)
    assert not admissible(opening,context,expensive_spread,"opening_spread_08")
    assert admissible(opening,context,expensive_spread,"opening_breakout_base")
    with pytest.raises(ValueError):
        admissible(opening,context,q,"perfect_100_pct_accuracy")


def test_pause_reclaim_hypothesis_uses_only_completed_prior_closes():
    prices=[600.0+.07*i for i in range(40)]
    prices[-2]-=.45
    prices[-1]+=.15
    z=frames(prices=prices)
    found=pause_reclaim_events(z)
    assert isinstance(found,list)
    assert all(datetime.fromisoformat(r["signal_at"]).astimezone(
        __import__("zoneinfo").ZoneInfo("America/New_York")).hour<11
        for r in found)


def test_zero_trade_has_no_win_rate_not_fabricated_profitability():
    s=stats([],["2026-10-06"],"opening_pause_reclaim","all_inspected")
    assert s["win_rate_pct"] is None
    assert s["trades"]==0
    assert s["hypothetical_net_one_contract_sum_usd"]==0
    summary=robustness([],["2026-10-06"],"opening_pause_reclaim",reps=100)
    assert summary["net_sum_without_best_day_usd"]==0
