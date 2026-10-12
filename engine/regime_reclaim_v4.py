"""V4 and V4.1 event-time research comparator (never sends broker orders).

Uses the same V2 regime-routed, cost-checked option entries. At the tenth
minute it extends only with a favorable *observed* option quote, completed
SPY bar momentum/volume, and a healthy spread. A precommitted V4.1
portfolio guard disallows extension if prior session account equity is
below its prior high-water mark. No trades or P&L without valid event data.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import json
import math
from datetime import date, timedelta
from pathlib import Path

from .regime_hold_conditional import (
    EXTEND_MIN_RETURN, EXTEND_MIN_RVOL, EXTEND_MIN_SPY_MOMENTUM_BPS,
    EXTEND_MIN_PREMIUM_PEAK_FRACTION, MAX_QUOTE_SPREAD, EXTEND_TRAIL,
)
from .regime_hold_sensitivity import liquidation_cutoff
from .regime_reclaim_frozen_signal import ema
from .regime_reclaim_v2 import (
    FEE_PER_SIDE, SLIP_PER_SHARE, FEE_STRESS_PER_TRADE, session_plan_v2, metrics,
)
from .regime_reclaim_v3 import run_v3_comparison
from .frozen_regime_forward import START_CASH, latest_legacy_day
from .weekly_options_data import load_session

ARMS=("v4_conditional_hold","v4_1_equity_guard")
BASE_TRAIL=.30
HARD_STOP=.35
TEN_MINUTES=timedelta(minutes=10)
MAX_GATE_QUOTE_DELAY=timedelta(seconds=60)


def credit_at_bid(bid: float) -> float:
    return max(0.0,(bid-SLIP_PER_SHARE)*100-FEE_PER_SIDE)


def event_hold_gate(bars, available, quote, at, side, credit, debit, peak):
    """Never uses a bar that closes after the received option quote."""
    i=bisect.bisect_right(available,at)-1
    if i<35 or quote.observed_at!=at:
        return False
    if not quote.ask>quote.bid>0 or quote.bid_size<1 or quote.ask_size<1:
        return False
    spread=(quote.ask-quote.bid)/((quote.ask+quote.bid)/2)
    if spread>MAX_QUOTE_SPREAD or credit<debit*(1+EXTEND_MIN_RETURN):
        return False
    if credit<peak*EXTEND_MIN_PREMIUM_PEAK_FRACTION:
        return False
    before=[bar.volume for bar in bars[i-20:i]]
    if not before or sum(before)<=0 or bars[i].volume/(sum(before)/len(before))<EXTEND_MIN_RVOL:
        return False
    closes=[bar.close for bar in bars[i-35:i+1]]
    direction=1 if side=="call" else -1
    mom_bps=direction*(closes[-1]/closes[-4]-1)*10000
    return mom_bps>=EXTEND_MIN_SPY_MOMENTUM_BPS and direction*(closes[-1]-ema(closes,8))>0


def exit_event_v4(ask, quote_events, bars, *, entry_at, side, cutoff, allow_extension):
    """Reject stale 10-min decisions and gaps; credit from observed option bid."""
    if not quote_events:
        return None
    available=[b.available_at for b in bars]
    due=entry_at+TEN_MINUTES
    debit=(ask+SLIP_PER_SHARE)*100+FEE_PER_SIDE
    peak=debit
    trail=BASE_TRAIL
    gate_seen=False
    extended=False
    last_at=None
    last_credit=None
    for q in quote_events:
        at=q.observed_at
        if at<=entry_at or at>cutoff or q.bid<0 or q.ask<q.bid:
            continue
        credit=credit_at_bid(q.bid)
        peak=max(peak,credit)
        if last_at is not None and at-last_at>timedelta(minutes=2):
            # A >2m gap cannot establish that a stop was executable.
            return None
        last_at,last_credit=at,credit
        if credit<=debit*(1-HARD_STOP)+1e-9:
            return {"pnl_usd":round(credit-debit,6),"exit_at":at,
                    "exit_reason":"independent_hard_stop_35pct","extended":extended}
        if credit<=peak*(1-trail)+1e-9:
            return {"pnl_usd":round(credit-debit,6),"exit_at":at,
                    "exit_reason":"trailing_stop","extended":extended}
        if not gate_seen and at>=due:
            if at-due>MAX_GATE_QUOTE_DELAY:
                return None
            gate_seen=True
            passed=allow_extension and event_hold_gate(
                bars,available,q,at,side,credit,debit,peak)
            if not passed:
                return {"pnl_usd":round(credit-debit,6),"exit_at":at,
                        "exit_reason":"standard_10min_exit","extended":False}
            extended=True
            trail=min(trail,EXTEND_TRAIL)
            if credit<=peak*(1-trail)+1e-9:
                return {"pnl_usd":round(credit-debit,6),"exit_at":at,
                        "exit_reason":"trailing_stop_20pct_extended","extended":True}
            continue
        if extended and (
            credit<=peak*(1-trail)+1e-9 or
            not event_hold_gate(bars,available,q,at,side,credit,debit,peak)
        ):
            return {"pnl_usd":round(credit-debit,6),"exit_at":at,
                    "exit_reason":"extended_exit","extended":True}
    if not gate_seen:
        return None
    if extended and last_at and cutoff-last_at<=MAX_GATE_QUOTE_DELAY:
        return {"pnl_usd":round(last_credit-debit,6),"exit_at":last_at,
                "exit_reason":"mandatory_pre_expiry_liquidation","extended":True}
    return None


def replay_v4(possible,session,settled,*,allow_extension):
    cutoff=liquidation_cutoff(session.day)
    positions,decisions=[],[]
    pending=0.0
    flat=None
    quotes_by_symbol={}
    for quote in session.quotes:
        quotes_by_symbol.setdefault(quote.symbol,[]).append(quote)
    for item in sorted(possible,key=lambda item:(
            item["option"].observed_at,item["signal"]["at"],item["option"].symbol)):
        quote=item["option"]
        at=quote.observed_at
        debit=item["debit"]
        if at>=cutoff-TEN_MINUTES-MAX_GATE_QUOTE_DELAY:
            decisions.append({"entry_at":at.isoformat(),"reason":"INSUFFICIENT_10MIN_CLOSE_BUFFER"})
            continue
        if flat is not None and at<=flat:
            decisions.append({"entry_at":at.isoformat(),"reason":"POSITION_BUSY"})
            continue
        if debit>settled+1e-9:
            decisions.append({"entry_at":at.isoformat(),"reason":"INSUFFICIENT_T1_SETTLED_CASH"})
            continue
        settled-=debit
        event_quotes=[q for q in quotes_by_symbol.get(quote.symbol,[]) if
                      at<q.observed_at<=cutoff]
        outcome=exit_event_v4(
            quote.ask,event_quotes,session.bars,entry_at=at,
            side=quote.right,cutoff=cutoff,allow_extension=allow_extension)
        if outcome is None:
            flat=cutoff
            positions.append({"entry_at":at.isoformat(),"symbol":quote.symbol,
                              "pnl_usd":None,"entry_debit_usd":debit,
                              "reason":"UNSCORABLE_STALE_OR_MISSING_EVENT_QUOTES"})
            continue
        flat=outcome["exit_at"]
        pnl=outcome["pnl_usd"]
        pending+=max(0.0,debit+pnl)
        positions.append({
            "entry_at":at.isoformat(),"symbol":quote.symbol,"side":quote.right,
            "setup":item["signal"]["setup"],"entry_debit_usd":debit,
            "pnl_usd":pnl,"exit_at":flat.isoformat(),
            "exit_reason":outcome["exit_reason"],
            "extended":outcome["extended"],
            "after_extra_2usd_friction_usd":round(pnl-FEE_STRESS_PER_TRADE,2),
        })
    return positions,settled,pending,decisions


def compare_v4(event_dir:Path,legacy_dir:Path,output_dir:Path)->dict:
    earlier=run_v3_comparison(event_dir,legacy_dir,output_dir)
    days=[date.fromisoformat(x["date"]) for x in earlier["processed"]]
    ledgers={name:{"cash":START_CASH,"pending":0.0,
                   "days":[],"trades":[],"decisions":[]}
             for name in ARMS}
    for day in days:
        _,candidates,_=session_plan_v2(event_dir,day)
        session=load_session(event_dir,day)
        for name,state in ledgers.items():
            previous=state["days"][-1] if state["days"] else START_CASH
            prior_peak=max([START_CASH]+state["days"])
            enable=(name=="v4_conditional_hold" or previous>=prior_peak-1e-8)
            state["cash"]+=state["pending"]
            state["pending"]=0.0
            rows,state["cash"],state["pending"],reject=replay_v4(
                candidates,session,state["cash"],allow_extension=enable)
            state["trades"] += [{"day":str(day),**r} for r in rows]
            state["decisions"] += [{"day":str(day),**r} for r in reject]
            state["days"].append(state["cash"]+state["pending"])
    estimates={name:metrics(state["trades"],state["cash"],state["pending"],state["days"])
               for name,state in ledgers.items()}
    status=earlier["status"]
    if any(x["unscorable_positions"] for x in estimates.values()) and status=="EXPLORATORY_FORWARD_PAIR_COMPARISON_COMPLETED":
        status="INCOMPLETE_UNSCOREABLE_QUOTES"
    report={
        "status":status,"version":"2026-10-12-V4-forward-frozen",
        "note":"V4 historical CBBO-minute winning numbers are NOT this V2-entry event-time forward experiment.",
        "processed":earlier["processed"],"new_event_sessions":earlier["new_event_sessions"],
        "start_cash_usd":START_CASH,"no_real_orders":True,"data_purchases":0,
        "last_inspected_legacy_date":str(latest_legacy_day(legacy_dir)),
        "outcomes":{**earlier["results"],**estimates},
        "v4_equity_guard":"extension off if preceding session's EOD equity below its prior running max",
        "v4_rules":"10min default, 10% positive premium, SPY 3-bar directional momentum >=3bps, 20-bar relative volume >=0.9, <=3.5% spread, >=90% peak, EMA8, 20% extended trail",
        "max_end_of_day_drawdown_only":True,
    }
    output_dir.mkdir(parents=True,exist_ok=True)
    (output_dir/"v4_conditional_forward.json").write_text(json.dumps(report,indent=2,sort_keys=True)+"\n")
    for name,st in ledgers.items():
        for kind in ("trades","decisions"):
            records=st[kind]
            fields=sorted({"day","entry_at","pnl_usd","reason"}|{k for row in records for k in row})
            with (output_dir/f"{name}_{kind}.csv").open("w",newline="") as handle:
                writer=csv.DictWriter(handle,fieldnames=fields)
                writer.writeheader()
                writer.writerows(records)
    print("V4_EVENT_FORWARD_COMPARE "+json.dumps(report,sort_keys=True),flush=True)
    return report


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--event-dir",type=Path,default=Path("/data/research/forward_events"))
    p.add_argument("--legacy-dir",type=Path,default=Path("/data/research"))
    p.add_argument("--output-dir",type=Path,default=Path("/data/research/regime_v2_comparison"))
    x=p.parse_args()
    compare_v4(x.event_dir,x.legacy_dir,x.output_dir)


if __name__=="__main__":
    main()
