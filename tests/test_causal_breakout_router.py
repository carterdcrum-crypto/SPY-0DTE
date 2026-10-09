"""Synthetic causality checks for free delayed-breakout forecast experiment."""
from dataclasses import replace
from datetime import date,datetime,time,timedelta
from zoneinfo import ZoneInfo

import pytest

from engine.data import HistoricalFrame
from engine.market import MarketSnapshot
from engine.causal_breakout_router import (
    POLICIES,RouterConfig,session_delayed_events,decision,run_research,_summarize,
)

ET=ZoneInfo("America/New_York")


def fake_day(day=date(2026,8,12),*,mode="follow",poison_after_entry=False,gap=False):
    start=datetime.combine(day,time(9,30),ET)
    frames=[]
    for i in range(66):
        if i<30:
            spot=700+i*.005
        elif i==30:
            spot=700.15
        elif i==31:
            spot=700.145
        elif i==32:
            spot=700.65
        elif i<=35:
            spot=700.68 if mode=="follow" else 699.8
        elif poison_after_entry and i>=37:
            spot=698.0
        elif mode=="follow":
            spot=700.68+(i-35)*.03
        else:
            spot=699.8-(i-35)*.015
        ts=start+timedelta(minutes=i+(1 if gap and i>=34 else 0))
        frames.append(HistoricalFrame(timestamp=ts,options=(),market=MarketSnapshot(
            spot=spot,bid=spot-.02,ask=spot+.02,realized_volatility=.15,
            implied_volatility=.2,volume_ratio=2 if i==32 else 1,
            minutes_to_close=390-i,
        )))
    return frames


def sample(frames):
    events=session_delayed_events(frames)
    assert events
    return next(e for e in events if abs(e.signal_spot-700.65)<.0001)


def test_signal_future_only_used_after_the_confirmation_and_entry_clock():
    first=sample(fake_day())
    flipped=sample(fake_day(poison_after_entry=True))
    assert first.signal_at==flipped.signal_at
    assert first.confirmation_at==flipped.confirmation_at
    assert first.proxy_entry_at==flipped.proxy_entry_at
    assert first.proxy_exit_at==flipped.proxy_exit_at
    assert first.continuation_accepted
    assert first.failed_break_fade_accepted is False
    assert first.delayed_raw_breakout_direction_signed_spy_bps>0
    assert flipped.delayed_raw_breakout_direction_signed_spy_bps<0
    for name in POLICIES:
        assert decision(first,name)==decision(flipped,name)
    # The confirmed route is decided at the end of bar i+3, entry
    # uses next COMPLETED bar i+4 and outcome 10 minutes after.
    start=fake_day()[32].timestamp
    assert first.confirmation_at==(start+timedelta(minutes=4)).isoformat()
    assert first.proxy_entry_at==(start+timedelta(minutes=5)).isoformat()
    assert first.proxy_exit_at==(start+timedelta(minutes=15)).isoformat()


def test_future_snapback_at_confirmation_changes_signal_not_before():
    a=sample(fake_day(mode="follow"))
    b=sample(fake_day(mode="snapback"))
    assert a.signal_spot==b.signal_spot
    assert a.original_30min_trend_signed_bps==b.original_30min_trend_signed_bps
    assert b.observed_first3_snapback
    assert a.continuation_accepted and not b.continuation_accepted
    assert decision(b,"delayed_baseline_all")==1


def test_fade_requires_weak_prior_trend_and_actual_three_min_rejection():
    a=sample(fake_day(mode="snapback"))
    assert a.confirm_extension_signed_bps<0
    assert a.original_30min_trend_signed_bps>8
    assert not a.failed_break_fade_accepted
    # Test a separate precalculated causal weak prior trend case.
    eligible=replace(a,failed_break_fade_accepted=True)
    assert decision(eligible,"confirmed_failed_break_fade")==-1
    assert decision(eligible,"conditional_router")==-1
    assert decision(a,"confirmed_failed_break_fade")==0


def test_filter_never_accesses_any_after_entry_outcome_field():
    a=sample(fake_day())
    b=replace(a,delayed_raw_breakout_direction_signed_spy_bps=-99999)
    for name in POLICIES:
        assert decision(a,name)==decision(b,name)


def test_missing_minute_never_compresses_three_minute_confirmation():
    result=session_delayed_events(fake_day(gap=True))
    assert all(abs(x.signal_spot-700.65)>.0001 for x in result)


def test_invalid_policy_rejected():
    with pytest.raises(ValueError,match="Unknown fixed"):
        decision(sample(fake_day()),"best_cherry_picked_configuration")


def test_same_clock_policy_baselines_zero_trade_and_no_option_pnl():
    day=date(2026,8,12)
    a=sample(fake_day(day))
    for strategy in POLICIES:
        m=_summarize([a],strategy,[day],RouterConfig(bootstrap_reps=25))
        assert m["account_return"] is None
        assert m["account_max_drawdown"] is None
        assert m["actual_options_pnl"] is None
        assert m["original_baseline_opportunities"]==1
        assert m["accepted"]+m["abstained"]==1
    for strategy in ("confirmed_failed_break_fade",):
        m=_summarize([a],strategy,[day],RouterConfig(bootstrap_reps=25))
        assert m["accepted"]==0
        assert m["net_signed_spy_bps_per_calendar_session_including_no_trade"]==0
    b=_summarize([a],"delayed_baseline_all",[day],RouterConfig(bootstrap_reps=25))
    assert b["bootstrap"]["paired_vs_delayed_baseline_spy_bps_95pct_ci"]==[0,0]


def test_research_periods_explicitly_are_not_untouched_holdout():
    samples=[*fake_day(date(2026,4,8)),
             *fake_day(date(2026,8,12)),
             *fake_day(date(2026,9,10),mode="snapback")]
    result=run_research(samples,RouterConfig(bootstrap_reps=20))
    assert result["frames_read"]==66*3
    assert result["sessions_read"]==3
    assert result["no_untouched_holdout_available"] is True
    assert result["requires_future_real_quotes_before_any_0dte_pnl_claim"] is True
    assert set(result["periods"])=={
        "development","validation","previously_studied_diagnostic"}
    for period,all_models in result["periods"].items():
        assert set(all_models)==set(POLICIES)
        assert all(stat["eligible_days"]==1 for stat in all_models.values())
    assert result["actions"]


def test_disconnected_last_10minute_horizon_rejected():
    observed=fake_day()
    # Missing a bar after confirmation invalidates the whole
    # delayed forecast rather than using a shorter horizon.
    truncated=observed[:42]
    assert not any(abs(x.signal_spot-700.65)<.0001
                   for x in session_delayed_events(truncated))
