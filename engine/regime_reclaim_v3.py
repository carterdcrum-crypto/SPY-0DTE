"""V3 Combined Regime Reclaim — NO maximum holding-time exit.

V2 regime router and contract selection are unchanged. Continue until
observed stop/structural deterioration or mandatory pre-expiration flat.
Paper only, never routes orders. Pairs against frozen V1 and fixed-hold V2
on genuinely new event-observed options/underlying sessions.
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timedelta
from pathlib import Path

from .frozen_regime_forward import START_CASH, latest_legacy_day, FEE_STRESS_PER_TRADE
from .regime_reclaim_v2 import run_compare, session_plan_v2, metrics
from .regime_hold_sensitivity import liquidation_cutoff, simulate_unlimited
from .weekly_options_data import load_session, available_sessions


def replay_open_ended_v3(candidates,session,settled):
    cutoff=liquidation_cutoff(session.day)
    flat=None
    pending=0.0
    positions=[]
    decisions=[]
    for item in sorted(candidates,key=lambda c:(
            c["option"].observed_at,c["signal"]["at"],c["option"].symbol)):
        q=item["option"]
        entry_at=q.observed_at
        debit=item["debit"]
        if entry_at >= cutoff-timedelta(minutes=1):
            decisions.append({"entry_at":entry_at.isoformat(),
                              "reason":"EXPIRY_BUFFER_CUTOFF"})
            continue
        if flat is not None and entry_at<=flat:
            decisions.append({"entry_at":entry_at.isoformat(),
                              "reason":"ONE_POSITION_BUSY"})
            continue
        if debit>settled+1e-9:
            decisions.append({"entry_at":entry_at.isoformat(),
                              "reason":"INSUFFICIENT_T1_SETTLED_CASH",
                              "required_usd":debit})
            continue
        # Decision/entry is made without accessing future price rows.
        settled-=debit
        path=[(x.observed_at,x.bid) for x in session.quotes
              if x.symbol==q.symbol and entry_at<x.observed_at<=cutoff
              and x.bid>=0 and x.ask>=x.bid]
        outcome=simulate_unlimited(q.ask,path,cutoff,
                    bars=session.bars,side=q.right,entry_at=entry_at,adaptive=True)
        if outcome is None:
            # Unverified liquidatability. Conservatively reserve entire debit
            # and block this session; never backfill a favorable last bid.
            flat=cutoff
            positions.append({
                "entry_at":entry_at.isoformat(),"symbol":q.symbol,
                "entry_debit_usd":debit,"pnl_usd":None,
                "reason":"UNSCOREABLE_MISSING_LIQUIDATION_QUOTE",
            })
            continue
        pnl=outcome["pnl_usd"]
        pending+=max(0.0,debit+pnl)
        flat=outcome["exit_at"]
        positions.append({
            "entry_at":entry_at.isoformat(),"symbol":q.symbol,
            "side":q.right,"setup":item["signal"]["setup"],
            "entry_debit_usd":debit,"pnl_usd":pnl,
            "exit_at":flat.isoformat(),"exit_reason":outcome["exit_reason"],
            "after_extra_2usd_friction_usd":round(pnl-FEE_STRESS_PER_TRADE,2),
        })
    return positions,settled,pending,decisions


def run_v3_comparison(event_dir:Path,legacy_dir:Path,output_dir:Path)->dict:
    """Side-by-side unchanged V1/V2, plus no-hold V3 on same new sessions."""
    prior=run_compare(event_dir,legacy_dir,output_dir)
    # Use only days that strict V1+V2 already validated and processed.
    days=[d["date"] for d in prior["processed"]]
    cash=START_CASH
    pending=0.
    equity=[]
    trades=[]
    decisions=[]
    for daystr in days:
        from datetime import date
        day=date.fromisoformat(daystr)
        _,possible,_=session_plan_v2(event_dir,day)
        session=load_session(event_dir,day)
        cash+=pending
        pending=0.
        results,cash,pending,rejections=replay_open_ended_v3(possible,session,cash)
        trades.extend([{"day":daystr,**r} for r in results])
        decisions.extend([{"day":daystr,**r} for r in rejections])
        equity.append(cash+pending)
    v3=metrics(trades,cash,pending,equity)
    results={**prior["results"],"v3_no_max_hold":v3}
    report={
        "study":"Combined Regime Reclaim 0DTE no-fixed-hold forward comparison",
        "status":prior["status"],
        "archived_hindsight_is_not_forward_test":True,
        "source_policy":"exact_V2_regime_entry_plus_adaptive20trail_minus_10min_timeout",
        "v3_has_no_fixed_max_minutes":True,
        "only_forced_time_exit":"verified NYSE regular/early close minus 15 minutes",
        "unchanged_stops":"30% trailing, tighten to 20% on observed adverse 2-bar EMA8 structure, independent 35% premium loss",
        "new_event_sessions":prior["new_event_sessions"],
        "processed":prior["processed"],
        "errors":prior["skipped"],
        "start_cash_usd":START_CASH,
        "settlement":"T+1 no same-day proceeds recycling",
        "extra_fee_stress_usd_each_trade":FEE_STRESS_PER_TRADE,
        "results":results,
        "no_real_orders":True,"data_purchases":0,
        "warning":"Historically inspected CBBO-minute files cannot measure V2 VWAP/regime entries or prove broker fills; genuine new option event NBBO required.",
    }
    output_dir.mkdir(parents=True,exist_ok=True)
    (output_dir/"v3_no_max_hold_comparison.json").write_text(json.dumps(report,indent=2,sort_keys=True)+"\n")
    for suffix,rows,cols in (
        ("trades",trades,("day","entry_at","pnl_usd")),
        ("decisions",decisions,("day","entry_at","reason")),
    ):
        path=output_dir/f"v3_no_max_hold_{suffix}.csv"
        keys=sorted(set(cols)|{k for row in rows for k in row})
        with path.open("w",newline="") as h:
            writer=csv.DictWriter(h,fieldnames=keys)
            writer.writeheader()
            writer.writerows(rows)
    print("REGIME_RECLAIM_NO_MAX_HOLD_FORWARD "+json.dumps(report,sort_keys=True),flush=True)
    return report


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--event-dir",type=Path,default=Path("/data/research/forward_events"))
    parser.add_argument("--legacy-dir",type=Path,default=Path("/data/research"))
    parser.add_argument("--output-dir",type=Path,default=Path("/data/research/regime_v2_comparison"))
    args=parser.parse_args()
    run_v3_comparison(args.event_dir,args.legacy_dir,args.output_dir)


if __name__=="__main__":
    main()
