"""V2 must remain an isolated, causal challenger to the frozen V1."""
from datetime import date, datetime, timedelta, timezone

from engine.regime_reclaim_v2 import (
    adaptive_exit, deteriorating_structure, route_candidate,
    run_compare, session_plan_v2,
)
from engine.weekly_options_data import Bar, Quote, SessionData

D = date(2026, 10, 12)
UTC = timezone.utc
T = datetime(2026, 10, 12, 13, 30, tzinfo=UTC)


def bars_for_router():
    bars = []
    for i in range(45):
        close = 600.0 + .07*i
        opening = close-.12
        bars.append(Bar(T+timedelta(minutes=i),opening,close+.08,
                        opening-.07,close,1000))
    return bars


def signal(bars, i=35):
    return {"at":bars[i].available_at,"side":"call",
            "setup":"trend_reclaim","volume_ratio":1.25,
            "trend_bps":4.0,"efficiency":.35}


def test_v2_router_is_causal_and_keeps_only_frozen_setups():
    bars=bars_for_router()
    e=signal(bars)
    first=route_candidate(e,bars,35)
    assert first["selected"] and first["regime"]=="trend_up"
    future = Bar(bars[-1].start+timedelta(minutes=1),900,910,890,905,1000000)
    assert route_candidate(e,bars+[future],35)==first
    breakout=dict(e,setup="failed_range_reentry")
    assert route_candidate(breakout,bars,35)["reason"]=="NON_FROZEN_SETUP"


def test_vwap_gate_rejects_bullish_reclaim_well_below_session_vwap():
    bars=bars_for_router()
    i=35
    bars[i]=Bar(bars[i].start,597.00,597.65,596.90,597.55,1000)
    decision=route_candidate(signal(bars),bars,i)
    assert not decision["selected"]
    assert decision["reason"]=="AGAINST_SESSION_VWAP"


def test_regime_router_rejects_trend_as_chop_and_stretch_during_trend():
    bars=bars_for_router()
    e=signal(bars)
    wrong=dict(e,trend_bps=.1,efficiency=.20)
    assert route_candidate(wrong,bars,35)["reason"]=="ROUTER_REJECTED_TREND"
    reverse=dict(e,setup="stretch_reclaim",efficiency=.35)
    assert route_candidate(reverse,bars,35)["reason"]=="ROUTER_REJECTED_STRETCH"


def test_option_selection_never_looks_for_better_later_timestamp(monkeypatch,tmp_path):
    import engine.regime_reclaim_v2 as v2
    bars=bars_for_router()
    t=bars[35].available_at
    q1=Quote(t+timedelta(minutes=1),"SPY261012C00600000",
             D,"call",600,1.00,1.02,.50,10,10,500,500)
    q2=Quote(t+timedelta(minutes=2),"SPY261012C00601000",
             D,"call",601,1.00,1.001,.55,100,100,500,500)
    session=SessionData(D,tuple(bars),tuple([q1,q2]),T+timedelta(hours=6))
    addon={b.start:{"volume_ratio":1.25,"realized_volatility":.20} for b in bars}
    iv={(q.observed_at,q.symbol):{"implied_volatility":.20} for q in session.quotes}
    monkeypatch.setattr(v2,"load_session",lambda _dir,_day:session)
    monkeypatch.setattr(v2,"_extra",
        lambda _root,stem,*args: addon if stem.startswith("spy_bars_") else iv)
    monkeypatch.setattr(v2,"identify_setups",lambda _frames:[signal(bars)])
    decisions,possible,_=session_plan_v2(tmp_path,D)
    assert len(possible)==1
    assert possible[0]["option"].symbol==q1.symbol
    assert decisions[0]["quote_observed_at"]==q1.observed_at.isoformat()


def test_first_quote_failing_spread_gate_does_not_fish_later(monkeypatch,tmp_path):
    import engine.regime_reclaim_v2 as v2
    bars=bars_for_router()
    t=bars[35].available_at
    bad=Quote(t+timedelta(minutes=1),"SPY261012C00600000",
              D,"call",600,1.0,1.10,.50,10,10,100,100)
    late=Quote(t+timedelta(minutes=2),"SPY261012C00601000",
               D,"call",601,1.0,1.02,.55,10,10,100,100)
    session=SessionData(D,tuple(bars),tuple([bad,late]),T+timedelta(hours=6))
    addons={b.start:{"volume_ratio":1.25,"realized_volatility":.20} for b in bars}
    iv={(q.observed_at,q.symbol):{"implied_volatility":.20} for q in session.quotes}
    monkeypatch.setattr(v2,"load_session",lambda *_:session)
    monkeypatch.setattr(v2,"_extra",lambda _root,stem,*args:
                        addons if stem.startswith("spy_bars_") else iv)
    monkeypatch.setattr(v2,"identify_setups",lambda _frames:[signal(bars)])
    decisions,possible,_=session_plan_v2(tmp_path,D)
    assert possible==[]
    assert decisions[0]["reason"]=="FAIL_V1_SPREAD_OR_IV_COST_GATE"


def test_adaptive_stop_never_relaxes_hard_loss_or_uses_future_spy_bars():
    t=datetime(2026,10,12,14,30,tzinfo=UTC)
    bars=bars_for_router()
    path=[(t+timedelta(minutes=i),p) for i,p in
          enumerate((1.04,1.10,1.20,1.45,1.40,1.20,1.00,.98,.95,.92))]
    result=adaptive_exit(1.0,path,t,"call",bars)
    assert result["exit_reason"]=="trailing_stop"
    out=adaptive_exit(1.0,[(t+timedelta(minutes=1),.50)],t,"call",bars)
    assert out["exit_reason"]=="independent_hard_stop_35pct"


def test_structure_does_not_read_uncompleted_or_future_bar():
    bars=bars_for_router()
    t=bars[35].available_at
    assert not deteriorating_structure(bars,t,"call")
    for k in (36,37):
        prev=bars[k-1].close
        bars[k]=Bar(bars[k].start,prev,prev+.02,prev-1.3,prev-1.2,1000)
    assert not deteriorating_structure(bars,bars[35].available_at,"call")
    assert deteriorating_structure(bars,bars[37].available_at,"call")


def test_no_forward_feed_returns_blocked_not_fictitious_win_rate(tmp_path):
    legacy=tmp_path/"legacy";legacy.mkdir()
    (legacy/"spy_0dte_2026-10-08.csv.gz").write_bytes(b"past inspected archive")
    feed=tmp_path/"feed";feed.mkdir()
    out=tmp_path/"compare"
    result=run_compare(feed,legacy,out)
    assert result["status"]=="BLOCKED_NO_NEW_EVENT_SESSIONS"
    for item in result["results"].values():
        assert item["scorable_positions"]==0
        assert item["win_rate_pct"] is None
        assert item["hypothetical_equity_usd"]==1000
    assert result["no_broker_orders"] is True
    assert (out/"comparison_manifest.json").exists()
    assert (out/"v1_frozen_decisions.csv").exists()


def test_identical_flat_new_sessions_produce_identical_empty_ledgers(tmp_path):
    import csv
    legacy=tmp_path/"legacy";legacy.mkdir()
    (legacy/"spy_0dte_2026-10-08.csv.gz").write_bytes(b"past inspected archive")
    feed=tmp_path/"feed";feed.mkdir()
    with (feed/"spy_bars_2026-10-12.csv").open("w",newline="") as f:
        w=csv.writer(f)
        w.writerow(("ts_start","open","high","low","close","volume",
                    "volume_ratio","realized_volatility"))
        for i in range(50):
            ts=(T+timedelta(minutes=i)).isoformat()
            w.writerow((ts,600,600,600,600,1000,1.0,.20))
    (feed/"spy_option_quotes_2026-10-12.csv").write_text(
        "observed_at,symbol,expiration,right,strike,bid,ask,delta,bid_size,"
        "ask_size,volume,open_interest,schema,implied_volatility\n")
    result=run_compare(feed,legacy,tmp_path/"out")
    assert result["status"]=="EXPLORATORY_FORWARD_PAIR_COMPARISON_COMPLETED"
    assert len(result["processed"])==1
    for item in result["results"].values():
        assert item["hypothetical_equity_usd"]==1000
        assert item["paper_positions"]==0
    assert result["results"]["v1_frozen"]==result["results"]["v2_router_fixed_exit"]
