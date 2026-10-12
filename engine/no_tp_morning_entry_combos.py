"""SPY 09:30-11:00 0DTE: predeclared entry combinations, NO fixed profit targets.

This research is exploratory, uses the previously inspected 128 days, and
NEVER changes production/paper/live trading. All strategies keep the SAME
30% whole-position trailing stop, independent 35% hard-loss exit and
10 minute hold limit, WITHOUT take-profit orders, scale-outs or runners.

Causal input: only completed SPY 1-minute closes and volume ratios, plus the
first observed sampled option quote. Historical CBBO-1m quote buckets are
NOT true event-time NBBO; option fills are modeled, not observed.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, time
from pathlib import Path

from .burst_research import _research_files
from .data import load_canonical_path
from .exit_policy_comparison import _select_entry, _sampled_path
from .free_signal_screen import ET, _bps
from .morning_0930_1100_study import opening_events, nonoverlap, in_window
from .trailing_only_sweep import simulate, period

TRAIL_PCT=30
INDEPENDENT_HARD_STOP_PCT=35
MAX_HOLD_MIN=10
START=time(9,30)
END=time(11,0)
EXTRA_EXECUTION_STRESS_USD=2.00
RULES=(
    "opening_breakout_base",
    "opening_volume_200",
    "opening_directional_efficiency_55",
    "opening_rvol_accelerating",
    "opening_5min_drive_5bps",
    "opening_volume200_efficiency55",
    "opening_spread_08",
    "opening_volume200_spread08",
    "opening_efficiency55_spread08",
    "opening_0946_to_1030",
    "opening_pause_reclaim",
    "opening_pause_reclaim_efficiency55",
)
SEGMENTS=("development","validation","previously_studied_diagnostic","all_inspected")


def direction_sign(right:str)->int:
    return 1 if right=="call" else -1


def signal_context(frames,at_idx:int,side:str)->dict:
    """All spot and relative volume inputs were completed BEFORE signal."""
    if at_idx<15:
        raise ValueError("insufficient history")
    close=[f.market.spot for f in frames[at_idx-15:at_idx+1]]
    volume=[f.market.volume_ratio for f in frames[at_idx-5:at_idx+1]]
    if not all(math.isfinite(x) and x>0 for x in close):
        raise ValueError("invalid completed spot prices")
    if not all(math.isfinite(x) for x in volume):
        raise ValueError("invalid completed volume ratios")
    sign=direction_sign(side)
    five=sign*_bps(close[-1],close[-6])
    ten=sign*_bps(close[-1],close[-11])
    total=sum(abs(_bps(close[j],close[j-1])) for j in range(1,len(close)))
    signed15=sign*_bps(close[-1],close[0])
    return {
        "signed_5min_bps":five,
        "signed_10min_bps":ten,
        "signed_15min_bps":signed15,
        "trend_efficiency_15min":signed15/total if total>0 else 0.,
        "rvol_acceleration_ratio":volume[-1]/max(0.01,statistics.fmean(volume[:-1])),
    }


def pause_reclaim_events(frames)->list[dict]:
    """New independent as-of opening trend + 5-close pullback reclaim.

    Only CLOSES exist in legacy SPY canonical data, so do not call this
    a real OHLC pullback, VWAP or footprint/microstructure strategy.
    """
    if not frames:return []
    events=[]
    closes=[f.market.spot for f in frames]
    for i in range(20,len(frames)):
        at=frames[i].timestamp+timedelta(minutes=1)
        if not in_window(at):
            continue
        if any((frames[j+1].timestamp-frames[j].timestamp).total_seconds()!=60
               for j in range(i-20,i)):
            continue
        if any(not math.isfinite(x) or x<=0 for x in closes[i-20:i+1]):
            continue
        vol=frames[i].market.volume_ratio
        if not math.isfinite(vol) or vol<1.50:
            continue
        last=closes[i]
        prev=closes[i-1]
        prev5=statistics.fmean(closes[i-6:i-1])
        current5=statistics.fmean(closes[i-5:i])
        rising=_bps(last,closes[i-15])>=8.
        falling=_bps(last,closes[i-15])<=-8.
        side=None
        if rising and prev<=prev5 and last>current5 and last>prev:
            side="call"
        elif falling and prev>=prev5 and last<current5 and last<prev:
            side="put"
        if side:
            events.append({
                "signal_at":at.isoformat(),
                "right":side,"origin":"pause_reclaim",
                "volume_ratio":vol,
            })
    return events


def admissible(event:dict,ctx:dict,quote,variant:str)->bool:
    """Never inspect option exit P&L to choose among candidate entries."""
    if variant not in RULES:
        raise ValueError("unknown entry combination")
    is_open=event["origin"]=="opening_close_breakout_proxy"
    is_pause=event["origin"]=="pause_reclaim"
    time_et=datetime.fromisoformat(event["signal_at"]).astimezone(ET).time()
    vol=event["volume_ratio"]
    spread=quote.spread_fraction
    eff=ctx["trend_efficiency_15min"]
    accel=ctx["rvol_acceleration_ratio"]
    five=ctx["signed_5min_bps"]
    return {
        "opening_breakout_base":is_open,
        "opening_volume_200":is_open and vol>=2.,
        "opening_directional_efficiency_55":is_open and eff>=.55,
        "opening_rvol_accelerating":is_open and accel>=1.15,
        "opening_5min_drive_5bps":is_open and five>=5.,
        "opening_volume200_efficiency55":is_open and vol>=2. and eff>=.55,
        "opening_spread_08":is_open and spread<=.08,
        "opening_volume200_spread08":is_open and vol>=2. and spread<=.08,
        "opening_efficiency55_spread08":is_open and eff>=.55 and spread<=.08,
        "opening_0946_to_1030":is_open and time(9,46)<=time_et<time(10,30),
        "opening_pause_reclaim":is_pause,
        "opening_pause_reclaim_efficiency55":is_pause and eff>=.55,
    }[variant]


def stats(ledger:list[dict],dates:list[str],variant:str,regime:str)->dict:
    rows=[x for x in ledger if x["variant"]==variant and
          (regime=="all_inspected" or period(x["day"])==regime)]
    calendar=[d for d in dates if regime=="all_inspected" or period(d)==regime]
    realized=0.
    peak=0.
    dd=0.
    pnl=[row["pnl_usd"] for row in rows]
    ret=[row["option_premium_return_pct"] for row in rows]
    daily=defaultdict(float)
    for row in rows:
        val=row["pnl_usd"]
        daily[row["day"]]+=val
        realized+=val
        peak=max(peak,realized)
        dd=max(dd,peak-realized)
    return {
        "sessions":len(calendar),"trades":len(rows),
        "wins":sum(x>0 for x in pnl),
        "win_rate_pct":round(100*sum(x>0 for x in pnl)/len(pnl),2) if pnl else None,
        "mean_option_premium_return_pct":round(statistics.fmean(ret),3) if ret else None,
        "hypothetical_net_one_contract_sum_usd":round(sum(pnl),2),
        "additional_friction_stressed_sum_usd":round(
            sum(pnl)-EXTRA_EXECUTION_STRESS_USD*len(pnl),2),
        "positive_days":sum(daily[d]>0 for d in calendar),
        "negative_days":sum(daily[d]<0 for d in calendar),
        "zero_or_no_trade_days":sum(daily[d]==0 for d in calendar),
        "closed_trade_additive_drawdown_not_account_usd":round(dd,2),
    }


def robustness(ledger:list[dict],dates:list[str],variant:str,reps:int=1200)->dict:
    daily=defaultdict(float)
    frequency=defaultdict(int)
    for case in ledger:
        if case["variant"]==variant:
            daily[case["day"]]+=case["pnl_usd"]
            frequency[case["day"]]+=1
    raw=[daily[d] for d in dates]
    stressed=[daily[d]-EXTRA_EXECUTION_STRESS_USD*frequency[d] for d in dates]
    random_gen=random.Random(93711)
    def block_ci(series):
        totals=[]
        for _ in range(reps):
            drawn=[]
            while len(drawn)<len(series):
                a=random_gen.randrange(len(series))
                drawn.extend(series[(a+k)%len(series)] for k in range(5))
            totals.append(statistics.fmean(drawn[:len(series)]))
        totals.sort()
        return [round(totals[int(.025*reps)],3),
                round(totals[int(.975*reps)-1],3)]
    if not dates:
        return {"status":"no_history"}
    biggest=sorted(((daily[d],d) for d in dates),reverse=True)
    return {
        "best_day":biggest[0][1],"best_day_pnl":round(biggest[0][0],2),
        "net_sum_without_best_day_usd":round(
            sum(raw)-biggest[0][0],2),
        "net_sum_without_best_three_days_usd":round(
            sum(raw)-sum(v for v,_ in biggest[:3]),2),
        "raw_block_bootstrap_95pct_usd_per_session":block_ci(raw),
        "friction_stressed_block_bootstrap_95pct_usd_per_session":
            block_ci(stressed),
        "exploratory_multiple_comparison_corrected":False,
        "genuinely_unseen_sessions":False,
    }


def run(root:Path,output:Path)->dict:
    records=[]
    diagnostics=defaultdict(int)
    dates=[]
    for file in _research_files(root):
        frames=list(load_canonical_path(file))
        if not frames: continue
        date=frames[0].timestamp.astimezone(ET).date().isoformat()
        dates.append(date)
        b=opening_events(frames)
        r=pause_reclaim_events(frames)
        events=b+r
        by_completed={fr.timestamp+timedelta(minutes=1):i
                      for i,fr in enumerate(frames)}
        # Resolve entry observations as-of modeled entry; not future
        # liquidations. A missing entry quote cancels before locking.
        prepped=[]
        for event in events:
            t=datetime.fromisoformat(event["signal_at"])
            at_idx=by_completed.get(t)
            if at_idx is None or at_idx<20:
                diagnostics["invalid_asof_signal"]+=1
                continue
            entry=_select_entry(frames,t,event["right"])
            if entry is None:
                diagnostics["no_entry_eligible_quote"]+=1
                continue
            entry_index,quote=entry
            context=signal_context(frames,at_idx,event["right"])
            prepped.append({
                "event":event,"context":context,
                "entry_index":entry_index,"quote":quote,
            })
        diagnostics["opening_breakout_candidates"]+=len(b)
        diagnostics["pause_reclaim_candidates"]+=len(r)
        diagnostics["eligible_quoted_candidates"]+=len(prepped)
        for variant in RULES:
            filtered=[
                row for row in prepped
                if admissible(row["event"],row["context"],row["quote"],variant)
            ]
            # Non-overlap independent per arm, based on signal time.
            # Quote availability is known at entry; future exit quote
            # availability NEVER decides which opportunity locks others.
            filtered.sort(key=lambda row:(
                row["event"]["signal_at"],row["event"]["origin"],
                row["event"]["right"]))
            lock=None
            for item in filtered:
                event=item["event"]
                entrytime=datetime.fromisoformat(event["signal_at"])+timedelta(minutes=1)
                if lock is not None and entrytime<=lock:
                    continue
                lock=entrytime+timedelta(minutes=10)
                diagnostics[variant+"_accepted"]+=1
                path=_sampled_path(frames,item["entry_index"],item["quote"].symbol)
                if path is None:
                    diagnostics[variant+"_missing_complete_exit_path"]+=1
                    continue
                quote=item["quote"]
                outcome=simulate(quote.ask,path,TRAIL_PCT)
                records.append({
                    "day":date,"variant":variant,
                    "signal_at":event["signal_at"],
                    "side":event["right"],"origin":event["origin"],
                    "symbol":quote.symbol,
                    "relative_volume":round(event["volume_ratio"],4),
                    "spread_fraction":round(quote.spread_fraction,6),
                    "signed_five_minute_spy_move_bps":round(
                        item["context"]["signed_5min_bps"],4),
                    "directional_efficiency":round(
                        item["context"]["trend_efficiency_15min"],4),
                    "pnl_usd":outcome["pnl_usd"],
                    "option_premium_return_pct":outcome["return_on_premium_pct"],
                    "exit_reason":outcome["exit_reason"],
                })
        diagnostics["archived_minute_frames"]+=len(frames)
    dates=sorted(set(dates))
    report={
        "study":"NO_FIXED_PROFIT_TARGET_0930_1100_SPY_OPTION_ENTRY_COMBINATIONS",
        "research_only":True,
        "time_window_ET":"09:30–11:00 inclusive opening, exclusive closing time",
        "exit":{"trailing_stop_pct":TRAIL_PCT,
                "independent_hard_stop_pct":INDEPENDENT_HARD_STOP_PCT,
                "max_hold_minutes":MAX_HOLD_MIN,
                "fixed_take_profit":False,
                "partial_position_take_profit":False,
                "runner":False},
        "archive_sessions":len(dates),
        "prior_legacy_CBBO_1m_not_true_event_nbbo":True,
        "full_chain_asof_put_call_volume_not_available":True,
        "not_new_out_of_sample_data":True,
        "live_or_paper_orders_submitted":False,
        "external_market_data_purchases":False,
        "additional_roundtrip_cost_stress_per_contract_usd":
            EXTRA_EXECUTION_STRESS_USD,
        "hypotheses_evaluated":len(RULES),
        "coverage":dict(diagnostics),
        "rules":{name:{
            "by_period":{group:stats(records,dates,name,group)
                         for group in SEGMENTS},
            "robustness":robustness(records,dates,name),
        } for name in RULES},
        "warnings":[
            "All strategies were explored on data already inspected in earlier studies.",
            "Multiple combinations tested. Strongest apparent result may be selection noise.",
            "Percent premium return is not the same as one-contract net dollar P&L.",
            "Sampled CBBO minute quotes and prices may not reflect executable fills.",
            "Full-chain put/call volume at the signal time is not present.",
            "These are individual simulated one-contract returns, not a funded account simulation.",
        ],
    }
    output.mkdir(parents=True,exist_ok=True)
    (output/"report.json").write_text(json.dumps(report,sort_keys=True,indent=2)+"\n")
    with (output/"trade_ledger.csv").open("w",newline="") as file:
        columns=["day","variant","signal_at","side","origin","symbol",
                 "relative_volume","spread_fraction",
                 "signed_five_minute_spy_move_bps","directional_efficiency",
                 "pnl_usd","option_premium_return_pct","exit_reason"]
        writer=csv.DictWriter(file,fieldnames=columns)
        writer.writeheader();writer.writerows(records)
    print("NO_TP_COMBOS_RESULT "+json.dumps(report,sort_keys=True),flush=True)
    return report


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--data-dir",type=Path,default=Path("/data/research"))
    parser.add_argument("--output-dir",type=Path,default=Path("/data/research/no_tp_entry_combos"))
    args=parser.parse_args()
    run(args.data_dir,args.output_dir)


if __name__=="__main__":
    main()
