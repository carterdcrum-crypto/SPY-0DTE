"""Both SPY option expirations, real observed-time quote gates, shared signals.

Synthetic data exists in tests ONLY, never substituted for user archives.
"""
from __future__ import annotations

import csv
import json
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from engine.spy_expiry_pair_study import (
    DteSession,Bar,Quote,
    next_trading_session,load_pair_session,
    _entry,_exit,_roundtrip,generate_same_signals,
    compare,input_audit,main,_policy_compare,
)
from engine.weighted_daily_coverage import Opportunity
from engine.session_calendar import session_close

NY=ZoneInfo("America/New_York")


def quote(day=date(2026,10,9),kind="0DTE",at=time(10,30,10),
          bid=1.00,ask=1.06,right="call",strike=700.0,delta=.5,
          symbol=None)->Quote:
    exp=day if kind=="0DTE" else next_trading_session(day)
    occ=f"SPY{exp.strftime('%y%m%d')}{'C' if right=='call' else 'P'}{round(strike*1000):08d}"
    return Quote(
        observed_at=datetime.combine(day,at,NY),
        symbol=symbol or occ,expiry=exp,right=right,strike=strike,
        bid=bid,ask=ask,delta=delta,bid_size=5,ask_size=5,
        volume=100,open_interest=1000,
    )


def opportunity(day=date(2026,10,9),signal=time(10,30),
                score=6)->Opportunity:
    ts=datetime.combine(day,signal,NY)
    return Opportunity(
        day=day.isoformat(),setup="close_breakout_baseline",
        original_direction="call",signal_at=ts.isoformat(),
        entry_proxy_at=(ts+timedelta(minutes=1)).isoformat(),
        exit_proxy_at=(ts+timedelta(minutes=11)).isoformat(),
        rsi14=60.,sma20=699.5,sma50=699.,macd=.2,macd_signal=.1,
        volume_ratio_at_signal=1.4,recent_macd_crossover_points=2,
        rsi_points=2,sma_points=2,price_action_points=0,
        volume_proxy_points=0,weighted_total=score,
        signed_spy_ten_minute_close_change_bps=-999.,  # poisoned outcome
    )


def session(day=date(2026,10,9),quotes=None)->DteSession:
    start=datetime.combine(day,time(9,30),NY)
    bs=[]
    for i in range(110):
        t=start+timedelta(minutes=i)
        bs.append(Bar(t,700.,700.05,699.95,700.,10000))
    return DteSession(
        day=day,bars=tuple(bs),quotes=tuple(sorted(quotes or (),key=lambda q:q.observed_at)),
        close_at=datetime.combine(day,session_close(day),NY),
    )


def test_next_trading_day_friday_holiday_and_early_close():
    assert next_trading_session(date(2026,10,9))==date(2026,10,12)
    assert next_trading_session(date(2026,11,25))==date(2026,11,27)
    assert next_trading_session(date(2026,12,24))==date(2026,12,28)
    with pytest.raises(ValueError,match="not a verified"):
        next_trading_session(date(2026,10,10))
    with pytest.raises(ValueError,match="not a verified"):
        next_trading_session(date(2025,10,9))


def test_entry_requires_REAL_POST_SIGNAL_event_and_matching_expiry_right():
    day=date(2026,10,9)
    s=opportunity()
    old=quote(at=time(10,29,59),bid=7,ask=7.1)
    too_soon=quote(at=time(10,30,1),bid=7,ask=7.1)
    q0=quote(at=time(10,30,3),bid=1,ask=1.05)
    q1=quote(kind="1DTE",at=time(10,30,3),bid=2,ask=2.06)
    future=quote(at=time(10,30,4),bid=.1,ask=.2)
    market=session(quotes=[old,too_soon,q0,q1,future])
    assert _entry(market,s,day)==q0
    assert _entry(market,s,next_trading_session(day))==q1
    assert _entry(session(quotes=[old,too_soon]),s,day) is None
    bad=quote(at=time(10,30,5),delta=.2)
    wrong=quote(at=time(10,30,6),right="put")
    assert _entry(session(quotes=[bad,wrong]),s,day) is None


def test_exit_only_from_same_contract_no_future_lookahead():
    entry=quote(at=time(10,30,3))
    before=quote(at=time(10,40,2),bid=20,ask=21)
    exact=quote(at=time(10,40,3),bid=1.45,ask=1.51)
    after=quote(at=time(10,40,4),bid=100,ask=101)
    wrong=quote(kind="1DTE",at=time(10,40,3),bid=8,ask=8.06)
    assert _exit(session(quotes=[entry,before,exact,after,wrong]),entry)==exact
    assert _exit(session(quotes=[entry,before]),entry) is None


def test_cost_per_contract_and_premium_pct_use_actual_observed_bid_ask():
    entry=quote(bid=1,ask=1.06)
    exitq=quote(at=time(10,40,10),bid=1.50,ask=1.56)
    x=_roundtrip(opportunity(),entry,exitq,"0DTE","weighted_score_ge4")
    assert x.entry_cash_debit_usd==pytest.approx(107.68)
    assert x.exit_cash_receivable_usd==pytest.approx(148.32)
    assert x.realized_quote_pnl_usd==pytest.approx(40.64)
    assert x.premium_return_percent==pytest.approx(100*40.64/107.68,abs=.001)
    assert x.contract.startswith("SPY")


def test_one_pair_same_signal_one_contract_and_fee_adjusted_returns(monkeypatch):
    import engine.spy_expiry_pair_study as x
    monkeypatch.setattr(x,"generate_same_signals",lambda s,p:[opportunity(s.day)])
    d=date(2026,10,9)
    inputs=[
        quote(d,"0DTE",time(10,30,3),bid=1.,ask=1.06),
        quote(d,"1DTE",time(10,30,3),bid=2.00,ask=2.06),
        quote(d,"0DTE",time(10,40,3),bid=1.50,ask=1.56),
        quote(d,"1DTE",time(10,40,3),bid=2.30,ask=2.36),
    ]
    report=compare([session(d,inputs)],starting_cash=10000.)
    vals=report["strategies"]["weighted_score_ge4"]
    assert vals["matched_quote_pairs"]==1
    assert vals["accounts"]["0DTE"]["quote_scenario_pnl_usd"]==pytest.approx(40.64)
    assert vals["accounts"]["1DTE"]["quote_scenario_pnl_usd"]==pytest.approx(20.64)
    assert vals["accounts"]["0DTE"]["positive_option_quote_pnl_days"]==1
    assert vals["accounts"]["1DTE"]["positive_option_quote_pnl_days"]==1
    assert vals["winner_status"]=="BLOCKED_INSUFFICIENT_MATCHED_REAL_OPTION_QUOTES"
    assert vals["future_predictive_winner"] is None
    assert report["actual_transaction_fills"]==0
    assert report["adaptive_signal_gate_disabled"] is True
    assert report["same_day_closures_only"] is True


def test_missing_one_tenor_no_fake_profit_and_paired_count_zero(monkeypatch):
    import engine.spy_expiry_pair_study as x
    monkeypatch.setattr(x,"generate_same_signals",lambda s,p:[opportunity(s.day)])
    d=date(2026,10,9)
    only_0=[quote(d,"0DTE",time(10,30,3)),
            quote(d,"0DTE",time(10,40,3),bid=2.,ask=2.06)]
    r=compare([session(d,only_0)])
    v=r["strategies"]["weighted_score_ge4"]
    assert v["matched_quote_pairs"]==0
    assert v["reject_reasons"]["missing_1dte_entry_event"]==1
    assert v["accounts"]["0DTE"]["quote_scenario_pnl_usd"] is None
    assert v["accounts"]["1DTE"]["quote_scenario_pnl_usd"] is None
    assert not v["matched_data_coverage_complete"]


def test_same_session_close_no_overnight_and_115_cash_blocks_contract(monkeypatch):
    import engine.spy_expiry_pair_study as x
    monkeypatch.setattr(x,"generate_same_signals",lambda s,p:[opportunity(s.day)])
    d=date(2026,10,9)
    qs=[
        quote(d,"0DTE",time(10,30,3),bid=1,ask=1.06),
        quote(d,"1DTE",time(10,30,3),bid=2,ask=2.06),
        quote(d,"0DTE",time(10,40,3),bid=1.5,ask=1.56),
        quote(d,"1DTE",time(10,40,3),bid=2.3,ask=2.36),
    ]
    r=compare([session(d,qs)],starting_cash=115.)
    v=r["strategies"]["weighted_score_ge4"]
    assert v["matched_quote_pairs"]==0
    assert v["reject_reasons"]["unaffordable_one_contract_in_either_book"]==1
    assert v["accounts"]["0DTE"]["quote_scenario_pnl_usd"] is None


def test_no_lookahead_underlying_price_labels_do_not_change_option_choice(monkeypatch):
    import engine.spy_expiry_pair_study as x
    d=date(2026,10,9)
    qs=[
        quote(d,"0DTE",time(10,30,3),bid=1,ask=1.06),
        quote(d,"1DTE",time(10,30,3),bid=2,ask=2.06),
        quote(d,"0DTE",time(10,40,3),bid=1.5,ask=1.56),
        quote(d,"1DTE",time(10,40,3),bid=2.3,ask=2.36),
    ]
    monkeypatch.setattr(x,"generate_same_signals",lambda s,p:[opportunity(s.day)])
    a=compare([session(d,qs)])
    monkeypatch.setattr(x,"generate_same_signals",lambda s,p:[
        replace(opportunity(s.day),signed_spy_ten_minute_close_change_bps=100000.)])
    b=compare([session(d,qs)])
    assert a["strategies"]==b["strategies"]


def test_writes_explicit_blocker_not_invented_performance(tmp_path,capsys):
    output=tmp_path/"research"
    assert main(["--data-dir",str(tmp_path),"--output-dir",str(output)])==0
    result=json.loads((output/"readiness.json").read_text())
    assert result["ready_to_score"] is False
    assert result["actual_option_profit_result"] is None
    assert result["bar_files"]==0 and result["event_quote_files"]==0
    assert result["no_paid_data_fetch"] is True
    assert not (output/"comparison.json").exists()


def test_event_bar_files_can_load_both_tenors_with_legitimate_occ_metadata(tmp_path):
    d=date(2026,10,9)
    bar=tmp_path/f"spy_bars_{d}.csv"
    with bar.open("w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=["ts_start","open","high","low","close","volume"])
        w.writeheader()
        w.writerow({"ts_start":"2026-10-09T10:30:00-04:00","open":700.,
                    "high":701.,"low":699.,"close":700.,"volume":10000})
    qfile=tmp_path/f"spy_option_quotes_{d}.csv"
    with qfile.open("w",newline="") as f:
        cols=["observed_at","symbol","expiration","right","strike",
              "bid","ask","delta","bid_size","ask_size","volume","open_interest","schema"]
        w=csv.DictWriter(f,fieldnames=cols)
        w.writeheader()
        for kind in ("0DTE","1DTE"):
            q=quote(d,kind)
            w.writerow({
                "observed_at":q.observed_at.isoformat(),
                "symbol":q.symbol,"expiration":q.expiry.isoformat(),
                "right":q.right,"strike":q.strike,
                "bid":q.bid,"ask":q.ask,"delta":q.delta,
                "bid_size":q.bid_size,"ask_size":q.ask_size,
                "volume":q.volume,"open_interest":q.open_interest,
                "schema":"cmbp-1",
            })
    x=load_pair_session(tmp_path,d)
    assert len(x.bars)==1
    assert len(x.quotes)==2
    assert {q.expiry for q in x.quotes}=={d,next_trading_session(d)}
    text=qfile.read_text().replace("cmbp-1","cbbo-1m")
    qfile.write_text(text)
    with pytest.raises(ValueError,match="no minute-bucket"):
        load_pair_session(tmp_path,d)


def test_empty_signal_sessions_do_not_make_up_option_returns():
    d=date(2026,10,9)
    s=session(d)
    a=compare([s])
    for policy in a["strategies"].values():
        assert policy["matched_quote_pairs"]==0
        assert policy["accounts"]["0DTE"]["quote_scenario_pnl_usd"] is None
        assert policy["accounts"]["1DTE"]["quote_scenario_pnl_usd"] is None
        assert policy["future_predictive_winner"] is None


def test_out_of_order_dates_block_replay():
    with pytest.raises(ValueError,match="chronological"):
        compare([session(date(2026,10,12)),session(date(2026,10,9))])
