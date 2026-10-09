"""Causal SPY magnitude forecasts and prior-session-only expected-edge gates."""
from __future__ import annotations

from dataclasses import replace
from datetime import date,datetime,time,timedelta
from zoneinfo import ZoneInfo

import pytest

from engine.causal_breakout_router import DelayedEvent
from engine.data import HistoricalFrame
from engine.market import MarketSnapshot
from engine.magnitude_edge_study import (
    FRICTION_BPS,MIN_FORECAST_MAGNITUDE_BPS,PRIOR_COMPLETE_SESSIONS,
    MagnitudeObservation,PastEvidence,POLICIES,build_session_observations,
    prior_evidence,choose,run_study,_summary,_bucket,
)
from engine.free_signal_screen import _bps

ET=ZoneInfo("America/New_York")


def trading_day(day:date=date(2026,8,12),*,future_reversal:bool=False,
                high_vol:bool=False):
    start=datetime.combine(day,time(9,30),ET)
    frames=[]
    for i in range(64):
        if i<30:
            value=700+i*.005+(0.1 if high_vol and i%2 else 0)
        elif i==30:
            value=700.15
        elif i==31:
            value=700.145
        elif i==32:
            value=700.65
        elif i<=35:
            value=700.68
        elif future_reversal and i>=37:
            value=699.7
        else:
            value=700.68+(i-35)*.03
        t=start+timedelta(minutes=i)
        frames.append(HistoricalFrame(timestamp=t,options=(),market=MarketSnapshot(
            spot=value,bid=value-.01,ask=value+.01,
            realized_volatility=.2,implied_volatility=.2,
            volume_ratio=2. if i==32 else 1.,
            minutes_to_close=390-i,
        )))
    return frames


def first(frames):
    events=build_session_observations(frames)
    assert events
    return next(x for x in events if abs(x.original.signal_spot-700.65)<.0001)


def prior_example(signed=8.,forecast=9.,cont=True):
    moment=datetime.combine(date(2026,8,12),time(10,7),ET).isoformat()
    ev=DelayedEvent(
        session="2026-08-12",original_direction="call",
        signal_at=moment,confirmation_at=moment,
        proxy_entry_at=moment,proxy_exit_at=moment,
        signal_spot=700.2,previous_15_close_boundary=700.,
        three_min_confirm_spot=700.25,
        original_at_signal_volume_ratio=2.,
        original_30min_trend_signed_bps=9.,
        original_15close_efficiency=.6,
        confirm_extension_signed_bps=3.,
        confirm_change_3min_signed_bps=3.,
        observed_first3_snapback=False,
        continuation_accepted=cont,failed_break_fade_accepted=False,
        delayed_raw_breakout_direction_signed_spy_bps=signed)
    return MagnitudeObservation(
        original=ev,forecast_at=moment,
        spot_30min_standard_deviation_one_minute_bps=forecast/3,
        forecast_expected_abs_spy_10min_bps=forecast,
        observed_absolute_spy_10min_bps=abs(signed),
        observed_signed_spy_10min_bps=signed,
        historical_bucket=_bucket(forecast),
        later_abs_move_gt_5bps=abs(signed)>5,
        later_signed_move_gt_2bps=signed>2,
        later_signed_move_gt_5bps=signed>5)


def test_future_spy_moves_never_alter_as_of_confirmation_features_or_admission():
    a=first(trading_day())
    b=first(trading_day(future_reversal=True))
    assert a.forecast_at==b.forecast_at
    assert a.forecast_expected_abs_spy_10min_bps==b.forecast_expected_abs_spy_10min_bps
    assert a.spot_30min_standard_deviation_one_minute_bps==b.spot_30min_standard_deviation_one_minute_bps
    assert a.observed_signed_spy_10min_bps>0
    assert b.observed_signed_spy_10min_bps<0
    history=[[prior_example()] for _ in range(PRIOR_COMPLETE_SESSIONS)]
    x=prior_evidence(history,a,mode="continuation")
    y=prior_evidence(history,b,mode="continuation")
    z=prior_evidence(history,a,mode="baseline")
    assert x==y
    for rule in POLICIES:
        assert choose(rule,a,z,x)==choose(rule,b,z,y)
        # Directly corrupt future labels but preserve pre-entry features.
        poisoned=replace(a,observed_absolute_spy_10min_bps=9000,
            observed_signed_spy_10min_bps=-9000,
            later_abs_move_gt_5bps=True,later_signed_move_gt_2bps=False,
            later_signed_move_gt_5bps=False)
        assert choose(rule,a,z,x)==choose(rule,poisoned,z,x)


def test_volatility_magnitude_forecast_derived_only_from_last_completed_closes():
    ev=first(trading_day(high_vol=True))
    assert ev.forecast_expected_abs_spy_10min_bps>0
    assert ev.historical_bucket==_bucket(ev.forecast_expected_abs_spy_10min_bps)
    assert ev.observed_absolute_spy_10min_bps==abs(ev.observed_signed_spy_10min_bps)


def test_40_previous_complete_sessions_required_and_positive_lower_bound():
    event=prior_example()
    history=[[prior_example(signed=8.)] for _ in range(PRIOR_COMPLETE_SESSIONS)]
    good=prior_evidence(history,event,mode="continuation")
    assert good.gate_open
    assert good.mean_past_net_spy_bps==pytest.approx(6)
    assert good.lower_bound_past_net_spy_bps==pytest.approx(6)
    assert not prior_evidence(history[:-1],event,mode="continuation").gate_open
    bad=[[prior_example(signed=-15)] for _ in range(PRIOR_COMPLETE_SESSIONS)]
    assert not prior_evidence(bad,event,mode="continuation").gate_open
    weak=replace(event,forecast_expected_abs_spy_10min_bps=3)
    assert not prior_evidence(history,weak,mode="continuation").gate_open
    with pytest.raises(ValueError,match="invalid evidence"):
        prior_evidence(history,event,mode="past_future")


def test_gate_never_copies_predicted_outcome_from_same_day():
    prior=[[prior_example(signed=8)] for _ in range(PRIOR_COMPLETE_SESSIONS)]
    target=prior_example(signed=-1000)
    prior_gate=prior_evidence(prior,target,mode="continuation")
    assert prior_gate.gate_open
    # The CURRENT result has no effect until the *next* complete session.
    assert prior_evidence(prior,replace(target,observed_signed_spy_10min_bps=9999),mode="continuation")==prior_gate
    # Once recorded in a completed prior session it may influence tomorrow.
    past_with_bad=prior[1:]+[[target]]
    assert not prior_evidence(past_with_bad,prior_example(),mode="continuation").gate_open


def test_zero_trades_are_zero_not_fake_returns_or_drawdown():
    a=first(trading_day())
    from engine.magnitude_edge_study import ScoredDecision
    evidence=PastEvidence(0,0,None,None,None,False)
    observations=[ScoredDecision(a,"past40_lowerbound_plus_magnitude",False,
                                 evidence,"validation")]
    stats=_summary(observations,[a.original.session,"2026-08-13"])
    assert stats["accepted"]==0
    assert stats["mean_signed_spy_bps_per_session_after_2bp_incl_abstentions"]==0
    assert stats["account_return"] is None
    assert stats["actual_option_profit"] is None
    assert stats["account_max_drawdown"] is None
    assert stats["bootstrap"]["95pct_daily_signed_spy_bps_after_2bp_proxy"]==[0,0]


def test_full_replay_presents_all_periods_calibration_and_no_option_pnl():
    from engine.magnitude_edge_study import run_study
    rows=[*trading_day(date(2026,4,8)),
          *trading_day(date(2026,8,12),future_reversal=True),
          *trading_day(date(2026,9,10))]
    r=run_study(rows)
    assert r["days_read"]==3
    assert r["minute_frames_read"]==192
    assert r["option_PnL"] is None
    assert r["evidence_weekly_account_100pct"] is False
    assert len(r["decision_ledger"])>=3
    assert set(r["models"])=={
        "development","validation","previously_studied_diagnostic"}
    for period,models in r["models"].items():
        assert set(models)==set(POLICIES)
        assert models["same_clock_delayed_baseline"]["accepted"]>=1
        for policy,model in models.items():
            assert model["account_return"] is None
            assert model["actual_option_profit"] is None
            assert model["accepted"]+model["abstained"]==model["eligible_same_clock_events"]
        assert models["past40_lowerbound_plus_magnitude"]["accepted"]==0
    assert r["calibration"]
