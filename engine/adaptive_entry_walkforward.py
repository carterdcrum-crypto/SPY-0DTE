"""Adaptive SPY 0DTE entry RESEARCH, frozen 30% full-position trailing exit.

No live/paper orders, data purchases, account changes, new backend dependencies
or trading recommendations. All training labels are learned strictly from
PRIOR completed sessions. Candidate signals and quote prices are archived
minute-bucket CBBO, NOT as-of independently observed executable event NBBO.
Complete intraday SPY option put/call volumes are NOT present.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from .burst_research import _research_files
from .data import load_canonical_path
from .exit_policy_comparison import _sampled_path, _select_entry
from .free_signal_screen import ET, _bps
from .spy_pcr_momentum_reversal import session_classifications, select
from .trailing_only_sweep import simulate, period

TRAIL_PCT = 30
PRIOR_SESSIONS = 60
MIN_TRAINING_MATCHED = 75
RANDOM_SEED = 20261011
FRICTION_STRESS_EXTRA_PER_TRADE = 2.00  # extra $0.01/option share EACH side
REGULARIZATION = 0.11  # mean-loss objective L2 penalty, not fitted
TRAIN_STEPS = 180
STEP_SIZE = 0.11
MIN_PRIOR_EXPERT = 16
EXPERTS = ("morning_high_volume_momentum", "confirmed_trend_pullback")
ARMS = (
    "both_experts_unfiltered",
    "momentum_expert_only",
    "pullback_expert_only",
    "adaptive_predicted_win_45",
    "adaptive_positive_stressed_expected_value",
    "adaptive_predicted_win_60_and_ev",
    "adaptive_predicted_win_75_and_ev",
    "adaptive_prior_profit_evidence",
)
PERIODS = ("development", "validation", "previously_studied_diagnostic", "all_inspected")


def clamp(value:float,lo:float=-3.0,hi:float=3.0)->float:
    return max(lo,min(hi,value))


def sigmoid(value:float)->float:
    z=clamp(value,-35,35)
    return 1.0/(1.0+math.exp(-z))


def expert_for(row)->str|None:
    """Only properties known by signal time."""
    if "base_four_factors_PASS" not in row.reasons:
        return None
    op=row.opportunity
    local=datetime.fromisoformat(op.signal_at).astimezone(ET)
    if op.setup=="close_breakout_baseline" and (
            row.family_mode=="momentum" and
            op.volume_ratio_at_signal>=1.5 and 10<=local.hour<12):
        return EXPERTS[0]
    if op.setup=="trend_pullback_reclaim" and (
            row.family_mode=="momentum" and
            row.price_action_confirmed and row.macd_histo_aligned_or_improving):
        return EXPERTS[1]
    return None


def causal_features(row,quote,frames,signal_index:int)->list[float]:
    """Fixed bounded, non-fitted scaling. All SPY observations <= signal bar.

    Quote properties are the first modeled option-entry quote, available
    only at the modeled entry observation, not from future trace or outcome.
    """
    op=row.opportunity
    i=signal_index
    if i<20:
        raise ValueError("not enough prior completed close history")
    closes=[f.market.spot for f in frames[i-20:i+1]]
    direction=1 if op.original_direction=="call" else -1
    market=frames[i].market
    clock=datetime.fromisoformat(op.signal_at).astimezone(ET)
    signed5=direction*_bps(closes[-1],closes[-6])
    signed15=direction*_bps(closes[-1],closes[-16])
    # Last 20 completed spot change dispersion, never future high/low.
    changes=[_bps(closes[k],closes[k-1]) for k in range(1,len(closes))]
    short_dispersion=statistics.pstdev(changes)
    return [
        1.0 if op.setup=="trend_pullback_reclaim" else -1.0,
        clamp((op.volume_ratio_at_signal-1.0)/1.0),
        clamp((row.factor_score-5)/2.0),
        1.0 if row.macd_histo_aligned_or_improving else -1.0,
        1.0 if row.price_action_confirmed else -1.0,
        clamp(signed5/10.0),
        clamp(signed15/20.0),
        clamp(short_dispersion/5.0),
        clamp((clock.hour*60+clock.minute-660)/120.0),
        clamp(quote.spread_fraction/.08),
        clamp((abs(quote.delta)-.5)/.12),
        clamp(quote.ask/3.0),
        clamp((market.implied_volatility-market.realized_volatility)/.25),
        1.0 if direction==1 else -1.0,
    ]


def train_prior_only(cases:list[dict])->tuple[list[float],dict]|None:
    """Full-batch regularized logistic fit from prior-day option wins ONLY.

    Not a guaranteed calibrated probability. No cross-session label leakage.
    """
    if len(cases)<MIN_TRAINING_MATCHED:
        return None
    k=len(cases[0]["features"])
    wins=sum(case["won"] for case in cases)
    # Keep a weak smoothed intercept if no winner class can be estimated.
    base=(wins+2)/(len(cases)+4)
    w=[math.log(base/(1-base))]+[0.0]*k
    for _ in range(TRAIN_STEPS):
        gradient=[0.0]*(k+1)
        for case in cases:
            x=case["features"]
            p=sigmoid(w[0]+sum(a*b for a,b in zip(w[1:],x)))
            residual=p-int(case["won"])
            gradient[0]+=residual
            for j,v in enumerate(x):
                gradient[j+1]+=residual*v
        count=len(cases)
        w[0]-=STEP_SIZE*gradient[0]/count
        for j in range(1,k+1):
            w[j]-=STEP_SIZE*(gradient[j]/count+REGULARIZATION*w[j])
    # A probabilistic payoff model requires independent prior option P&Ls.
    # Expert win/loss means are shrunk toward pooled means to reduce noise.
    pooled_win=[x["net_pnl_usd"] for x in cases if x["won"]]
    pooled_loss=[x["net_pnl_usd"] for x in cases if not x["won"]]
    expert={}
    for name in EXPERTS:
        prior=[x for x in cases if x["expert"]==name]
        profit=[x["net_pnl_usd"] for x in prior if x["won"]]
        loss=[x["net_pnl_usd"] for x in prior if not x["won"]]
        def shrink(values,pooled,fallback):
            old=(statistics.fmean(pooled) if pooled else fallback)
            return (sum(values)+15*old)/(len(values)+15)
        expert[name]={
            "prior_matched":len(prior),
            "avg_profit_win_usd":shrink(profit,pooled_win,25.0),
            "avg_profit_loss_usd":shrink(loss,pooled_loss,-25.0),
        }
    return w,expert


def predicted_score(fitted:tuple[list[float],dict],case:dict)->dict:
    w,payoffs=fitted
    prob=sigmoid(w[0]+sum(a*b for a,b in zip(w[1:],case["features"])))
    expert=payoffs[case["expert"]]
    ev=(prob*expert["avg_profit_win_usd"]
        +(1-prob)*expert["avg_profit_loss_usd"]
        -FRICTION_STRESS_EXTRA_PER_TRADE)
    return {
        "predicted_win":round(prob,6),
        "predicted_stressed_net_usd_per_contract":round(ev,4),
        "prior_expert_matched":expert["prior_matched"],
    }


def prior_profit_gate(prior:list[dict],expert:str)->bool:
    """A robust-looking but preregistered past-only hurdle.

    A loose filter can be optimistic under multiple testing; this is a
    research safety constraint, not a certified confidence guarantee.
    """
    matched=[x for x in prior if x["expert"]==expert]
    if len(matched)<35 or len({x["day"] for x in matched})<12:
        return False
    daily=defaultdict(float)
    for item in matched:
        daily[item["day"]]+=item["net_pnl_usd"]-FRICTION_STRESS_EXTRA_PER_TRADE
    all_day=sorted({x["day"] for x in prior})
    vals=[daily[d] for d in all_day]  # include no-trade days
    if len(vals)<30:
        return False
    lower=statistics.fmean(vals)-2.0*statistics.stdev(vals)/math.sqrt(len(vals))
    return lower>0


def decision(case:dict,score:dict|None,arm:str,prior:list[dict])->bool:
    if arm not in ARMS:
        raise ValueError("unknown adaptive entry arm")
    if arm=="both_experts_unfiltered":
        return True
    if arm=="momentum_expert_only":
        return case["expert"]==EXPERTS[0]
    if arm=="pullback_expert_only":
        return case["expert"]==EXPERTS[1]
    if score is None or score["prior_expert_matched"]<MIN_PRIOR_EXPERT:
        return False
    p=score["predicted_win"]
    ev=score["predicted_stressed_net_usd_per_contract"]
    if arm=="adaptive_predicted_win_45":
        return p>=.45
    if arm=="adaptive_positive_stressed_expected_value":
        return ev>0
    if arm=="adaptive_predicted_win_60_and_ev":
        return p>=.60 and ev>0
    if arm=="adaptive_predicted_win_75_and_ev":
        return p>=.75 and ev>0
    if arm=="adaptive_prior_profit_evidence":
        return ev>0 and prior_profit_gate(prior,case["expert"])
    raise ValueError("not a recognized arm")


def stats(ledger:list[dict],days:list[str],arm:str,pd:str)->dict:
    subset=[x for x in ledger if x["arm"]==arm and
            (pd=="all_inspected" or x["period"]==pd)]
    byday=defaultdict(float)
    for x in subset:
        byday[x["day"]]+=x["net_pnl_usd"]
    relevant=[d for d in days if pd=="all_inspected" or period(d)==pd]
    p=[x["net_pnl_usd"] for x in subset]
    returns=[x["premium_return_pct"] for x in subset]
    equity=high=dd=0.
    for pnl in p:
        equity+=pnl
        high=max(high,equity)
        dd=max(dd,high-equity)
    return {
        "calendar_sessions":len(relevant),"trades":len(p),
        "positive_days":sum(byday[d]>0 for d in relevant),
        "negative_days":sum(byday[d]<0 for d in relevant),
        "flat_or_no_trade_days":sum(byday[d]==0 for d in relevant),
        "hypothetical_pnl_usd_not_account":round(sum(p),2),
        "extra_slippage_stressed_pnl_usd_not_account":round(
            sum(p)-FRICTION_STRESS_EXTRA_PER_TRADE*len(p),2),
        "win_rate_pct":round(100*sum(x>0 for x in p)/len(p),2) if p else None,
        "mean_premium_return_pct":round(statistics.fmean(returns),3) if returns else None,
        "mean_pnl_per_trade_usd":round(statistics.fmean(p),4) if p else None,
        "cumulative_closed_trade_pnl_drawdown_usd_NOT_ACCOUNT":round(dd,2),
    }


def bootstrap_daily(ledger:list[dict],days:list[str],arm:str,reps:int=1000)->dict:
    daily=defaultdict(float)
    counts=defaultdict(int)
    for x in ledger:
        if x["arm"]==arm:
            daily[x["day"]]+=x["net_pnl_usd"]
            counts[x["day"]]+=1
    gross=[daily[d] for d in days]
    stress=[daily[d]-counts[d]*FRICTION_STRESS_EXTRA_PER_TRADE for d in days]
    rnd=random.Random(RANDOM_SEED)
    def ci(arr):
        samples=[]
        if not arr: return None
        for _ in range(reps):
            picked=[]
            while len(picked)<len(arr):
                offset=rnd.randrange(len(arr))
                picked.extend(arr[(offset+j)%len(arr)] for j in range(5))
            samples.append(statistics.fmean(picked[:len(arr)]))
        samples.sort()
        return [round(samples[24],4),round(samples[974],4)]
    return {"five_session_block_CI_mean_pnl_per_calendar_day_usd":ci(gross),
            "extra_friction_stress_same_CI":ci(stress),
            "descriptive_only_not_multiple_test_or_true_holdout":True}


def run(root:Path,out:Path)->dict:
    research=[]
    coverage=defaultdict(int)
    for p in _research_files(root):
        frames=list(load_canonical_path(p))
        if not frames: continue
        day=frames[0].timestamp.astimezone(ET).date().isoformat()
        classified=session_classifications(frames)
        # Base selector already enforces ten-minute non-overlap. Preserve
        # common candidate clock before applying expert variants.
        chosen=select(classified,"momentum_reversal_four_factors")
        at_close={frame.timestamp.replace(microsecond=0):i
                  for i,frame in enumerate(frames)}
        available=[]
        for row in chosen:
            expert=expert_for(row)
            if expert is None:
                continue
            signal_time=datetime.fromisoformat(row.opportunity.signal_at)
            i=at_close.get(signal_time.replace(microsecond=0)
                           -__import__("datetime").timedelta(minutes=1))
            if i is None or i<20:
                coverage["unmatched_asof_signal_bar"]+=1
                continue
            selected=_select_entry(frames,signal_time,row.opportunity.original_direction)
            if selected is None:
                coverage["missing_entry_quote"]+=1
                continue
            at_idx,q=selected
            path=_sampled_path(frames,at_idx,q.symbol)
            if path is None:
                coverage["missing_exit_path"]+=1
                continue
            outcome=simulate(q.ask,path,TRAIL_PCT)
            features=causal_features(row,q,frames,i)
            available.append({
                "day":day,"period":period(day),"expert":expert,
                "signal_at":row.opportunity.signal_at,
                "option_symbol":q.symbol,"features":features,
                "won":outcome["pnl_usd"]>0,
                "net_pnl_usd":outcome["pnl_usd"],
                "premium_return_pct":outcome["return_on_premium_pct"],
                "close_reason":outcome["exit_reason"],
            })
            coverage[expert+"_matched"]+=1
        research.append({"day":day,"period":period(day),"candidates":available})
        coverage["completed_sessions"]+=1
        coverage["completed_minute_frames"]+=len(frames)
    research.sort(key=lambda x:x["day"])
    days=[x["day"] for x in research]
    ledger=[]
    predictions=[]
    eligible_sessions=0
    for i,session in enumerate(research):
        prior=[x for day in research[max(0,i-PRIOR_SESSIONS):i]
               for x in day["candidates"]]
        fit=(train_prior_only(prior) if i>=PRIOR_SESSIONS else None)
        if fit:eligible_sessions+=1
        previously=[x for day in research[max(0,i-PRIOR_SESSIONS):i]
                    for x in day["candidates"]]
        for arm in ARMS:
            for case in sorted(session["candidates"],key=lambda x:x["signal_at"]):
                score=predicted_score(fit,case) if fit else None
                if score and arm=="both_experts_unfiltered":
                    predictions.append({"day":session["day"],"period":session["period"],
                        "expert":case["expert"],"won":case["won"],
                        **score})
                if arm not in ARMS[:3] and fit is None:
                    continue  # scores cannot be computed without prior evidence
                if not decision(case,score,arm,previously):
                    continue
                ledger.append({
                    "day":session["day"],"period":session["period"],
                    "expert":case["expert"],"arm":arm,
                    "signal_at":case["signal_at"],
                    "option_symbol":case["option_symbol"],
                    "net_pnl_usd":case["net_pnl_usd"],
                    "premium_return_pct":case["premium_return_pct"],
                    "predicted_win":score["predicted_win"] if score else None,
                    "predicted_stressed_ev_usd":score["predicted_stressed_net_usd_per_contract"]
                        if score else None,
                })
    eval_days=days[PRIOR_SESSIONS:]
    # Compare dynamic models only over same chronological after-warmup days.
    eval_ledger=[r for r in ledger if r["day"] in set(eval_days)]
    sections={arm:{
        "all_128_days_reference":stats(ledger,days,arm,"all_inspected"),
        "post_warmup_68_days":stats(eval_ledger,eval_days,arm,"all_inspected"),
        "post_warmup_bootstrap":bootstrap_daily(eval_ledger,eval_days,arm),
        "period_breakdown":{p:stats(eval_ledger,eval_days,arm,p) for p in PERIODS[:-1]},
    } for arm in ARMS}
    labeled=[x for x in predictions]
    brier=(statistics.fmean(
        (x["predicted_win"]-int(x["won"]))**2 for x in labeled)
           if labeled else None)
    calibration={}
    for lo,hi in ((0,.25),(.25,.4),(.4,.5),(.5,.6),(.6,.75),(.75,1.00001)):
        cases=[x for x in labeled if lo<=x["predicted_win"]<hi]
        calibration[f"[{lo},{hi})"]={
            "signals":len(cases),
            "mean_predicted_win_pct":round(100*statistics.fmean(
                x["predicted_win"] for x in cases),2) if cases else None,
            "observed_win_pct":round(100*statistics.fmean(
                int(x["won"]) for x in cases),2) if cases else None,
        }
    result={
        "study":"FROZEN_30PCT_TRAILING_CAUSAL_ADAPTIVE_TWO_EXPERT_LOGISTIC_WALKFORWARD",
        "research_only":True,"data_purchases":0,"trading_orders":0,
        "no_profit_targets_or_scaleouts":True,
        "independent_hard_loss_pct":35,"maximum_hold_minutes":10,
        "quote_data_event_executable_NBBO":False,"actual_full_chain_PCR":False,
        "actual_fills_and_account_drawdown":False,"fresh_untouched_holdout":False,
        "expert_names":EXPERTS,"algorithms":ARMS,
        "train_only_prior_days":PRIOR_SESSIONS,
        "minimum_prior_matched_training_trades":MIN_TRAINING_MATCHED,
        "training_sessions_with_model":eligible_sessions,
        "candidate_coverage":dict(coverage),
        "post_warmup_test_sessions":len(eval_days),
        "results":sections,
        "calibration":{"test_predictions":len(labeled),
                       "brier_loss_lower_is_better":round(brier,6) if brier else None,
                       "bins":calibration},
        "selection_bias_warning":
            "Research-only already-inspected archive and multiple candidate policies. "
            "Descriptive chronological splits are NOT untouched future validation.",
    }
    out.mkdir(parents=True,exist_ok=True)
    (out/"report.json").write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
    with (out/"paired_predictions.csv").open("w",newline="") as f:
        columns=["day","period","expert","won","predicted_win",
                 "predicted_stressed_net_usd_per_contract","prior_expert_matched"]
        w=csv.DictWriter(f,fieldnames=columns)
        w.writeheader();w.writerows(predictions)
    with (out/"simulated_trades.csv").open("w",newline="") as f:
        columns=["day","period","expert","arm","signal_at","option_symbol",
                 "net_pnl_usd","premium_return_pct","predicted_win","predicted_stressed_ev_usd"]
        w=csv.DictWriter(f,fieldnames=columns)
        w.writeheader();w.writerows(ledger)
    print("ADAPTIVE_ENTRY_RESULT "+json.dumps(result,sort_keys=True),flush=True)
    return result


def main():
    a=argparse.ArgumentParser()
    a.add_argument("--data-dir",type=Path,default=Path("/data/research"))
    a.add_argument("--output-dir",type=Path,default=Path("/data/research/adaptive_entry_walkforward"))
    options=a.parse_args()
    run(options.data_dir,options.output_dir)


if __name__=="__main__":
    main()
