"""Exploratory V4 entry-quality sizing: entry/exit frozen, contract count varies.

NO historical tuning claim: deterministic precommitted rubric is applied to the
previously examined 128-session minute CBBO dataset. Market/contract features
must exist at the signal/entry timestamp. Output includes bucket diagnostics
and preserves a separate T+1 settled-cash ledger for each arm.

No orders, paid data calls, or modification to live execution.
"""
from __future__ import annotations
import argparse
import csv
import json
from collections import Counter
from datetime import timedelta
from pathlib import Path
from .burst_research import _research_files
from .data import load_canonical_path
from .regime_hold_sensitivity import (
    selection, quote_path, liquidation_cutoff, START_CASH, MAX_FIXED_MIN,
)
from .regime_hold_conditional import simulate_conditional
from .v4_dynamic_size_backtest import summary
from .v4_compound_sizing import adaptive_quantity, unrestricted_research_quantity
from .weekly_options_data import NY

ARMS=("v4_fixed_one","v4_quality_2x","v4_quality_3x","v4_quality_2x_no_4pct_dd_gate",
      "v4_adaptive_compound_guarded","v4_unrestricted_cash_stress_RESEARCH_ONLY")
PREMIUM_EXTRAS_FRACTION=.35
MAX_DRAWDOWN_FOR_EXTRA=.04
MAX_SETUP_SCORE=6

def score_at_entry(item, frames):
    """Static additive features known no later than first selected option quote.

    Signal trend/efficiency/RVOL from completed SPY minute; selected q spread,
    delta and IV/RV from selected as-of first quote; no future candle or outcome.
    """
    event=item["signal"]
    q=item["quote"]
    market=frames[item["entry_idx"]].market
    result=0
    # A larger trend is not inherently better for reversals. Keep separate
    # directional tests and do not infer a fitted success probability.
    if event["setup"]=="trend_reclaim":
        result+=int(abs(event["trend_bps"])>=4.)
        result+=int(event["efficiency"]>=.30)
    else:
        result+=int(abs(event["trend_bps"])<=6.)
        result+=int(event["efficiency"]<=.40)
    result+=int(event["volume_ratio"]>=1.20)
    result+=int(q.spread_fraction<=.020)
    result+=int(.45<=abs(q.delta)<=.55)
    result+=int(
        market.realized_volatility>0 and
        q.implied_volatility/market.realized_volatility<=1.25)
    if not 0<=result<=MAX_SETUP_SCORE:
        raise AssertionError("quality score out of range")
    return result


def size(arm, *, score, equity, settled, peak, debit):
    """V4 baseline one contract if affordable; optional extras subject to rails."""
    if arm not in ARMS:
        raise ValueError("unknown quality sizing arm")
    if settled<debit or equity<=0 or peak<=0 or debit<=0:
        return 0
    if arm=="v4_fixed_one":
        return 1
    if arm=="v4_adaptive_compound_guarded":
        return adaptive_quantity(score=score,equity=equity,settled=settled,
            peak=peak,debit=debit,starting_cash=START_CASH,
            exposure=.35,max_contracts=10000,bid_size=10000,ask_size=10000)
    if arm=="v4_unrestricted_cash_stress_RESEARCH_ONLY":
        return unrestricted_research_quantity(settled=settled,debit=debit)
    if arm.startswith("v4_quality_3x"):
        target=3 if score>=5 else 2 if score>=4 else 1
    else:
        target=2 if score>=4 else 1
    if target==1:
        return 1
    current_dd=max(0.,1-equity/peak)
    if arm!="v4_quality_2x_no_4pct_dd_gate" and current_dd>MAX_DRAWDOWN_FOR_EXTRA:
        return 1
    cap=min(int((PREMIUM_EXTRAS_FRACTION*equity+1e-8)//debit),
            int((settled+1e-8)//debit))
    # The 35% premium-at-risk cap only constrains EXTRA exposure; baseline
    # one-contract V4 remains unchanged (cannot retroactively erase signals).
    return max(1,min(target,cap))


def run(data_dir:Path,out_dir:Path):
    files=_research_files(data_dir)
    if not files:
        raise ValueError("missing real inspected SPY archive")
    states={arm:{"settled":START_CASH,"pending":0.,"trades":[],
                 "daily":[],"skips":Counter(),"exits":Counter()}
            for arm in ARMS}
    buckets={str(i):{"signals":0,"winners":0,"pnl_sum":0.,
                     "premium_sum":0.} for i in range(MAX_SETUP_SCORE+1)}
    dates=[]
    for file in files:
        frames=list(load_canonical_path(file))
        if not frames: continue
        day=frames[0].timestamp.astimezone(NY).date()
        dates.append(str(day))
        candidates=selection(frames)
        cutoff=liquidation_cutoff(day)
        for arm,state in states.items():
            state["settled"]+=state["pending"]
            state["pending"]=0.
            open_equity=state["settled"]
            peak=max([START_CASH]+[d["equity_usd"] for d in state["daily"]])
            flat=None
            for item in candidates:
                at=item["entry_at"]
                if at>=cutoff-timedelta(minutes=MAX_FIXED_MIN+1):
                    state["skips"]["LATE_ENTRY"]+=1
                    continue
                if flat is not None and at<=flat:
                    state["skips"]["POSITION_BUSY"]+=1
                    continue
                q=item["quote"]
                debit=item["debit"]
                score=score_at_entry(item,frames)
                n=size(arm,score=score,equity=open_equity,
                       settled=state["settled"],peak=peak,debit=debit)
                if n<1:
                    state["skips"]["INSUFFICIENT_SETTLED_CASH"]+=1
                    continue
                if n*debit>state["settled"]+1e-7:
                    raise AssertionError("borrowed cash or sold funds reused")
                outcome=simulate_conditional(q.ask,
                    quote_path(frames,item["entry_idx"],q.symbol,cutoff),
                    frames,entry_at=at,side=item["signal"]["side"],
                    symbol=q.symbol,cutoff=cutoff)
                state["settled"]=round(state["settled"]-n*debit,8)
                row={"day":str(day),"arm":arm,"symbol":q.symbol,
                     "entry_at":at.isoformat(),"setup":item["signal"]["setup"],
                     "quality_score":score,"contracts":n,
                     "unit_entry_debit_usd":debit,"position_cost_usd":round(n*debit,2)}
                if outcome is None:
                    flat=cutoff
                    state["trades"].append({**row,"pnl_usd":None,
                        "exit_reason":"UNKNOWN_QUOTE_EXIT_RESERVED"})
                    continue
                unit=outcome["pnl_usd"]
                pnl=round(unit*n,6)
                state["pending"]+=max(0.,n*debit+pnl)
                flat=outcome["exit_at"]
                state["exits"][outcome["exit_reason"]]+=1
                state["trades"].append({**row,"unit_pnl_usd":unit,
                    "pnl_usd":pnl,"exit_at":flat.isoformat(),
                    "exit_reason":outcome["exit_reason"]})
                if arm=="v4_fixed_one":
                    bucket=buckets[str(score)]
                    bucket["signals"]+=1
                    bucket["winners"]+=int(unit>0)
                    bucket["pnl_sum"]+=unit
                    bucket["premium_sum"]+=debit
            state["daily"].append({"day":str(day),
                "equity_usd":round(state["settled"]+state["pending"],4)})
    results={arm:summary(state) for arm,state in states.items()}
    diagnostics={}
    for name,b in buckets.items():
        count=b["signals"]
        diagnostics[name]={
            "scored_trades":count,
            "win_pct":round(b["winners"]*100/count,2) if count else None,
            "mean_pnl_usd":round(b["pnl_sum"]/count,2) if count else None,
            "sum_pnl_usd":round(b["pnl_sum"],2)}
    report={
        "study":"V4 entry-quality-only integer sizing sensitivity",
        "status":"RETROSPECTIVE_INSPECTED_MINUTE_BUCKET_PROXY_NO_LIVE_PROMOTION",
        "first_day":min(dates) if dates else None,
        "last_day":max(dates) if dates else None,
        "sessions":len(dates),"start_cash_usd":START_CASH,
        "entry_exit_unchanged":True,
        "score_rule":"six additive as-of attributes: setup direction trend, efficiency, RVOL>=1.2, spread<=2%, 0.45<=abs(delta)<=0.55, IV/RV<=1.25",
        "extras_trigger":"quality>=4 for 2x, >=5 for 3x",
        "extra_contracts_premium_cap_pct_of_open_equity":35,
        "extra_contracts_drawdown_soft_veto_at_pct":4,
        "one_open_position_at_a_time":True,
        "new_arms":"guarded adaptive compounding and unrestricted settled-cash stress (RESEARCH ONLY)",
        "settlement":"T+1, no same-day proceeds reuse, independent cash accounts",
        "costs":"$0.65 fee per side and $0.01/share slippage per contract side; +$2/contract stress in results",
        "max_drawdown":"end-of-day modeled account equity, not intraday maximum",
        "results":results,
        "fixed_one_score_buckets_exploratory":diagnostics,
        "no_broker_orders":True,"new_data_purchases":0,
        "warning":"These same 128 sessions were inspected for prior V4 studies. The score rubric has NOT been calibrated or validated out of sample. OPRA CBBO-minute snapshots are NOT executable event-time NBBO. Higher position size can worsen fills and unmodeled intraday losses.",
    }
    out_dir.mkdir(parents=True,exist_ok=True)
    (out_dir/"report.json").write_text(json.dumps(report,sort_keys=True,indent=2)+"\n")
    with (out_dir/"trades.csv").open("w",newline="") as f:
        rows=[v for state in states.values() for v in state["trades"]]
        cols=sorted({"day","arm","pnl_usd","contracts","quality_score"}|
                    {key for row in rows for key in row})
        w=csv.DictWriter(f,fieldnames=cols)
        w.writeheader();w.writerows(rows)
    print("V4_ENTRY_QUALITY_SIZING_RESULT "+json.dumps(report,sort_keys=True),flush=True)
    return report

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--data-dir",type=Path,default=Path("/data/research"))
    p.add_argument("--output-dir",type=Path,default=Path("/data/research/v4_quality_size"))
    x=p.parse_args()
    run(x.data_dir,x.output_dir)

if __name__=="__main__":
    main()
