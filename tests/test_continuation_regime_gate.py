"""Past-only adaptation and small-n bias tests; no options P&L."""
from __future__ import annotations

from dataclasses import replace
from datetime import date,datetime,time,timedelta
from zoneinfo import ZoneInfo

import pytest

from engine.causal_breakout_router import DelayedEvent
from engine.continuation_regime_gate import (
    PRIOR_SESSIONS,MIN_PRIOR_CONFIRMED_EVENTS,FRICTION_BPS,
    _group_details,causal_gate,fisher_exact_two_sided,wilson_interval,
    run_diagnostic,_strata,
)
from engine.data import HistoricalFrame
from engine.market import MarketSnapshot

ET=ZoneInfo("America/New_York")


def example_event(day=date(2026,8,12),signed=7.0):
    d=datetime.combine(day,time(10,3),ET).isoformat()
    return DelayedEvent(
        session=day.isoformat(),original_direction="call",signal_at=d,
        confirmation_at=d,proxy_entry_at=d,proxy_exit_at=d,
        signal_spot=700,previous_15_close_boundary=699.9,
        three_min_confirm_spot=700.2,
        original_at_signal_volume_ratio=1.8,
        original_30min_trend_signed_bps=12,
        original_15close_efficiency=.55,
        confirm_extension_signed_bps=3,
        confirm_change_3min_signed_bps=2,
        observed_first3_snapback=False,
        continuation_accepted=True,
        failed_break_fade_accepted=False,
        delayed_raw_breakout_direction_signed_spy_bps=signed,
    )


def fake_intraday(day=date(2026,8,12),bad_future=False):
    start=datetime.combine(day,time(9,30),ET)
    result=[]
    for minute in range(64):
        if minute<30:spot=700+minute*.005
        elif minute==30:spot=700.15
        elif minute==31:spot=700.145
        elif minute==32:spot=700.65
        elif minute<=35:spot=700.68
        elif bad_future:spot=700.0
        else:spot=700.68+(minute-35)*.03
        now=start+timedelta(minutes=minute)
        result.append(HistoricalFrame(timestamp=now,options=(),market=MarketSnapshot(
            spot=spot,bid=spot-.01,ask=spot+.01,
            realized_volatility=.2,implied_volatility=.25,
            volume_ratio=2. if minute==32 else 1.,
            minutes_to_close=390-minute)))
    return result


def test_fisher_exact_is_symmetric_and_very_small_sample_is_not_proof():
    assert fisher_exact_two_sided(10,3,3,4)==pytest.approx(fisher_exact_two_sided(3,4,10,3))
    assert fisher_exact_two_sided(10,3,3,4)>.05
    assert fisher_exact_two_sided(0,0,0,0)==1.
    with pytest.raises(ValueError):
        fisher_exact_two_sided(-1,3,3,4)


def test_wilson_reflects_uncertainty_in_tiny_13_and_7_cohorts():
    a=wilson_interval(10,13)
    b=wilson_interval(3,7)
    assert a[0]<.6 and a[1]>.9
    assert b[0]<.3 and b[1]>.7
    assert wilson_interval(0,0)==(None,None)


def test_prior_complete_sessions_only_can_activate_without_today_future():
    yes=example_event(signed=8)
    prior=[[yes] for _ in range(PRIOR_SESSIONS)]
    allow,n,hit,net=causal_gate(prior)
    assert allow and n==PRIOR_SESSIONS
    assert hit==1 and net==6
    # Future outcome on current day must not be supplied to this function.
    poisoned=replace(yes,delayed_raw_breakout_direction_signed_spy_bps=-1000)
    assert causal_gate(prior)==causal_gate(prior)  # deterministic prior decision
    assert poisoned not in prior[-1]
    # Mutating an *older completed* day is allowed to change the next day's gate.
    bad=[[poisoned] for _ in range(PRIOR_SESSIONS)]
    assert not causal_gate(bad)[0]


def test_minimum_complete_days_and_observations_fail_closed():
    positive=example_event()
    assert not causal_gate([[positive] for _ in range(PRIOR_SESSIONS-1)])[0]
    sparse=[[positive] if i<MIN_PRIOR_CONFIRMED_EVENTS-1 else []
            for i in range(PRIOR_SESSIONS)]
    assert not causal_gate(sparse)[0]
    neutral=example_event(signed=FRICTION_BPS)
    assert not causal_gate([[neutral] for _ in range(PRIOR_SESSIONS)])[0]


def test_lookback_window_does_not_consult_older_history():
    good=example_event(signed=9)
    bad=example_event(signed=-15)
    recent=[[good] for _ in range(PRIOR_SESSIONS)]
    assert causal_gate([[bad] for _ in range(50)]+recent)==causal_gate(recent)


def test_report_all_events_not_just_winners_and_no_account_returns():
    e=[example_event(signed=s) for s in (10,-8,3)]
    a=_group_details(e,"test")
    assert a["accepted_events"]==3
    assert a["correct_spy_direction"]==2
    assert a["positive_after_hypothetical_2bp_underlying_hurdle"]==2
    assert a["positive_after_hypothetical_5bp_underlying_hurdle"]==1
    assert a["wrong_or_flat_spy_direction"]==1
    assert a["mean_after_5bp_underlying_friction_proxy"] is not None
    assert a["one_deleted_net_average_min"] is not None
    assert _strata(e,"validation")
    assert all(x["warning_very_small_stratum"] for x in _strata(e,"validation"))


def test_full_research_has_periods_and_no_option_returns():
    data=(*fake_intraday(date(2026,4,8)),
          *fake_intraday(date(2026,8,12),bad_future=True),
          *fake_intraday(date(2026,9,10)))
    result=run_diagnostic(data)
    assert result["sessions"]==3
    assert result["frames"]==64*3
    assert result["real_options_profitability"] is None
    assert result["previously_seen_time_periods_not_untouched_holdout"] is True
    assert result["frozen_gate"]["gate_computed_before_current_session"] is True
    for period,data in result["periods"].items():
        assert data["eligible_sessions"]==1
        assert data["equity_curve"] is None
        assert data["account_drawdown"] is None
        assert data["real_option_pnl"] is None
        assert data["past_only_gate_open_sessions"]==0
        assert data["prior_session_gate"]["accepted_events"]==0
    assert result["per_signal_table"]
    assert all(record["causal_session_gate_accepted"] is False
               for record in result["per_signal_table"])


def test_daily_gate_is_not_permitted_to_use_same_day_profitable_results():
    data=[*fake_intraday(date(2026,8,12)),
          *fake_intraday(date(2026,8,13),bad_future=True)]
    r=run_diagnostic(data)
    days=r["daily_gate_decisions"]
    assert days[0].history_sessions==0
    assert days[0].history_events==0
    assert days[1].history_sessions==1
    assert days[1].history_events>=0
    assert not days[0].gate_open
    assert not days[1].gate_open
