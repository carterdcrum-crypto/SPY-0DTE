"""SPY-0DTE-only event quote / complete OHLCV data-quality gate.

All fixture quotes below are SYNTHETIC UNIT TEST INPUT, not market data.
"""
from __future__ import annotations

import csv
from datetime import date,datetime,time,timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from engine.spy0dte_quote_readiness import (
    audit_0dte,validate_day,run_existing_frozen_strategies,
    main,
)

NY=ZoneInfo("America/New_York")


def _full_day(root:Path,day:date,*,include_put:bool=True,
              hole:bool=False,bucket_schema:str="nbbo-event")->None:
    root.mkdir(parents=True,exist_ok=True)
    bars=root/f"spy_bars_{day}.csv"
    cols=["ts_start","open","high","low","close","volume"]
    start=datetime.combine(day,time(9,30),NY)
    with bars.open("w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=cols);w.writeheader()
        for i in range(390):
            if hole and i==210:
                continue
            t=start+timedelta(minutes=i)
            w.writerow({"ts_start":t.isoformat(),"open":700.,
                        "high":700.10,"low":699.90,
                        "close":700.,"volume":1200})
    quotes=root/f"spy_option_quotes_{day}.csv"
    columns=["observed_at","symbol","expiration","right","strike","bid",
             "ask","delta","bid_size","ask_size","volume",
             "open_interest","schema"]
    with quotes.open("w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=columns);w.writeheader()
        begin=datetime.combine(day,time(9,45),NY)
        for n in range(72):
            ts=begin+timedelta(minutes=5*n,seconds=10)
            for right in (["call","put"] if include_put else ["call"]):
                s="C" if right=="call" else "P"
                occ=f"SPY{day.strftime('%y%m%d')}{s}{700000:08d}"
                w.writerow({
                    "observed_at":ts.isoformat(),
                    "symbol":occ,"expiration":day.isoformat(),
                    "right":right,"strike":700.,
                    "bid":1.00,"ask":1.05,
                    "delta":.5 if right=="call" else -.5,
                    "bid_size":3,"ask_size":3,"volume":100,
                    "open_interest":1000,
                    "schema":bucket_schema,
                })


def test_missing_real_quote_data_fails_closed_and_skips_execution(tmp_path):
    r=audit_0dte(tmp_path)
    assert r["status"]=="BLOCKED_0DTE_EVENT_QUOTE_OR_OHLCV_COVERAGE"
    assert r["real_pnl_when_blocked"] is None
    assert r["paid_data_requests_performed"]==0
    assert len(r["missing_or_blockers"])==2
    x=run_existing_frozen_strategies(tmp_path,r)
    assert x["options_quote_scenario_pnl"] is None
    assert x["orders_sent"]==0
    assert main(["--data-dir",str(tmp_path),"--output-dir",str(tmp_path/"results")])==0
    assert (tmp_path/"results"/"readiness.json").exists()
    assert not (tmp_path/"results"/"fixed_strategy_quote_scenarios.json").exists()


def test_full_day_0dte_event_quote_completeness_on_synthetic_test_inputs(tmp_path):
    day=date(2026,10,9)
    _full_day(tmp_path,day)
    report=validate_day(tmp_path,day)
    assert report["expected_completed_1m_ohlcv_bars"]==390
    assert report["observed_complete_ohlcv_bars"]==390
    assert report["bars_complete"] is True
    assert report["quote_events_per_right"]=={"call":72,"put":72}
    assert report["call_and_put_five_minute_window_coverage"]==1.0
    assert report["complete_for_0dte_research"] is True
    # One day can pass quality inspection but fails the full historical
    # repeatability sample threshold.
    a=audit_0dte(tmp_path)
    assert a["status"]=="BLOCKED_0DTE_EVENT_QUOTE_OR_OHLCV_COVERAGE"
    assert "MINIMUM_20_MATCHED_SESSIONS_NOT_MET" in a["missing_or_blockers"]
    assert a["complete_days"]==1
    passed=audit_0dte(tmp_path,min_sessions=1)
    assert passed["status"]=="READY_FOR_DESCRIPTIVE_QUOTE_RESEARCH"
    x=run_existing_frozen_strategies(tmp_path,passed)
    assert x["status"]=="DESCRIPTIVE_QUOTE_SCENARIOS_ONLY"
    assert x["genuine_untouched_holdout"] is False
    assert x["strategies"]["orb_simple"]["trades"]==0
    assert x["strategies"]["orb_simple"]["calendar_days"]==1


def test_incomplete_ohlcv_or_missing_put_quote_never_ready(tmp_path):
    day=date(2026,10,9)
    _full_day(tmp_path,day,hole=True)
    r=validate_day(tmp_path,day)
    assert not r["bars_complete"]
    assert "INCOMPLETE_OR_NONCONTIGUOUS_REAL_1M_OHLCV" in r["reasons"]
    _full_day(tmp_path,day,include_put=False)
    r=validate_day(tmp_path,day)
    assert not r["complete_for_0dte_research"]
    assert r["quote_events_per_right"]["put"]==0
    assert "MISSING_QUALIFYING_CALL_OR_PUT_EVENT_QUOTES" in r["reasons"]


def test_wrong_minute_bucket_quote_schema_is_rejected(tmp_path):
    day=date(2026,10,9)
    _full_day(tmp_path,day,bucket_schema="cbbo-1m")
    r=validate_day(tmp_path,day)
    assert not r["complete_for_0dte_research"]
    assert any("INVALID_OR_MISSING_REAL_MARKET_INPUT:ValueError" in s
               for s in r["reasons"])
    assert r["actual_event_quotes"]==0


def test_no_future_session_spot_files_can_rescue_missing_quote_day(tmp_path):
    d1=date(2026,10,8)
    d2=date(2026,10,9)
    _full_day(tmp_path,d1)
    _full_day(tmp_path,d2)
    missing=tmp_path/f"spy_option_quotes_{d1}.csv"
    missing.unlink()
    a=audit_0dte(tmp_path,min_sessions=1)
    # Only the matched d2 source is audited, and d1 cannot become a
    # known historical return from its bars alone.
    assert a["paired_file_days"]==1
    assert a["complete_days"]==1
    assert a["real_ohlcv_files"]==2 and a["real_event_quote_files"]==1
