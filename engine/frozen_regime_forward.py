"""Frozen Regime Reclaim: fail-closed prospective EVENT-DATA paper replay.

Freeze original research candidate on commit 2aa21568ff72e4cafbf718e75084d3c0fa4ee83b.
Do not re-optimize on the 128 previously inspected legacy snapshot sessions.

REQUIRES genuinely new (>archive last date) paired completed SPY OHLCV and
event-observed 0DTE NBBO timestamps/sizes, PLUS as-of:
  spy_bars_D.csv[volume_ratio,realized_volatility]
  spy_option_quotes_D.csv[implied_volatility]
These are mandatory provenance-bearing derived metrics, NOT silently inferred
from old CBBO-minute snapshots. No data? Return BLOCKED with precise reasons.

One contract max at a time; $1000 simulated settled cash / T+1;
no same-day proceeds recycling. No fixed take profit or partial exits.
A past-looking quote may never be selected to satisfy a future signal.
This is offline event-data paper replay, NOT actual broker fills or live feed.
"""
from __future__ import annotations

import argparse,csv,gzip,hashlib,json,math
from collections import defaultdict,Counter
from datetime import datetime,timedelta,date
from pathlib import Path
from types import SimpleNamespace

from .burst_research import _research_files
from .weekly_options_data import load_session,available_sessions,audit,aware
from .regime_reclaim_winrate import identify_setups,START_CASH,TRAIL_PCT,HARD_STOP_PCT,MAX_HOLD_MIN
from .trailing_only_sweep import simulate
from .free_signal_screen import ET

FROZEN_COMMIT="2aa21568ff72e4cafbf718e75084d3c0fa4ee83b"
FROZEN_POLICY="blend_trend_or_stretch_cost_aware"
REQUIRED_BAR_EXTRA=("volume_ratio","realized_volatility")
REQUIRED_QUOTE_EXTRA=("implied_volatility",)
MAX_QUOTE_DELAY_MINUTES=(1,2)
FEE_STRESS_PER_TRADE=2.0


def _open(path:Path):
    return gzip.open(path,"rt",newline="") if path.suffix==".gz" else path.open("r",newline="")


def file_for(root:Path,stem:str)->Path|None:
    for suffix in (".csv.gz",".csv"):
        p=root/(stem+suffix)
        if p.is_file():return p
    return None


def _extra(root:Path,stem:str,tskey:str,columns:tuple[str,...],symkey:str|None=None)->dict:
    path=file_for(root,stem)
    if path is None:raise FileNotFoundError(f"missing {stem}")
    with _open(path) as fd:
        reader=csv.DictReader(fd)
        missing=(set(columns)|{tskey}|({symkey} if symkey else set()))-set(reader.fieldnames or ())
        if missing:raise ValueError(f"{stem} missing as-of fields: {sorted(missing)}")
        gathered={}
        for row in reader:
            t=aware(row[tskey])
            key=(t,row[symkey].replace(" ","")) if symkey else t
            if key in gathered:raise ValueError(f"duplicate as-of observation in {stem}")
            vals={name:float(row[name]) for name in columns}
            if not all(math.isfinite(v) and v>=0 for v in vals.values()):
                raise ValueError("invalid as-of volatility/RVOL")
            gathered[key]=vals
    return gathered


def latest_legacy_day(root:Path)->date:
    files=_research_files(root)
    if not files:raise ValueError("no archive available to establish untouched-session cutoff")
    return max(date.fromisoformat(x.name[9:19]) for x in files)


def session_plan(root:Path,day:date)->tuple[list[dict],list[dict],dict]:
    """As-of events from true OHLCV and independently observed event option quotes."""
    session=load_session(root,day) # strict accepted event-only quote schema
    bars=_extra(root,f"spy_bars_{day}","ts_start",REQUIRED_BAR_EXTRA)
    opts=_extra(root,f"spy_option_quotes_{day}","observed_at",REQUIRED_QUOTE_EXTRA,"symbol")
    frames=[]
    for bar in session.bars:
        addon=bars[bar.start]
        frames.append(SimpleNamespace(
            timestamp=bar.start,
            market=SimpleNamespace(spot=bar.close,
                volume_ratio=addon["volume_ratio"],
                realized_volatility=addon["realized_volatility"]),
        ))
    events=identify_setups(frames)
    decisions=[]
    possible=[]
    for e in events:
        if e["setup"] not in ("trend_reclaim","stretch_reclaim"):
            continue
        signaltime=e["at"]
        lo=signaltime+timedelta(minutes=MAX_QUOTE_DELAY_MINUTES[0])
        hi=signaltime+timedelta(minutes=MAX_QUOTE_DELAY_MINUTES[1])
        # Note: true observed event quote; require no future-first
        # selection beyond the predeclared 2-minute entry window.
        good=[q for q in session.quotes if
            lo<=q.observed_at<=hi and q.right==e["side"]
            and q.ask>q.bid>0 and .40<=abs(q.delta)<=.60 and q.bid_size>=1
            and q.ask_size>=1]
        seen_at=min((q.observed_at for q in good),default=None)
        if seen_at is None:
            decisions.append({"signal_at":e["at"].isoformat(),
                "setup":e["setup"],"reason":"NO_VALID_FRESH_EVENT_QUOTE",
                "selected":False})
            continue
        # Choose ONLY same earliest observed quote-time candidates,
        # no selecting based on later spread improvement or exit labels.
        quotes=[q for q in good if q.observed_at==seen_at]
        q=min(quotes,key=lambda x:(abs(abs(x.delta)-.5),
                                    (x.ask-x.bid)/(x.ask+x.bid),x.symbol))
        mid=(q.ask+q.bid)/2
        spreadfrac=(q.ask-q.bid)/mid
        iv=opts[(q.observed_at,q.symbol)]["implied_volatility"]
        at_vol=next((f.market.realized_volatility for f in reversed(frames)
                     if f.timestamp+timedelta(minutes=1)<=e["at"]),None)
        if at_vol is None or at_vol<=0:
            decisions.append({"signal_at":e["at"].isoformat(),
                "setup":e["setup"],"reason":"NO_ASOF_SPY_REALIZED_VOL",
                "selected":False})
            continue
        gate=spreadfrac<=.035 and iv/at_vol<=1.5
        decisions.append({"signal_at":e["at"].isoformat(),
            "setup":e["setup"],"option":q.symbol,
            "quote_observed_at":seen_at.isoformat(),
            "bid":q.bid,"ask":q.ask,
            "option_iv":iv,"spy_realized_volatility":at_vol,
            "spread_fraction":spreadfrac,
            "reason":"PASS_COST_GATE" if gate else "FAIL_COST_GATE",
            "selected":gate})
        if not gate:continue
        possible.append({"signal":e,"option":q,
            "debit":round((q.ask+.01)*100+.65,2)})
    return decisions,possible,{
        "loaded_completed_ohlcv_bars":len(session.bars),
        "loaded_event_nbbo_options":len(session.quotes),
        "asof_scanned_signals":len(events),
    }


def replay_session(possible:list[dict],quotes,settled:float)->tuple[list[dict],float,float,list[dict]]:
    """One contract at a time, actual SELLABLE BID paper replay; no fills."""
    pending=0.
    flat=None
    results=[]
    decisions=[]
    for c in sorted(possible,key=lambda x:(
        x["option"].observed_at,x["signal"]["at"],
        x["option"].symbol)):
        q=c["option"];start=q.observed_at;debit=c["debit"]
        if flat is not None and start<=flat:
            decisions.append({"reason":"ONE_POSITION_BUSY","entry_at":start.isoformat()})
            continue
        if debit>settled+1e-9:
            decisions.append({"reason":"INSUFFICIENT_T1_SETTLED_CASH",
                "entry_at":start.isoformat(),"required_usd":debit})
            continue
        # This is a paper ENTRY only. Unknown future bid is NOT used
        # to choose or reject a quote at the decision instant.
        settled-=debit
        due=start+timedelta(minutes=MAX_HOLD_MIN)
        path=[(x.observed_at,x.bid) for x in quotes if
            x.symbol==q.symbol and start<x.observed_at<=due
            and x.bid>=0 and x.ask>=x.bid]
        # Require a near-due liquidatable quote rather than cherry-pick
        # last observed favorable bid. Unscorable paper event treated
        # as unknown outcome, not booked as a free refund/gain.
        if not path or due-path[-1][0]>timedelta(seconds=30):
            results.append({"entry_at":start.isoformat(),"symbol":q.symbol,
                "reason":"UNSCOREABLE_EXIT_EVENT_DATA",
                "entry_debit_usd":debit,"pnl_usd":None})
            flat=due
            continue
        paper=simulate(q.ask,path,TRAIL_PCT)
        pnl=paper["pnl_usd"]
        pending+=max(0.,debit+pnl)
        flat=datetime.fromisoformat(paper["exit_time"])
        results.append({"entry_at":start.isoformat(),
            "signal_at":c["signal"]["at"].isoformat(),
            "symbol":q.symbol,"side":q.right,"setup":c["signal"]["setup"],
            "entry_debit_usd":debit,"exit_at":paper["exit_time"],
            "exit_reason":paper["exit_reason"],
            "pnl_usd":pnl,
            "after_extra_2usd_friction_usd":round(pnl-FEE_STRESS_PER_TRADE,2)})
    return results,settled,pending,decisions


def run(root:Path,legacy:Path,output:Path)->dict:
    cutoff=latest_legacy_day(legacy)
    available=available_sessions(root)
    old=[str(x) for x in available if x<=cutoff]
    fresh=[x for x in available if x>cutoff]
    settled=START_CASH
    pending=0.
    all_decisions=[]
    trades=[]
    errors={}
    processed=[]
    for day in fresh:
        try:
            daily,possible,coverage=session_plan(root,day)
        except (ValueError,FileNotFoundError,KeyError) as exc:
            errors[str(day)]=str(exc)
            continue
        settled+=pending
        pending=0.
        from .weekly_options_data import load_session
        quotes=load_session(root,day).quotes
        out,settled,pending,execution=replay_session(possible,quotes,settled)
        all_decisions.extend([{"day":str(day),**d} for d in daily+execution])
        trades.extend([{"day":str(day),**d} for d in out])
        processed.append({"day":str(day),**coverage,
                          "paper_positions":len(out)})
    scored=[x for x in trades if x["pnl_usd"] is not None]
    unscored=[x for x in trades if x["pnl_usd"] is None]
    report={
        "name":"frozen_regime_reclaim_true_event_forward_replay",
        "frozen_git_sha":FROZEN_COMMIT,
        "frozen_arm":FROZEN_POLICY,
        "frozen_start_cash_usd":START_CASH,
        "frozen_exit":{"trail_pct":TRAIL_PCT,"hard_pct":HARD_STOP_PCT,
             "hold_minutes":MAX_HOLD_MIN,"take_profit":False},
        "last_inspected_legacy_date":str(cutoff),
        "excluded_reused_or_pre_cutoff_event_sessions":old,
        "new_event_sessions_available":[str(d) for d in fresh],
        "processed":processed,"errors":errors,
        "strict_event_readiness":audit(root),
        "status":("BLOCKED_NO_NEW_EVENT_SESSIONS" if not fresh
                  else "BLOCKED_INVALID_ASOF_FEATURES" if not processed
                  else "INCOMPLETE_UNSCOREABLE_QUOTES" if unscored
                  else "EXPLORATORY_FORWARD_PAPER_REPLAY_COMPLETED"),
        "candidate_decisions":len(all_decisions),
        "paper_positions":len(trades),"scorable_positions":len(scored),
        "unscorable_positions":len(unscored),
        "modeled_one_contract_pnl_usd":round(
            sum(x["pnl_usd"] for x in scored),2),
        "hypothetical_equity_usd":round(settled+pending,2),
        "not_real_broker_fills":True,
        "no_autonomous_trading":True,"cost_purchase":0,"broker_orders":0,
        "limitations":[
            "Only genuinely new days strictly after previously inspected archive max date qualify.",
            "Strict event schema and as-of volume_ratio, SPY realized-vol and option IV are required; never inferred from CBBO buckets.",
            "Offline shadow replay after new session files are uploaded, not a streaming live paper broker.",
            "Instruments are sampled as-of reported event timestamps; source provenance cannot be externally verified.",
            "Missing exit path leaves premium reserved and return unscored, not a fictitious profitable position.",
            "No real exchange execution, spread depth guarantee or exercise/assignment outcome.",
        ],
    }
    output.mkdir(parents=True,exist_ok=True)
    (output/"manifest.json").write_text(json.dumps(report,indent=2,sort_keys=True)+"\n")
    def write_csv(path,rows,columns):
        with (output/path).open("w",newline="") as f:
            fieldnames=sorted(set(columns)|{k for row in rows for k in row})
            w=csv.DictWriter(f,fieldnames=fieldnames)
            w.writeheader();w.writerows(rows)
    write_csv("decisions.csv",all_decisions,("day","reason","signal_at"))
    write_csv("paper_positions.csv",trades,("day","entry_at","pnl_usd"))
    print("FROZEN_FORWARD_MANIFEST "+json.dumps(report,sort_keys=True),flush=True)
    return report


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--event-dir",type=Path,default=Path("/data/research/forward_events"))
    p.add_argument("--legacy-dir",type=Path,default=Path("/data/research"))
    p.add_argument("--output-dir",type=Path,default=Path("/data/research/frozen_forward"))
    x=p.parse_args()
    run(x.event_dir,x.legacy_dir,x.output_dir)


if __name__=="__main__":
    main()
