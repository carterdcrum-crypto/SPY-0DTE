"""V4 event replay cannot mark trades without true quotes and cannot look ahead."""
from datetime import date,datetime,timedelta,timezone
from types import SimpleNamespace
from pathlib import Path

from engine.regime_reclaim_v4 import (
    event_hold_gate, exit_event_v4, compare_v4, replay_v4
)

T=datetime(2026,10,12,14,10,tzinfo=timezone.utc)
Q="SPY261012C00600000"
D=date(2026,10,12)

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


def test_quality_3x_paper_counts_real_contracts_and_reserves_cash(monkeypatch):
    from engine import regime_reclaim_v4 as v4
    from types import SimpleNamespace
    session=SimpleNamespace(day=D,bars=[],quotes=[])
    selected=SimpleNamespace(observed_at=T,symbol=Q,right="call",
                             ask=1.0,bid=.99,bid_size=10,ask_size=10)
    one={"signal":{"at":T-timedelta(minutes=1),"setup":"trend_reclaim"},
         "option":selected,"debit":101.65,"quality_score":5}
    session.quotes=[selected]
    monkeypatch.setattr(v4,"exit_event_v4",lambda *args,**kw: {
        "pnl_usd":20.0,"exit_at":T+timedelta(minutes=11),
        "exit_reason":"standard_exit","extended":True})
    trades,settled,pending,decisions=v4.replay_v4(
        [one],session,1000.,allow_extension=True,
        quality_3x=True,open_equity=1000.,prior_peak=1000.)
    assert not decisions
    assert len(trades)==1 and trades[0]["contracts"]==3
    assert trades[0]["pnl_usd"]==60.
    assert trades[0]["after_extra_2usd_friction_usd"]==54.
    assert settled==1000-3*101.65
    assert pending==3*101.65+60.
    # No intraday recycling: 3 contracts require funds settled before entry.

def test_quality_3x_is_throttled_on_drawdown_and_limited_by_depth(monkeypatch):
    from engine import regime_reclaim_v4 as v4
    from types import SimpleNamespace
    selected=SimpleNamespace(observed_at=T,symbol=Q,right="call",
                             ask=1.0,bid=.99,bid_size=2,ask_size=3)
    entry={"signal":{"at":T-timedelta(minutes=1),"setup":"trend_reclaim"},
           "option":selected,"debit":101.65,"quality_score":6}
    session=SimpleNamespace(day=D,bars=[],quotes=[selected])
    monkeypatch.setattr(v4,"exit_event_v4",lambda *args,**kw: {
        "pnl_usd":0.0,"exit_at":T+timedelta(minutes=10),
        "exit_reason":"ten_min","extended":False})
    strong,*_=v4.replay_v4([entry],session,1000.,allow_extension=True,
                           quality_3x=True,open_equity=1000.,prior_peak=1000.)
    assert strong[0]["contracts"]==2  # observed bid depth is 2
    weak,*_=v4.replay_v4([entry],session,940.,allow_extension=True,
                         quality_3x=True,open_equity=940.,prior_peak=1000.)
    assert weak[0]["contracts"]==1   # prior day EOD drawdown 6%

def test_quality_3x_refuses_to_invent_missing_score():
    from engine import regime_reclaim_v4 as v4
    from types import SimpleNamespace
    import pytest
    selected=SimpleNamespace(observed_at=T,symbol=Q,right="call",
                             ask=1.0,bid=.99,bid_size=5,ask_size=5)
    item={"signal":{"at":T-timedelta(minutes=1),"setup":"trend_reclaim"},
          "option":selected,"debit":101.65}
    session=SimpleNamespace(day=D,bars=[],quotes=[selected])
    with pytest.raises(ValueError,match="missing pre-entry quality"):
        v4.replay_v4([item],session,1000.,allow_extension=True,
                     quality_3x=True,open_equity=1000.,prior_peak=1000.)

def test_scorer_rejects_missing_option_volatility_and_uses_asof_bar(monkeypatch):
    from engine import regime_reclaim_v4 as v4
    from types import SimpleNamespace
    import pytest
    earlier=SimpleNamespace(start=T-timedelta(minutes=2),
                            available_at=T-timedelta(minutes=1))
    late=SimpleNamespace(start=T+timedelta(minutes=1),
                         available_at=T+timedelta(minutes=2))
    q=SimpleNamespace(observed_at=T,symbol=Q,right="call",
                      ask=1.02,bid=1.00,delta=.50)
    signal={"at":earlier.available_at,"setup":"trend_reclaim",
            "trend_bps":5.0,"efficiency":.40,"volume_ratio":1.3}
    session=SimpleNamespace(day=D,bars=[earlier,late],quotes=[q])
    info={
        f"spy_bars_{D}":{earlier.start:{"realized_volatility":.22},
                          late.start:{"realized_volatility":.001}},
        f"spy_option_quotes_{D}":{(T,Q):{"implied_volatility":.23}},
    }
    monkeypatch.setattr(v4,"_extra",lambda root,stem,*args:info[stem])
    scored=v4.asof_quality_candidates(Path("."),session,
        [{"signal":signal,"option":q,"debit":100.}])
    assert scored[0]["quality_score"]==6
    del info[f"spy_option_quotes_{D}"][(T,Q)]
    with pytest.raises(KeyError):
        v4.asof_quality_candidates(Path("."),session,
            [{"signal":signal,"option":q,"debit":100.}])
