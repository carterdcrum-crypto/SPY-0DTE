"""Weighted multiple-setup intraday opportunities and honest day coverage."""
from __future__ import annotations

from dataclasses import replace
from datetime import date,datetime,time,timedelta
from zoneinfo import ZoneInfo

import pytest

from engine.data import HistoricalFrame
from engine.market import MarketSnapshot
from engine.weighted_daily_coverage import (
    FAMILIES,POLICIES,Opportunity,decision,session_opportunities,
    select_nonoverlapping,_summary,run_study,
)

ET=ZoneInfo("America/New_York")


def frames(day=date(2026,8,12),*,reverse_future=False):
    first=datetime.combine(day,time(9,30),ET)
    out=[]
    for i in range(181):
        if i<90:
            spot=700+(.001 if i%2 else 0.)
        elif i==90:
            spot=700.7
        elif i<95:
            spot=700.72
        elif reverse_future and i>=96:
            spot=699.2
        else:
            spot=700.72+(i-94)*.005
        now=first+timedelta(minutes=i)
        out.append(HistoricalFrame(timestamp=now,options=(),
            market=MarketSnapshot(
                spot=spot,bid=spot-.01,ask=spot+.01,
                realized_volatility=.2,implied_volatility=.2,
                volume_ratio=1.8 if i==90 else 1.,
                minutes_to_close=390-i,
            )))
    return out


def synthetic_op(day="2026-08-12",minute=50,setup="close_breakout_baseline",
                 score=6,ret=6.0)->Opportunity:
    start=datetime.fromisoformat(day+"T10:00:00-04:00")+timedelta(minutes=minute)
    return Opportunity(
        day=day,setup=setup,original_direction="call",
        signal_at=start.isoformat(),
        entry_proxy_at=(start+timedelta(minutes=1)).isoformat(),
        exit_proxy_at=(start+timedelta(minutes=11)).isoformat(),
        rsi14=60.,sma20=700.,sma50=699.5,macd=.2,macd_signal=.1,
        volume_ratio_at_signal=1.7,
        recent_macd_crossover_points=2,rsi_points=2,sma_points=2,
        price_action_points=0,volume_proxy_points=0,
        weighted_total=score,
        signed_spy_ten_minute_close_change_bps=ret,
    )


def test_score_and_outcome_separation_no_future_leak():
    x=synthetic_op(score=5,ret=200.)
    y=replace(x,signed_spy_ten_minute_close_change_bps=-300.)
    assert [decision(x,model) for model in POLICIES]==[
        decision(y,model) for model in POLICIES]
    assert decision(x,"weighted_score_ge4")
    assert not decision(x,"weighted_score_ge6")
    with pytest.raises(ValueError):
        decision(x,"always_daily_to_fake_profits")


def test_close_price_indicators_fixed_when_future_price_changes():
    a=session_opportunities(frames())
    b=session_opportunities(frames(reverse_future=True))
    when=(frames()[90].timestamp+timedelta(minutes=1)).isoformat()
    ea=next(x for x in a if x.signal_at==when)
    eb=next(x for x in b if x.signal_at==when)
    for field in ("rsi14","sma20","sma50","macd","macd_signal",
                  "recent_macd_crossover_points","rsi_points","sma_points",
                  "price_action_points","volume_proxy_points","weighted_total"):
        assert getattr(ea,field)==getattr(eb,field)
    assert ea.signed_spy_ten_minute_close_change_bps>0
    assert eb.signed_spy_ten_minute_close_change_bps<0


def test_three_families_are_included_and_shared_lock_prevents_overlap():
    # One clock across multiple independent families; adjacent entries
    # that overlap the first 10m return are not double-counted.
    a=synthetic_op(minute=50,setup=FAMILIES[0])
    b=synthetic_op(minute=51,setup=FAMILIES[1])
    c=synthetic_op(minute=60,setup=FAMILIES[2])
    d=synthetic_op(minute=63,setup=FAMILIES[2])
    out=select_nonoverlapping([b,c,d,a],"all_setups_nonoverlapping")
    assert out==[a,d]
    # A rejected candidate must NOT block a later one.
    rejected=replace(a,weighted_total=3)
    out=select_nonoverlapping([rejected,b,d],"weighted_score_ge4")
    assert out==[b,d]


def test_negative_and_no_signal_days_are_explicit_and_zero_is_not_profit():
    day1="2026-08-12"
    day2="2026-08-13"
    day3="2026-08-14"
    a=synthetic_op(day1,ret=7)
    b=synthetic_op(day2,ret=-7)
    x=_summary([day1,day2,day3],[a,b],"all_setups_nonoverlapping")
    assert x["selected_signals"]==2
    assert x["days_positive_signed_spy_bps_after_2bp_proxy"]==1
    assert x["days_negative_signed_spy_bps_after_2bp_proxy"]==1
    assert x["days_zero_signed_spy_bps_after_2bp_proxy"]==1
    assert x["days_no_signals"]==1
    assert x["mean_signed_spy_bps_per_market_day_after_hypothetical_2bp"]==pytest.approx(-2/3,abs=.0001)
    assert x["actual_SPY_0DTE_options_net_pnl"] is None
    assert x["account_max_drawdown"] is None
    assert x["account_pnl"] is None


def test_all_strict_gates_may_abstain_every_day_without_fabricated_edge():
    d="2026-08-12"
    x=synthetic_op(d,score=0,ret=500)
    stats=_summary([d,"2026-08-13"],[x],"weighted_score_ge6")
    assert stats["selected_signals"]==0
    assert stats["days_no_signals"]==2
    assert stats["days_positive_signed_spy_bps_after_2bp_proxy"]==0
    assert stats["mean_signed_spy_bps_per_market_day_after_hypothetical_2bp"]==0
    assert stats["bootstrap"]["ci95_signed_underlying_bps_per_day"]==[0,0]


def test_stream_three_periods_and_explicit_nonoptions_status():
    xs=[*frames(date(2026,4,8)),
        *frames(date(2026,8,12)),
        *frames(date(2026,9,10))]
    out=run_study(xs)
    assert out["sessions"]==3
    assert out["minute_frames"]==3*181
    assert out["orders_placed"]==0
    assert out["adaptive_trailing_return_gate"] is False
    assert out["already_inspected_dates_NOT_untouched_holdout"] is True
    assert set(out["results"])=={
        "development","validation","previously_studied_diagnostic"}
    for label,models in out["results"].items():
        assert set(models)==set(POLICIES)
        for model,m in models.items():
            assert m["calendar_sessions"]==1
            assert m["selected_signals"]>=0
            assert m["actual_SPY_0DTE_options_net_pnl"] is None


def test_reject_out_of_order_sessions():
    with pytest.raises(ValueError,match="chronological"):
        run_study([*frames(date(2026,9,10)),*frames(date(2026,8,12))])
