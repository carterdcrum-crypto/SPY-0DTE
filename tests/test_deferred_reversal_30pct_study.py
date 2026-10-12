"""Detect only confirmed later reversals using past completed closes."""
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace

from engine import deferred_reversal_30pct_study as research


def fake_frames():
    t0=datetime(2026,10,6,13,0,tzinfo=timezone.utc)
    prices=[100.0]*105
    prices[75]=99.00
    prices[76]=99.02
    return [SimpleNamespace(timestamp=t0+timedelta(minutes=i),
                            market=SimpleNamespace(spot=p,
                                volume_ratio=1.4)) for i,p in enumerate(prices)]


def test_reversal_waits_for_next_completed_minute(monkeypatch):
    frames=fake_frames()
    when=frames[75].timestamp+timedelta(minutes=1)
    trigger=SimpleNamespace(family="rolling_mean_reversal",direction="call",
                            signal_timestamp=when.isoformat(),
                            signed_spy_move_bps=-9999)
    monkeypatch.setattr(research,"screen_session",lambda frames:[trigger])
    monkeypatch.setattr(research,"_rsi_wilder",lambda values:[40.]*len(values))
    a=[0.]*len(frames)
    b=[0.]*len(frames)
    a[76]=.1
    monkeypatch.setattr(research,"_macd",lambda values:(a,b))
    rows,n=research.delayed_reversals(frames,strict=True)
    assert n==1
    assert len(rows)==1
    assert rows[0]["signal_at"]==(frames[76].timestamp+timedelta(minutes=1)).isoformat()
    assert rows[0]["confirmation_wait"]==1
    trigger.signed_spy_move_bps=100000
    assert research.delayed_reversals(frames,strict=True)[0]==rows


def test_no_same_minute_impossible_reversal(monkeypatch):
    frames=fake_frames()
    frames[76].market.spot=98.95
    when=frames[75].timestamp+timedelta(minutes=1)
    monkeypatch.setattr(research,"screen_session",lambda frames:[SimpleNamespace(
        family="rolling_mean_reversal",direction="call",signal_timestamp=when.isoformat())])
    monkeypatch.setattr(research,"_rsi_wilder",lambda values:[40.]*len(values))
    a=[0.]*len(frames)
    a[76]=.1
    monkeypatch.setattr(research,"_macd",lambda values:(a,[0.]*len(values)))
    rows,n=research.delayed_reversals(frames,strict=True)
    assert n==1
    assert rows==[]


def test_duplicate_entry_window_causes_lock():
    at=datetime(2026,10,6,14,0,tzinfo=timezone.utc)
    events=[{"signal_at":(at+timedelta(minutes=i)).isoformat(),"priority":0} for i in (0,2,9,12)]
    selected=research._locked(events)
    assert len(selected)==2
    assert [e["signal_at"] for e in selected]==[events[0]["signal_at"],events[3]["signal_at"]]
