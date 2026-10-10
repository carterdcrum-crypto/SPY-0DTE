"""Strict SPY 0DTE ONLY event-NBBO data readiness and fixed quote-study gate.

Existing historical SPY close/CBBO-minute aggregates CANNOT stand in for
event-observed 0DTE NBBO, genuine OHLCV, fills, or executable profit.
This gate inspects on-disk archival sources ONLY, does not buy data,
invoke brokerage, reactivate adaptive filtering, or simulate option P&L
unless documented session-level event-data coverage is adequate.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Sequence

from .session_calendar import session_close
from .weekly_options_data import NY, audit as file_audit, available_sessions, load_session
from .weekly_options_experiment import ExperimentConfig, run_simulation

MIN_COMPLETE_SESSIONS_FOR_OPTION_STUDY = 20
MIN_FIVE_MINUTE_BOTH_RIGHTS_COVERAGE = .90
MIN_ELIGIBLE_OPTION_QUOTES_PER_SESSION = 50
FIRST_ELIGIBILITY_TIME = time(9, 45)
SESSION_EXIT_BUFFER_MINUTES = 15

FROZEN_CANDIDATES = ("orb_simple", "orb_filtered", "vwap_reclaim")


def _expected_clock(day: date) -> list[datetime]:
    end = session_close(day)
    if end is None:
        raise ValueError("not a supported SPY exchange session")
    start = datetime.combine(day, time(9, 30), NY)
    close = datetime.combine(day, end, NY)
    return [start + timedelta(minutes=i) for i in
            range(int((close-start).total_seconds()/60))]


def validate_day(root: Path, day: date)->dict:
    """Report coverage and failures; never fabricate quote dates or returns."""
    expected = _expected_clock(day)
    result = {
        "session":day.isoformat(),
        "expected_completed_1m_ohlcv_bars":len(expected),
        "observed_complete_ohlcv_bars":0,
        "bars_complete":False,
        "actual_event_quotes":0,
        "eligible_0dte_event_quotes":0,
        "call_and_put_five_minute_window_coverage":0.,
        "quote_events_per_right": {"call":0,"put":0},
        "complete_for_0dte_research":False,
        "reasons":[],
    }
    try:
        record = load_session(root, day)
    except (ValueError, FileNotFoundError, OSError, KeyError) as exc:
        result["reasons"].append(f"INVALID_OR_MISSING_REAL_MARKET_INPUT:{type(exc).__name__}:{exc}")
        return result

    observed = [b.start.astimezone(NY) for b in record.bars]
    result["observed_complete_ohlcv_bars"]=len(observed)
    result["bars_complete"]=observed==expected
    if not result["bars_complete"]:
        result["reasons"].append("INCOMPLETE_OR_NONCONTIGUOUS_REAL_1M_OHLCV")
    result["actual_event_quotes"]=len(record.quotes)
    filtered=[q for q in record.quotes if
              q.expiry==day and q.bid>0 and q.ask>q.bid
              and q.ask_size>=1 and q.bid_size>=1
              and q.open_interest>=25
              and .4<=abs(q.delta)<=.6
              and (q.ask-q.bid)/((q.bid+q.ask)/2)<=.15]
    result["eligible_0dte_event_quotes"]=len(filtered)
    for q in filtered:
        result["quote_events_per_right"][q.right]+=1

    first = datetime.combine(day, FIRST_ELIGIBILITY_TIME, NY)
    last = record.close_at.astimezone(NY)-timedelta(
        minutes=SESSION_EXIT_BUFFER_MINUTES)
    starts=[]
    at=first
    while at+timedelta(minutes=5)<=last:
        starts.append(at)
        at+=timedelta(minutes=5)
    windows={start: set() for start in starts}
    for q in filtered:
        when=q.observed_at.astimezone(NY)
        if first<=when<last:
            minutes=(when-first).total_seconds()/60
            start=first+timedelta(minutes=5*int(minutes//5))
            if start in windows and q.right in ("call","put"):
                windows[start].add(q.right)
    full_windows=sum(sides=={"call","put"} for sides in windows.values())
    coverage=full_windows/len(windows) if windows else 0.
    result["both_rights_windows_observed"]=full_windows
    result["total_eligible_five_minute_windows"]=len(windows)
    result["call_and_put_five_minute_window_coverage"]=round(coverage,5)

    if len(filtered)<MIN_ELIGIBLE_OPTION_QUOTES_PER_SESSION:
        result["reasons"].append("NOT_ENOUGH_REAL_SIZED_LIQUID_OPTION_EVENTS")
    if not all(result["quote_events_per_right"].values()):
        result["reasons"].append("MISSING_QUALIFYING_CALL_OR_PUT_EVENT_QUOTES")
    if coverage<MIN_FIVE_MINUTE_BOTH_RIGHTS_COVERAGE:
        result["reasons"].append("SPARSE_OBSERVED_EVENT_QUOTES_ACROSS_SESSION")
    result["complete_for_0dte_research"]=not result["reasons"]
    return result


def audit_0dte(root: Path,*,min_sessions:int=MIN_COMPLETE_SESSIONS_FOR_OPTION_STUDY)->dict:
    if min_sessions<1:
        raise ValueError("minimum complete sessions must be positive")
    filename=file_audit(root)
    days=available_sessions(root)
    bars_dates={date.fromisoformat(p.name[9:19])
                for p in root.glob("spy_bars_*.csv*")}
    quote_dates={date.fromisoformat(p.name[18:28])
                 for p in root.glob("spy_option_quotes_*.csv*")}
    covered_dates=bars_dates|quote_dates
    missing_market_days=[]
    if covered_dates:
        cursor=min(covered_dates)
        while cursor<=max(covered_dates):
            if session_close(cursor) is not None and cursor not in covered_dates:
                missing_market_days.append(cursor.isoformat())
            cursor+=timedelta(days=1)
    unpaired_bars=sorted(d.isoformat() for d in bars_dates-quote_dates)
    unpaired_quotes=sorted(d.isoformat() for d in quote_dates-bars_dates)
    reports=[validate_day(root,day) for day in days]
    complete=[r for r in reports if r["complete_for_0dte_research"]]
    full_date_coverage=(not unpaired_bars and not unpaired_quotes
                        and not missing_market_days)
    status=("READY_FOR_DESCRIPTIVE_QUOTE_RESEARCH"
            if len(complete)>=min_sessions and len(complete)==len(reports)
            and full_date_coverage
            else "BLOCKED_0DTE_EVENT_QUOTE_OR_OHLCV_COVERAGE")
    missing=[]
    if not days:
        missing=["Genuine SPY 1m OHLCV spy_bars_YYYY-MM-DD.csv(.gz)",
                 "Observed-event same-session expiry SPY OCC bid/ask quotes "
                 "spy_option_quotes_YYYY-MM-DD.csv(.gz), with real quote observation timestamps"]
    else:
        if len(reports)<min_sessions:
            missing.append(f"MINIMUM_{min_sessions}_MATCHED_SESSIONS_NOT_MET")
        if len(complete)!=len(reports):
            missing.append("AT_LEAST_ONE_MATCHED_DAY_FAILED_MARKET_COVERAGE")
    if unpaired_bars:
        missing.append("MISSING_REAL_OPTION_EVENT_QUOTES_ON_BAR_DAYS")
    if unpaired_quotes:
        missing.append("MISSING_REAL_OHLCV_ON_QUOTE_DAYS")
    if missing_market_days:
        missing.append("MISSING_WHOLE_TRADING_DAYS_WITHIN_ARCHIVE_DATE_SPAN")
    return {
        "instrument":"SPY",
        "expiration":"0DTE_SAME_SESSION_ONLY",
        "status":status,
        "minimum_days_to_run_quote_model":min_sessions,
        "paired_file_days":len(days),
        "complete_days":len(complete),
        "failed_paired_days":len(reports)-len(complete),
        "unpaired_bar_session_dates":unpaired_bars,
        "unpaired_option_quote_session_dates":unpaired_quotes,
        "missing_market_sessions_within_archive_span":missing_market_days,
        "legacy_spy_only_minute_snapshot_files":filename["legacy_minute_snapshot_files"],
        "real_ohlcv_files":filename["true_ohlcv_files"],
        "real_event_quote_files":filename["timestamped_option_quote_files"],
        "quote_coverage_standard":{
            "min_both_CALL_PUT_rights_five_minute_bucket_fraction":
              MIN_FIVE_MINUTE_BOTH_RIGHTS_COVERAGE,
            "min_eligible_quote_event_count_per_complete_session":
              MIN_ELIGIBLE_OPTION_QUOTES_PER_SESSION,
            "bid_and_ask_positive_size_required":True,
            "source_timestamp_event_observation_NOT_minute_bucket":True,
            "actual_0dte_expiration_only":True,
        },
        "per_day":reports,
        "missing_or_blockers":missing,
        "real_pnl_when_blocked":None,
        "never_fabricate_price_or_quote_records":True,
        "paid_data_requests_performed":0,
        "live_order_actions":0,
        "adaptive_signal_gate_enabled":False,
        "previously_seen_market_history_NOT_UNTOUCHED_HOLDOUT":True,
    }


def run_existing_frozen_strategies(root:Path,audit:dict,
                                   *,starting_cash:float=10000.)->dict:
    if audit["status"]!="READY_FOR_DESCRIPTIVE_QUOTE_RESEARCH":
        return {"status":"NOT_RUN_REAL_EVENT_QUOTE_COVERAGE_BLOCKED",
                "options_quote_scenario_pnl":None,
                "true_options_pnl":None,"orders_sent":0}
    dates=[date.fromisoformat(r["session"]) for r in audit["per_day"]]
    # Do not cherry pick and run just some sessions: all paired days
    # must have passed coverage audit, including no-trade dates.
    sessions=tuple(load_session(root,day) for day in dates)
    models={}
    for strategy in FROZEN_CANDIDATES:
        cfg=ExperimentConfig(strategy=strategy,risk_fraction=.01)
        report=run_simulation(sessions,starting_cash=starting_cash,cfg=cfg)
        models[strategy]={
            "scenario_starting_cash":starting_cash,
            "scenario_ending_equity":round(report.ending_equity,4),
            "net_quote_assumption_account_return_percent":
                round(100*report.total_return,4),
            "max_quote_assumption_account_drawdown_percent":
                round(100*report.max_drawdown,4),
            "trades":report.completed_trades,
            "order_attempts":report.orders_attempted,
            "filled_option_orders":report.order_fills,
            "stale_quote_marks":report.stale_quote_marks,
            "unpriced_forced_worthless_exits":report.forced_worthless_exits,
            "fees_assumed_usd":round(report.fees_paid,4),
            "profit_factor":report.profit_factor,
            "calendar_days":len(report.daily_equity),
            "calendar_days_with_positive_realized_pnl":sum(
                (r.realized_pnl_cumulative-
                 (report.daily_equity[i-1].realized_pnl_cumulative if i else 0))>0
                for i,r in enumerate(report.daily_equity)),
            "interpretation":"Observed event quote scenario with hypothetical fills, NOT live execution",
        }
    return {
        "status":"DESCRIPTIVE_QUOTE_SCENARIOS_ONLY",
        "previous_history_viewed":True,
        "genuine_untouched_holdout":False,
        "no_trading_automations":True,
        "strategies":models,
    }


def main(argv:Sequence[str]|None=None)->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data-dir",type=Path,default=Path("/data/research"))
    p.add_argument("--output-dir",type=Path,
                   default=Path("/data/research/spy0dte_quote_readiness"))
    p.add_argument("--minimum-sessions",type=int,default=MIN_COMPLETE_SESSIONS_FOR_OPTION_STUDY)
    p.add_argument("--initial-cash",type=float,default=10000.)
    args=p.parse_args(argv)
    args.output_dir.mkdir(parents=True,exist_ok=True)
    inspection=audit_0dte(args.data_dir,min_sessions=args.minimum_sessions)
    (args.output_dir/"readiness.json").write_text(
        json.dumps(inspection,indent=2,sort_keys=True),encoding="utf8")
    print("SPY 0DTE EVENT-DATA AUDIT: "+json.dumps({
        "status":inspection["status"],
        "paired_days":inspection["paired_file_days"],
        "complete_days":inspection["complete_days"],
        "legacy_snapshot_files":inspection["legacy_spy_only_minute_snapshot_files"],
        "missing":inspection["missing_or_blockers"],
        "purchased_data_usd":0,
        "live_orders":0,
    },sort_keys=True),flush=True)
    if inspection["status"]=="READY_FOR_DESCRIPTIVE_QUOTE_RESEARCH":
        result=run_existing_frozen_strategies(
            args.data_dir,inspection,starting_cash=args.initial_cash)
        (args.output_dir/"fixed_strategy_quote_scenarios.json").write_text(
            json.dumps(result,indent=2,sort_keys=True),encoding="utf8")
        print("SPY 0DTE FROZEN QUOTE STRATEGIES: "+
              json.dumps(result,sort_keys=True),flush=True)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
