"""Strictly completed-session training and proper-score regression controls."""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from engine.data import HistoricalFrame
from engine.market import MarketSnapshot
from engine.magnitude_edge_study import MagnitudeObservation
from engine.joint_probability_audit import (
    JointPrediction,TARGETS,PRIOR_COMPLETE_SESSIONS,MIN_TRAINING_EVENTS,
    _laplace_prob,_probability_pair,forecast_day,_metrics,run_study,brier,
)
from engine.causal_breakout_router import DelayedEvent

ET=ZoneInfo("America/New_York")


def synthetic_event(day:date=date(2026,8,12), signed:float=8.,
                    continuation:bool=True, high:bool=True)->MagnitudeObservation:
    when=datetime.combine(day,time(10,8),ET).isoformat()
    e=DelayedEvent(
        session=day.isoformat(),original_direction="call",
        signal_at=when,confirmation_at=when,proxy_entry_at=when,
        proxy_exit_at=when,signal_spot=700.,
        previous_15_close_boundary=699.9,three_min_confirm_spot=700.2,
        original_at_signal_volume_ratio=2.,original_30min_trend_signed_bps=10.,
        original_15close_efficiency=.5,confirm_extension_signed_bps=3.,
        confirm_change_3min_signed_bps=2.,observed_first3_snapback=False,
        continuation_accepted=continuation,failed_break_fade_accepted=False,
        delayed_raw_breakout_direction_signed_spy_bps=signed,
    )
    v=7. if high else 3.
    return MagnitudeObservation(
        original=e,forecast_at=when,
        spot_30min_standard_deviation_one_minute_bps=v/2.5,
        forecast_expected_abs_spy_10min_bps=v,
        observed_absolute_spy_10min_bps=abs(signed),
        observed_signed_spy_10min_bps=signed,
        historical_bucket="predicted_high_ge_5bp" if high else "predicted_low_below_5bp",
        later_abs_move_gt_5bps=abs(signed)>5,
        later_signed_move_gt_2bps=signed>2,
        later_signed_move_gt_5bps=signed>5,
    )


def sample_history(successes=200,losers=200):
    # Full trading sessions, 10 outcomes per day, plenty to score.
    hist=[]
    for i in range(40):
        values=[]
        for j in range(10):
            event=synthetic_event(signed=8 if i*10+j<successes else -9,
                                  continuation=(j%2==0),
                                  high=(j%3==0))
            values.append(event)
        hist.append(values)
    return hist


def fake_spy_day(day:date,negative:bool=False):
    first=datetime.combine(day,time(9,30),ET)
    out=[]
    for i in range(66):
        if i<30:spot=700+i*.005
        elif i==30:spot=700.15
        elif i==31:spot=700.145
        elif i==32:spot=700.65
        elif i<=35:spot=700.68
        elif negative and i>=37:spot=699.80
        else:spot=700.68+(i-35)*.03
        t=first+timedelta(minutes=i)
        out.append(HistoricalFrame(timestamp=t,options=(),market=MarketSnapshot(
            spot=spot,bid=spot-.01,ask=spot+.01,
            realized_volatility=.15,implied_volatility=.2,
            volume_ratio=2 if i==32 else 1,
            minutes_to_close=390-i)))
    return out


def test_laplace_and_shrunk_probabilities_stay_valid_and_monotonic():
    prior=[synthetic_event(signed=9),synthetic_event(signed=-12)]
    probs=_laplace_prob(prior)
    assert probs[:3]==pytest.approx((.5,.5,.5))
    assert probs[3]==pytest.approx(.5)
    base,local,n,mean,conditional_mean=_probability_pair(prior,prior[0])
    assert base==probs and n==2
    assert all(0<p<1 for p in local)
    assert local[0]>=local[1]>=local[2]


def test_future_label_changes_cannot_change_current_prediction():
    prior=sample_history()
    today=synthetic_event(signed=-20)
    a=forecast_day(prior,[today],date(2026,8,12))
    altered=replace(today,observed_signed_spy_10min_bps=999,
                    actual_future_UNUSED_LABEL="test") if False else replace(
                        today,observed_signed_spy_10min_bps=999,
                        observed_absolute_spy_10min_bps=999,
                        later_signed_move_gt_2bps=True,later_signed_move_gt_5bps=True)
    b=forecast_day(prior,[altered],date(2026,8,12))
    assert len(a)==len(b)==1
    assert a[0].past_base_probs==b[0].past_base_probs
    assert a[0].past_conditional_probs==b[0].past_conditional_probs
    assert a[0].prior_event_count==400
    assert a[0].actual_gt2 is False
    assert b[0].actual_gt2 is True
    assert a[0].past_conditional_expected_signed_spy_bps==b[0].past_conditional_expected_signed_spy_bps


def test_not_enough_prior_sessions_or_trades_never_backfills_future():
    ev=synthetic_event()
    prior=sample_history()
    assert not forecast_day(prior[:39],[ev],date(2026,8,12))
    assert not forecast_day([[] for _ in range(40)],[ev],date(2026,8,12))
    assert len(forecast_day(prior,[ev],date(2026,8,12)))==1
    assert MIN_TRAINING_EVENTS==100


def test_60_session_window_ignores_older_sessions_and_day_outcomes():
    good=sample_history()
    more=[good[0]]*PRIOR_COMPLETE_SESSIONS
    obs=synthetic_event()
    a=forecast_day(more,[obs],date(2026,8,12))
    b=forecast_day([[synthetic_event(signed=-999)]]*10+more,
                   [obs],date(2026,8,12))
    assert a[0].past_base_probs==b[0].past_base_probs
    assert a[0].past_conditional_probs==b[0].past_conditional_probs
    assert a[0].prior_complete_days==60


def test_brier_proper_scoring_and_no_account_pnl():
    assert brier(.9,True)==pytest.approx(.01)
    assert brier(.1,False)==pytest.approx(.01)
    with pytest.raises(ValueError):
        brier(2.0,True)
    prior=sample_history()
    record=forecast_day(prior,[synthetic_event()],date(2026,8,12))[0]
    summary=_metrics([record],["2026-08-12","2026-08-13"])
    assert summary["eligible_predictions"]==1
    assert summary["calendar_days"]==2
    assert summary["option_PnL"] is None
    assert summary["account_return"] is None
    assert summary["autonomous_trades_created"]==0
    assert summary["gt5"]["day_block_delta_ci"]["days_including_no_signal"]==2
    assert summary["gt2"]["day_block_delta_ci"]["days_with_predictions"]==1


def test_full_replay_requires_warmup_and_does_not_trade():
    days=[date(2026,4,1)+timedelta(days=i) for i in range(45)]
    frames=[]
    for i,day in enumerate(days):
        frames.extend(fake_spy_day(day,negative=(i%4==0)))
    result=run_study(frames)
    assert result["minutes"]==len(frames)
    assert result["sessions"]==len(days)
    assert result["reused_historical_data_not_untouched_holdout"] is True
    assert result["adaptive_signal_filter_enabled"] is False
    assert result["live_auto_trade_modified"] is False
    assert result["new_data_purchased_usd"]==0
    assert result["signal_ledger"]
    assert all(pred.prior_complete_days>=40 for pred in result["signal_ledger"])
    assert result["reports"]["development"]["eligible_predictions"]>0
    for period,report in result["reports"].items():
        assert report["options_fills"] is None
        assert report["account_max_drawdown"] is None
        for target in TARGETS:
            assert "baseline_brier" in report[target]


def test_reject_nonchronological_sessions():
    day_a=fake_spy_day(date(2026,8,12))
    day_b=fake_spy_day(date(2026,8,11))
    with pytest.raises(ValueError,match="chronological"):
        run_study([*day_a,*day_b])
