"""SPY 09:30–11:00 exchange-time strategy and causal opening setup."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from engine.morning_0930_1100_study import (
    START_TIME, END_TIME, OLD_START, OLD_END,
    TRAIL_PCT, OPENING_HISTORY, in_window, opening_events,
    nonoverlap, summarize, bootstrap,
)


def make_frames(prices=None, volume=1.8):
    origin=datetime(2026,10,6,13,30,tzinfo=timezone.utc)  # 09:30 EDT
    if prices is None:
        prices=[600+0.05*i for i in range(100)]
    return [SimpleNamespace(
        timestamp=origin+timedelta(minutes=i),
        market=SimpleNamespace(spot=x,volume_ratio=volume))
        for i,x in enumerate(prices)]


def test_requested_window_is_new_york_930_to_11_not_original_10_to_12():
    assert (START_TIME.hour,START_TIME.minute)==(9,30)
    assert (END_TIME.hour,END_TIME.minute)==(11,0)
    assert (OLD_START.hour,OLD_END.hour)==(10,12)
    for hh,mm,expected in [(9,29,False),(9,30,True),(9,59,True),
                           (10,59,True),(11,0,False),(11,30,False)]:
        when=datetime(2026,10,6,hh+4,mm,tzinfo=timezone.utc)
        assert in_window(when)==expected
    assert TRAIL_PCT==30


def test_opening_scanner_cannot_trade_open_without_15_completed_bar_history():
    frames=make_frames()
    found=opening_events(frames)
    assert found
    when=min(datetime.fromisoformat(x["signal_at"]) for x in found)
    # First signal occurs at 09:46 EDT, after initial 16 completed closes.
    assert when.astimezone(__import__("zoneinfo").ZoneInfo("America/New_York")).hour==9
    assert when.minute>=OPENING_HISTORY+31
    assert all(in_window(datetime.fromisoformat(x["signal_at"])) for x in found)
    assert all(x["right"]=="call" for x in found)


def test_opening_scanner_rejects_future_signal_time_or_data_gaps():
    frames=make_frames()
    assert opening_events(frames[:15])==[]
    broken=make_frames()
    broken[5].timestamp+=timedelta(minutes=2)
    assert all(datetime.fromisoformat(x["signal_at"]).minute>=0
               for x in opening_events(broken))
    # Changing the NEXT completed minute cannot change signal at 09:46.
    baseline=[x for x in opening_events(frames)
              if datetime.fromisoformat(x["signal_at"]).minute==46]
    frames[17].market.spot=100000
    assert [x for x in opening_events(frames)
              if datetime.fromisoformat(x["signal_at"]).minute==46]==baseline
    quiet=make_frames(volume=1.0)
    assert opening_events(quiet)==[]


def test_nonoverlap_uses_signal_time_not_future_profit():
    origin=datetime(2026,10,6,13,46,tzinfo=timezone.utc)
    events=[{"signal_at":(origin+timedelta(minutes=i)).isoformat(),
             "right":"call","origin":"opening_close_breakout_proxy"} for i in (0,2,11,12)]
    chosen=nonoverlap(events)
    assert [datetime.fromisoformat(x["signal_at"]) for x in chosen]==[
        origin,origin+timedelta(minutes=11)]


def test_empty_arms_do_not_fabricate_win_rate():
    report=summarize([],["2026-10-06"],"legacy_volume_0930_1100",
                     "all_inspected")
    assert report["hypothetical_one_contract_trades"]==0
    assert report["win_rate_pct"] is None
    assert report["modeled_pnl_usd_NOT_ACCOUNT"]==0
    interval=bootstrap([],["2026-10-06"],"legacy_volume_0930_1100",reps=20)
    assert interval["extra_friction_5session_block_95pct_usd_per_calendar_day"]==[0,0]
