"""Read-only matched-event research tests. Never place an order."""
from datetime import date, datetime, timedelta, timezone

from engine.collector import SnapshotStore
from engine.intrabar_research import (
    ARMS, Cycle, Quote, _candidate_events, _simulate,
    research_day, run_once,
)
from engine.providers.webull_free import FreeOptionSnapshot


def _timeline():
    start = datetime(2026, 10, 16, 13, 30, tzinfo=timezone.utc)
    symbol = "SPY261016C00700000"
    cycles = []
    for second in range(48*60):
        if second % 2:
            continue
        t = start+timedelta(seconds=second)
        event_at = start+timedelta(minutes=40)
        if t < event_at:
            spot=700.0
        else:
            progress=(t-event_at).total_seconds()
            spot=700.4+min(progress,20)*.02
        q=Quote(symbol, "call", .80, .81, 5, 5, .50)
        cycles.append(Cycle(t, spot, {symbol:q}))
    return cycles


def test_early_ignition_arms_score_same_observed_price_breakout():
    cycles = _timeline()
    events = _candidate_events(cycles)
    assert events
    event = events[0]
    assert event[1] == "call"
    outcomes={}
    for arm in ARMS:
        trade, reason = _simulate(cycles,event,arm)
        assert trade is not None, (arm,reason)
        assert trade["reason"] == "five_minute_or_session_exit"
        outcomes[arm]=trade
    # No future signal candle is used to trigger the earlier experimental arm.
    assert outcomes["early_5s"]["entry_at"] < outcomes["after_candle_close"]["entry_at"]
    assert outcomes["hybrid_10s"]["entry_at"] < outcomes["after_candle_close"]["entry_at"]


def test_research_rejects_untrusted_option_time_without_fake_profit(tmp_path):
    path=tmp_path/"sample.sqlite"
    store=SnapshotStore(path)
    day=date(2026,10,16)
    at=datetime(2026,10,16,14,30,tzinfo=timezone.utc)
    store.insert([FreeOptionSnapshot(
        timestamp=at,received_at=at,timestamp_quality="receive_time_only",
        feed_delay_seconds=0,option_symbol="SPY261016C00700000",
        expiration=day,right="call",strike=700,
        bid=.80,ask=.81,bid_size=5,ask_size=5,
        underlying_price=700.,minutes_to_expiry=180,
        volume=50,open_interest=500,underlying_timestamp=at,
        source="webull_production")])
    store.close()
    result=research_day(path,day)
    assert result["status"]=="BLOCKED_INSUFFICIENT_TIMESTAMP_VERIFIED_CYCLES"
    assert result["broker_orders"]==0
    assert result["candidate_events"]==0


def test_report_is_atomic_and_offsession_and_never_trades(tmp_path):
    output=tmp_path/"research"/"report.json"
    path=tmp_path/"market.sqlite"
    result=run_once(path,output,days=2,
        now=datetime(2026,10,18,18,tzinfo=timezone.utc))
    assert output.is_file()
    assert len(result["results"])==2
    assert result["broker_orders"]==0
    assert result["live_v5_unchanged"]
    assert result["results"][0]["status"]=="BLOCKED_INSUFFICIENT_TIMESTAMP_VERIFIED_CYCLES"
