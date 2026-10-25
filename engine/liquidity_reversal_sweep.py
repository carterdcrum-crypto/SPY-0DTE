"""Isolated close-only liquidity-reversal sensitivity; no broker or data API."""
from __future__ import annotations
import json, math, os, statistics
from collections import Counter
from datetime import timedelta
from pathlib import Path
from .burst_research import _research_files
from .data import load_canonical_path
from .regime_hold_sensitivity import liquidation_cutoff, quote_path
from .regime_reclaim_frozen_signal import ema
from .weekly_options_data import NY
from .v5_archive_proxy import proxy_candidates, proxy_exit, ARMS
from quantconnect.v5_core import Signal

CONFIGS = {
 "baseline": (0., 0., 1.25, .60, False),
 "deeper_reclaim_2bps": (.0002, 0., 1.25, .60, False),
 "larger_sweep_2bps": (0., .0002, 1.25, .60, False),
 "higher_rvol": (0., 0., 1.50, .60, False),
 "lower_efficiency": (0., 0., 1.25, .40, False),
 "one_bar_confirmation": (0., 0., 1.25, .60, True),
}

def signals(frames, i, *, reclaim=0., sweep=0., min_rvol=1.25, max_eff=.60, confirm=False, challenger=True):
    if i<56:
        return ()
    if confirm:
        previous=signals(frames,i-1,reclaim=reclaim,sweep=sweep,min_rvol=min_rvol,max_eff=max_eff)
        if not previous:
            return ()
        sig=previous[0]
        now=frames[i].market.spot;last=frames[i-1].market.spot
        if (frames[i].timestamp-frames[i-1].timestamp).total_seconds()!=60 or frames[i].timestamp.astimezone(NY).date()!=frames[i-1].timestamp.astimezone(NY).date():
            return ()
        if not math.isfinite(now) or now<=0:
            return ()
        if now<last if sig.side=="put" else now>last:
            return previous
        return ()
    window=frames[i-56:i+1]
    day=frames[i].timestamp.astimezone(NY).date()
    if any(f.timestamp.astimezone(NY).date()!=day or not math.isfinite(f.market.spot) or f.market.spot<=0 for f in window):
        return ()
    if any((b.timestamp-a.timestamp).total_seconds()!=60 for a,b in zip(window,window[1:])):
        return ()
    rvol=frames[i].market.volume_ratio
    if not math.isfinite(rvol) or rvol<min_rvol:
        return ()
    spots=[f.market.spot for f in window]
    now=spots[-1];prev=spots[-2]
    old=spots[-23:-3]
    distance=sum(abs(b-a) for a,b in zip(spots[-16:-1],spots[-15:]))
    efficiency=abs(now-spots[-16])/distance if distance else 0.
    if efficiency>=max_eff:
        return ()
    slow=ema(spots[-36:-1],21);oldslow=ema(spots[-36:-11],21)
    trend=(slow/oldslow-1)*1e4 if oldslow>0 else 0.
    if prev>max(old)*(1+sweep) and now<max(old)*(1-reclaim) and now<prev:
        return (Signal("put","liquidity_reversal_CLOSE_PROXY",rvol,trend,efficiency),)
    if prev<min(old)*(1-sweep) and now>min(old)*(1+reclaim) and now>prev:
        return (Signal("call","liquidity_reversal_CLOSE_PROXY",rvol,trend,efficiency),)
    return ()

def quantity(cash, debit, mode):
    if mode not in ("fixed_one", "settled_cash"):
        raise ValueError("unknown sizing mode")
    if not math.isfinite(cash) or not math.isfinite(debit) or debit <= 0 or cash < debit - 1e-8:
        return 0
    return 1 if mode == "fixed_one" else max(0, math.floor((cash + 1e-8) / debit))

def run():
    mode=os.environ.get("LIQUIDITY_SIZING_MODE","fixed_one")
    root=Path(os.environ.get("RESEARCH_DATA_DIR","/data/research"))
    paths=_research_files(root)
    if not paths:
        raise ValueError("No historical archive")
    states={name:dict(cash=1000.,pending=0.,peak=1000.,max_dd=0.,pnl=[],unscored=0,contracts=0,max_contracts=0,skips=Counter(),daily=[]) for name in CONFIGS}
    days=[]
    for file in paths:
        frames=list(load_canonical_path(file))
        if not frames:
            continue
        day=frames[0].timestamp.astimezone(NY).date()
        days.append(str(day))
        cutoff=liquidation_cutoff(day)
        for name,(reclaim,sweep,volume,max_eff,confirm) in CONFIGS.items():
            s=states[name];s["cash"]+=s["pending"];s["pending"]=0.;busy=None
            builder=lambda frames,i,challenger=True: signals(frames,i,reclaim=reclaim,sweep=sweep,min_rvol=volume,max_eff=max_eff,confirm=confirm)
            candidates=proxy_candidates(frames,challenger=True,signal_builder=builder)
            for item in candidates:
                at=item["entry_at"]
                if at>=cutoff-timedelta(minutes=5):
                    s["skips"]["late"]+=1;continue
                if busy is not None and at<=busy:
                    s["skips"]["busy"]+=1;continue
                debit=item["debit"]
                qty=quantity(s["cash"],debit,mode)
                if qty < 1:
                    s["skips"]["cash"]+=1;continue
                if qty*debit>s["cash"]+1e-8:
                    raise AssertionError("settled cash exceeded")
                s["cash"]-=qty*debit
                s["contracts"]+=qty;s["max_contracts"]=max(s["max_contracts"],qty)
                outcome=proxy_exit(ARMS[-1],item,frames,cutoff)
                if outcome is None:
                    terminal=[(t,b) for t,b in quote_path(frames,item["entry_idx"],item["quote"].symbol,cutoff) if cutoff<=t<=cutoff+timedelta(minutes=1)]
                    if terminal:
                        t,b=terminal[0];outcome={"exit_at":t,"pnl_usd":max(0.,(b-.01)*100-.65)-debit}
                if outcome is None:
                    s["unscored"]+=1;busy=cutoff;continue
                pnl=qty*outcome["pnl_usd"];s["pending"]+=max(0.,qty*debit+pnl)
                s["pnl"].append(pnl);busy=outcome["exit_at"]
            equity=s["cash"]+s["pending"];s["peak"]=max(s["peak"],equity)
            s["max_dd"]=max(s["max_dd"],1-equity/s["peak"])
            s["daily"].append({"day":str(day),"equity":round(equity,2)})
        print("LIQUIDITY_PROGRESS "+str(day),flush=True)
    results={}
    for name,s in states.items():
        pnl=s["pnl"];wins=sum(x>0 for x in pnl);loss=-sum(x for x in pnl if x<0)
        results[name]={"ending_equity":round(s["cash"]+s["pending"],2),"trades":len(pnl),"win_rate":round(100*wins/len(pnl),2) if pnl else None,"profit_factor":round(sum(x for x in pnl if x>0)/loss,3) if loss else None,"max_eod_drawdown_pct":round(100*s["max_dd"],2),"unscorable":s["unscored"],"contracts":s["contracts"],"max_contracts_per_trade":s["max_contracts"],"net_after_extra_2usd_per_contract":round(sum(pnl)-2*s["contracts"],2),"skips":dict(s["skips"])}
    report={"status":"REUSED_IN_SAMPLE_CLOSE_ONLY_PROXY_NOT_EXACT_V5","sessions":len(days),"first_day":min(days),"last_day":max(days),"starting_cash":1000,"sizing_mode":mode,"daily_contract_cap":None,"contracts_per_trade":1 if mode=="fixed_one" else "all_settled_cash","iv_rv_cap":1.5,"max_hold_minutes":18,"fees_per_side":.65,"slippage_per_share_per_side":.01,"results":results,"missing_real_intrabar_volume_and_executable_depth":True,"data_purchases":0,"broker_orders":0}
    dest=root/"liquidity_reversal_2026_10_25"/mode;dest.mkdir(parents=True,exist_ok=True)
    (dest/"report.json").write_text(json.dumps(report,indent=2)+"\n")
    (dest/"daily_equity.json").write_text(json.dumps({k:v["daily"] for k,v in states.items()})+"\n")
    print("LIQUIDITY_RESULT "+json.dumps(report,sort_keys=True),flush=True)

def main():
    try:
        for mode in ("fixed_one", "settled_cash"):
            os.environ["LIQUIDITY_SIZING_MODE"]=mode
            run()
    except Exception as exc:
        print("LIQUIDITY_BLOCKED "+json.dumps({"error":type(exc).__name__,"detail":str(exc)[:300]}),flush=True)
    os.execvp("python",["python","-m","engine.forward_watch"])

if __name__=="__main__":
    main()
