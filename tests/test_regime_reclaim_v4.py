"""V4 event replay cannot mark trades without true quotes and cannot look ahead."""
from datetime import date,datetime,timedelta,timezone
from types import SimpleNamespace

from engine.regime_reclaim_v4 import (
    event_hold_gate, exit_event_v4, compare_v4, replay_v4
)

T=datetime(2026,10,12,14,10,tzinfo=timezone.utc)
Q="SPY261012C00600000"

def bars(rise=.12,volume=150):
    out=[]
    for n in range(75):
        stamp=T+timedelta(minutes=n-40)
        out.append(SimpleNamespace(start=stamp,available_at=stamp+timedelta(minutes=1),
            close=600+rise*n,volume=volume))
    return out

def quote(at,bid=1.50,ask=1.51):
    return SimpleNamespace(observed_at=at,bid=bid,ask=ask,
                           bid_size=10,ask_size=10,symbol=Q)

def test_event_gate_is_causal_and_requires_gain_and_directional_volume():
    seq=bars()
    available=[b.available_at for b in seq]
    q=quote(T+timedelta(minutes=10))
    assert event_hold_gate(seq,available,q,q.observed_at,"call",148.35,101.65,150)
    assert not event_hold_gate(seq,available,q,q.observed_at,"put",148.35,101.65,150)
    assert not event_hold_gate(seq,available,q,q.observed_at,"call",105,101.65,150)
    seq[55].close=10
    assert event_hold_gate(seq,available,q,q.observed_at,"call",148.35,101.65,150)

def test_no_extension_closes_at_ten_minute_event():
    seq=bars()
    quotes=[quote(T+timedelta(minutes=n)) for n in range(1,15)]
    result=exit_event_v4(1.0,quotes,seq,entry_at=T,side="call",
                         cutoff=T+timedelta(minutes=15),allow_extension=False)
    assert result["exit_reason"]=="standard_10min_exit"
    assert result["exit_at"]==T+timedelta(minutes=10)

def test_strong_signal_can_extend_and_then_exit():
    seq=bars()
    quotes=[quote(T+timedelta(minutes=n)) for n in range(1,14)]
    quotes += [quote(T+timedelta(minutes=14),bid=.99,ask=1.00)]
    result=exit_event_v4(1.0,quotes,seq,entry_at=T,side="call",
                         cutoff=T+timedelta(minutes=15),allow_extension=True)
    assert result["extended"]
    assert result["exit_at"]>T+timedelta(minutes=10)

def test_missing_ten_minute_event_is_unscorable():
    seq=bars()
    quotes=[quote(T+timedelta(minutes=1)),quote(T+timedelta(minutes=12))]
    result=exit_event_v4(1.0,quotes,seq,entry_at=T,side="call",
                         cutoff=T+timedelta(minutes=15),allow_extension=True)
    assert result is None

def test_v4_no_event_sessions_has_no_invented_win_rate(tmp_path):
    legacy=tmp_path/"legacy"
    legacy.mkdir()
    (legacy/"spy_0dte_2026-10-08.csv.gz").write_bytes(b"old")
    event=tmp_path/"events"
    event.mkdir()
    report=compare_v4(event,legacy,tmp_path/"out")
    assert report["status"]=="BLOCKED_NO_NEW_EVENT_SESSIONS"
    assert report["outcomes"]["v4_conditional_hold"]["win_rate_pct"] is None
    assert report["outcomes"]["v4_1_equity_guard"]["win_rate_pct"] is None
    assert report["no_real_orders"]
