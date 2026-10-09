"""Causality and reporting tests for SPY underlying-only free signal research."""
from dataclasses import replace
from datetime import date,datetime,time,timedelta
from zoneinfo import ZoneInfo

import pytest

from engine.data import HistoricalFrame
from engine.market import MarketSnapshot
from engine.free_signal_screen import (
    ScreenConfig,FAMILIES,_family_signals,screen_session,run_screen,_metrics,
)

ET=ZoneInfo("America/New_York")


def make_frames(day=date(2026,8,12),change_future=False,missing_gap=False):
    first=datetime.combine(day,time(9,30),ET)
    frames=[]
    for i in range(64):
        if i<30:
            spot=700+i*.005
        elif i==32:
            spot=700.50
        elif i>32:
            spot=(700.10 if change_future else 700.5+(i-32)*.006)
        else:
            spot=700.145
        when=first+timedelta(minutes=i+(1 if missing_gap and i>=34 else 0))
        frames.append(HistoricalFrame(timestamp=when,options=(),
            market=MarketSnapshot(spot=spot,bid=spot-.01,ask=spot+.01,
                realized_volatility=.12,implied_volatility=.2,
                volume_ratio=2 if i==32 else 1,
                minutes_to_close=390-i)))
    return frames


def test_close_breakout_event_is_from_past_and_results_use_future_spy_only():
    f=make_frames()
    r=screen_session(f)
    baseline=[x for x in r if x.family=="close_breakout_baseline"]
    guarded=[x for x in r if x.family=="close_breakout_volume"]
    assert baseline and guarded
    e=baseline[0]
    assert e.direction=="call"
    assert e.signal_timestamp==(
        f[32].timestamp+timedelta(minutes=1)).isoformat()
    assert e.proxy_entry_timestamp==(
        f[33].timestamp+timedelta(minutes=1)).isoformat()
    assert e.proxy_exit_timestamp==(
        f[43].timestamp+timedelta(minutes=1)).isoformat()
    assert e.signed_spy_move_bps>0


def test_future_prices_cannot_modify_initial_signal_or_at_signal_features():
    today=make_frames()
    future=make_frames(change_future=True)
    cfg=ScreenConfig()
    for i in (32,):
        original=_family_signals([f.market.spot for f in today[:i+1]],
            [f.market.volume_ratio for f in today[:i+1]],cfg)
        revised=_family_signals([f.market.spot for f in future[:i+1]],
            [f.market.volume_ratio for f in future[:i+1]],cfg)
        assert original==revised
        assert original["close_breakout_volume"]=="call"


def test_missing_next_minute_rejects_entry_not_compress_elapsed_time():
    r=screen_session(make_frames(missing_gap=True))
    assert not any(x.signal_spot==700.5 for x in r)


def test_trade_proxies_never_present_as_executable_option_results():
    day=date(2026,8,12)
    r=run_screen(make_frames(day))
    assert r["source_sessions"]==1
    assert r["periods"]["validation"]["close_breakout_baseline"]["signal_count"]>=1
    for period,model in r["periods"].items():
        for family,stat in model.items():
            assert family in FAMILIES
            assert stat["account_returns"] is None
            assert stat["option_pnl"] is None
            assert stat["options_trade_fills"] is None


def test_daily_bootstrap_deterministic_and_includes_no_signal_days():
    days=(date(2026,8,12),date(2026,8,13))
    r=screen_session(make_frames(days[0]))
    only=[x for x in r if x.family=="close_breakout_volume"]
    cfg=ScreenConfig(bootstrap_reps=50,bootstrap_seed=71)
    a=_metrics(only,days,cfg)
    b=_metrics(only,days,cfg)
    assert a==b
    assert a["session_count"]==2
    assert a["signal_sessions"]==1
    assert a["bootstrap"]["n_days"]==2


def test_no_out_of_order_or_cross_day_smearing():
    data=make_frames()
    with pytest.raises(ValueError,match="strict"):
        screen_session([data[1],data[0]])
    with pytest.raises(ValueError,match="another trading date"):
        screen_session([data[0],make_frames(date(2026,8,13))[1]])


def test_nonoverlapping_signal_windows_per_family():
    records=screen_session(make_frames())
    per_family={}
    for event in records:
        start=datetime.fromisoformat(event.proxy_entry_timestamp)
        end=datetime.fromisoformat(event.proxy_exit_timestamp)
        if event.family in per_family:
            assert start>per_family[event.family]
        per_family[event.family]=end
