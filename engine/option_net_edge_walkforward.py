"""SPY 0DTE OPTION NET EDGE — prior-only walk-forward on opening and legacy setups.

Research-only, unvalidated minute-bucket option-quote outcomes. Every
candidate's forecast uses only quotes at its modeled entry observation and
examples from PRIOR FINISHED MARKET SESSIONS. Later exits are labels, NEVER
inputs to admission. No paid data, no account orders, no live changes.

Five entry experts: 5m and 15m opening-CLOSE breakouts, 5m and 15m
confirmed CLOSE-range retests, late four-factor momentum. Not true OHLCV ORB!
Same exit for all arms: 30% trailing whole position, independent 35%
initial stop, 10-minute maximum hold, no take profit / scale out / runner.
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
from .morning_0930_1100_study import legacy_events
from .or_retest_frequency import range_events, candidate_quality
from .trailing_only_sweep import simulate, period
from .weekly_options_data import audit as event_data_audit

TRAIL_PCT=30
STOP_PCT=35
MAX_HOLD=10
PRIOR_SESSIONS=45
MIN_PRIOR_EXPERT=20
MAX_NEIGHBORS=30
EXTRA_FRICTION_USD=2.0
# Pre-specified shrinkage to reduce the risk of spurious local winners.
PRIOR_SHRINK=20
MIN_DIRECTIONAL_MOVE_COST_RATIO=1.5
POLICIES=(
    "all_setups_one_at_a_time",
    "option_friction_only",
    "prior_analog_positive_net",
    "prior_analog_net_plus_friction",
    "prior_analog_positive_lower_bound",
)
EXPERTS=("direct_5m","direct_15m","retest_5m","retest_15m","legacy_4f")
GROUPS=("development","validation","previously_studied_diagnostic","all_inspected")


def safe(value:float,cap:float=4.0)->float:
    return max(-cap,min(cap,value))


def normalize_candidate_features(frames,signal_index:int,quote,side:str)->tuple[float,...]:
    """NO future SPY closes. All quote inputs from first modeled entry."""
    market=frames[signal_index].market
    current=market.spot
    prev5=frames[max(0,signal_index-5)].market.spot
    prev15=frames[max(0,signal_index-15)].market.spot
    sign=1 if side=="call" else -1
    ret5=sign*_bps(current,prev5)
    ret15=sign*_bps(current,prev15)
    chunks=[_bps(frames[k].market.spot,frames[k-1].market.spot)
            for k in range(max(1,signal_index-14),signal_index+1)]
    path_eff=ret15/sum(abs(z) for z in chunks) if chunks and sum(abs(z) for z in chunks)>0 else 0
    gross_roundtrip_cost_share=max(0.,quote.ask-quote.bid)+.02+1.30/100
    incremental_friction_share=EXTRA_FRICTION_USD/100
    cost_share=gross_roundtrip_cost_share+incremental_friction_share
    # Pure first-order delta break-even estimate: no IV, gamma or
    # time-decay model included. Used as a PROXY feature, not a forecast.
    delta_per_spy_dollar=abs(quote.delta)
    break_even_bps=(cost_share/max(.05,delta_per_spy_dollar*current))*10000.
    five_move_bps=abs(_bps(current,prev5))
    ratio_iv_rv=(quote.implied_volatility / market.realized_volatility
                 if market.realized_volatility>0 else math.inf)
    spot_iv_minus_rv=market.implied_volatility-market.realized_volatility
    clock=(frames[signal_index].timestamp+timedelta(minutes=1)).astimezone(ET)
    # Fixed scales determined before seeing any result.
    return (
        safe(ret5/12.),safe(ret15/30.),safe(path_eff*2),
        safe((market.volume_ratio-1)/1.5),
        safe(quote.spread_fraction/.1),
        safe((quote.ask+.01)/2.0),
        safe(break_even_bps/10.),
        safe(five_move_bps/max(.25,break_even_bps)),
        safe((ratio_iv_rv-1.)/1.0) if math.isfinite(ratio_iv_rv) else 4.,
        safe(spot_iv_minus_rv/.3),
        safe((clock.hour*60+clock.minute-690)/180.),
        safe((abs(quote.delta)-.5)/.15),
    )


# Prior-only grouping: each expert draws analog observations of the
# same deterministic setup, NOT post-hoc labels from today's signals.
WEIGHTS=(1.0,.8,.7,.5,1.5,.7,1.3,.6,.6,.5,.35,.4)


def analog_distance(a:tuple[float,...],b:tuple[float,...])->float:
    if len(a)!=len(WEIGHTS) or len(b)!=len(WEIGHTS):
        raise ValueError("invalid entry feature dimensions")
    return sum(w*(u-v)*(u-v) for w,u,v in zip(WEIGHTS,a,b))


def predict_net(prior:list[dict],expert:str,features:tuple[float,...])->dict|None:
    cohort=[c for c in prior if c["expert"]==expert and c["pnl_usd"] is not None]
    if len(cohort)<MIN_PRIOR_EXPERT:
        return None
    neighbors=sorted(cohort,key=lambda c:(
        analog_distance(c["features"],features),c["day"],c["signal_at"]))[:MAX_NEIGHBORS]
    distances=[analog_distance(c["features"],features) for c in neighbors]
    weights=[1/(1+d) for d in distances]
    sw=sum(weights)
    local=sum(w*c["pnl_usd"] for w,c in zip(weights,neighbors))/sw
    overall=statistics.fmean(c["pnl_usd"] for c in cohort)
    # Prior pseudo-observations make small local samples conservative.
    ev_before_extra=(local*sw+overall*PRIOR_SHRINK)/(sw+PRIOR_SHRINK)
    expected_extra_stressed=ev_before_extra-EXTRA_FRICTION_USD
    wins=sum(w*(c["pnl_usd"]>0) for w,c in zip(weights,neighbors))/sw
    variance=sum(w*(c["pnl_usd"]-local)**2 for w,c in zip(weights,neighbors))/sw
    effective_n=sw*sw/sum(w*w for w in weights)
    # This is a heuristic uncertainty penalty only, NOT formal confidence.
    lower=expected_extra_stressed-1.645*math.sqrt(variance/max(1,effective_n))
    return {
        "prior_expert_trades":len(cohort),
        "matched_analogs":len(neighbors),
        "prior_expert_mean_net_usd":round(overall,4),
        "local_simulated_mean_net_usd":round(local,4),
        "predicted_stressed_net_usd":round(expected_extra_stressed,4),
        "uncertainty_penalized_stressed_net_usd":round(lower,4),
        "analog_historical_win_rate_pct":round(100*wins,2),
        "not_calibrated_probability":True,
    }


def cost_check(features:tuple[float,...],quote)->bool:
    # The 8th feature is an as-of signed 5-minute spot magnitude / estimated
    # delta-only break-even movement. Don't call this an option forecast.
    return (quote.spread_fraction<=.08
            and features[7]>=MIN_DIRECTIONAL_MOVE_COST_RATIO)


def decide(expert:str,features:tuple[float,...],quote,
           forecast:dict|None,arm:str)->bool:
    if arm not in POLICIES:raise ValueError("invalid research policy")
    if arm=="all_setups_one_at_a_time":
        return True
    if arm=="option_friction_only":
        return cost_check(features,quote)
    if forecast is None:
        return False
    profit=forecast["predicted_stressed_net_usd"]>0
    if arm=="prior_analog_positive_net":
        return profit
    if arm=="prior_analog_net_plus_friction":
        return profit and cost_check(features,quote)
    if arm=="prior_analog_positive_lower_bound":
        return (profit and
            forecast["uncertainty_penalized_stressed_net_usd"]>0)
    raise AssertionError("unknown policy")


def candidates_for_day(frames)->list[dict]:
    """Return signal/quote/feature tuples before ANY future exit is inspected."""
    raw=[]
    for n in (5,15):
        for event in range_events(frames,n):
            label=("retest" if event["type"]=="retest" else "direct")+f"_{n}m"
            if label not in EXPERTS:raise AssertionError("unknown expert")
            raw.append((label,event))
    for event in legacy_events(frames,time(10,46),time(15,25)):
        raw.append(("legacy_4f",{
            "signal_at":event["signal_at"],"right":event["right"],
            "type":"four_factor","range_minutes":0,
            "volume_ratio":event["volume_ratio"],
        }))
    index={frame.timestamp+timedelta(minutes=1):i
           for i,frame in enumerate(frames)}
    ordered=[]
    for expert,event in raw:
        when=datetime.fromisoformat(event["signal_at"])
        if when.astimezone(ET).time()>=time(15,25):
            continue
        signal_idx=index.get(when)
        if signal_idx is None or signal_idx<15:
            continue
        selected=_select_entry(frames,when,event["right"])
        if selected is None:
            continue
        option_idx,quote=selected
        observed=frames[option_idx].timestamp+timedelta(minutes=1)
        features=normalize_candidate_features(frames,signal_idx,quote,event["right"])
        cost=candidate_quality(frames,signal_idx,quote,event["right"])
        ordered.append({
            "expert":expert,"event":event,"features":features,
            "quote":quote,"entry_at":observed,
            "option_quote_index":option_idx,
            "symbol":quote.symbol,"entry_debit_usd":cost["estimated_entry_debit_per_contract_usd"],
        })
    return sorted(ordered,key=lambda c:(
        c["entry_at"],EXPERTS.index(c["expert"]),c["symbol"]))


def day_evaluate(frames,datestring:str,candidates:list[dict],
                 prior:list[dict])->tuple[list[dict],list[dict],dict]:
    """Predict without outcomes; evaluate exit only for admitted trades.

    Missing future full bid paths will stop a slot for the full 10 minutes
    in the research simulator rather than presciently admitting another
    signal at the same entry time. Past-day training, however, includes
    only labeled complete option bid paths.
    """
    ready=len(prior)>0
    forecasts={}
    all_examples=[]
    diagnostic=defaultdict(int)
    for idx,c in enumerate(candidates):
        pred=predict_net(prior,c["expert"],c["features"]) if ready else None
        forecasts[idx]=pred
        # Each completed candidate outcome becomes available to training
        # only on LATER CALENDAR DAYS. Not used for today's trade decisions.
        future=_sampled_path(frames,c["option_quote_index"],c["symbol"])
        outcome=simulate(c["quote"].ask,future,TRAIL_PCT) if future else None
        all_examples.append({
            "day":datestring,
            "signal_at":c["event"]["signal_at"],
            "expert":c["expert"],
            "features":c["features"],
            "pnl_usd":outcome["pnl_usd"] if outcome else None,
        })
        diagnostic[c["expert"]+"_candidate"]+=1
        diagnostic["future_label_complete" if outcome else "future_label_missing"]+=1
    output=[]
    for arm in POLICIES:
        flat_at=None
        for idx,c in enumerate(candidates):
            if flat_at is not None and c["entry_at"]<=flat_at:
                diagnostic[arm+"_busy"]+=1
                continue
            pred=forecasts[idx]
            if not decide(c["expert"],c["features"],c["quote"],pred,arm):
                continue
            # Signal selection NEVER chooses using exit outcome.
            diagnostic[arm+"_accepted"]+=1
            known=all_examples[idx]["pnl_usd"]
            future=_sampled_path(frames,c["option_quote_index"],c["symbol"])
            if future is None:
                flat_at=c["entry_at"]+timedelta(minutes=MAX_HOLD)
                diagnostic[arm+"_missing_future_after_admit"]+=1
                continue
            result=simulate(c["quote"].ask,future,TRAIL_PCT)
            flat_at=datetime.fromisoformat(result["exit_time"])
            output.append({
                "day":datestring,"arm":arm,"expert":c["expert"],
                "signal_at":c["event"]["signal_at"],
                "entry_at":c["entry_at"].isoformat(),
                "exit_at":result["exit_time"],"symbol":c["symbol"],
                "side":c["event"]["right"],
                "pnl_usd":result["pnl_usd"],
                "premium_return_pct":result["return_on_premium_pct"],
                "entry_debit_usd":c["entry_debit_usd"],
                "predicted_stressed_ev":pred["predicted_stressed_net_usd"] if pred else None,
                "uncertainty_penalized_ev":pred["uncertainty_penalized_stressed_net_usd"]
                     if pred else None,
                "sample_size":pred["prior_expert_trades"] if pred else 0,
            })
            if known!=result["pnl_usd"]:
                raise AssertionError("model payoff mismatch")
    return all_examples,output,diagnostic


def outcome_summary(rows:list[dict],days:list[str],arm:str,group:str)->dict:
    relevant=[d for d in days if group=="all_inspected" or period(d)==group]
    chosen=[r for r in rows if r["arm"]==arm and (
        group=="all_inspected" or period(r["day"])==group)]
    pv=[r["pnl_usd"] for r in chosen]
    perday=defaultdict(float)
    freq=defaultdict(int)
    for r in chosen:
        perday[r["day"]]+=r["pnl_usd"]
        freq[r["day"]]+=1
    total=peak=drawdown=0.
    for x in pv:
        total+=x
        peak=max(peak,total)
        drawdown=max(drawdown,peak-total)
    return {
        "sessions":len(relevant),"trades":len(pv),
        "trades_per_session":round(len(pv)/len(relevant),3) if relevant else None,
        "active_days":sum(freq[d]>0 for d in relevant),
        "days_2plus":sum(freq[d]>=2 for d in relevant),
        "days_3plus":sum(freq[d]>=3 for d in relevant),
        "max_trades_per_day":max((freq[d] for d in relevant),default=0),
        "wins":sum(x>0 for x in pv),
        "observed_win_pct":round(100*sum(x>0 for x in pv)/len(pv),2) if pv else None,
        "net_option_pnl_usd_1_contract_NOT_ACCOUNT":round(sum(pv),2),
        "extra_2usd_slippage_stress_usd":round(sum(pv)-EXTRA_FRICTION_USD*len(pv),2),
        "closed_trade_additive_drawdown_usd_not_account":round(drawdown,2),
        "positive_days":sum(perday[d]>0 for d in relevant),
        "negative_days":sum(perday[d]<0 for d in relevant),
        "no_trade_days":sum(freq[d]==0 for d in relevant),
    }


def block_interval(rows:list[dict],days:list[str],arm:str,reps:int=1200)->dict:
    byday=defaultdict(float)
    count=defaultdict(int)
    for r in rows:
        if r["arm"]==arm:
            byday[r["day"]]+=r["pnl_usd"]
            count[r["day"]]+=1
    data=[byday[d]-EXTRA_FRICTION_USD*count[d] for d in days]
    if not data:return {"95pct_interval":None}
    rng=random.Random(20261012)
    means=[]
    for _ in range(reps):
        sample=[]
        while len(sample)<len(data):
            at=rng.randrange(len(data))
            sample.extend(data[(at+j)%len(data)] for j in range(5))
        means.append(statistics.fmean(sample[:len(data)]))
    means.sort()
    best=sorted((byday[d] for d in days),reverse=True)
    return {
        "extra_friction_5day_bootstrap_mean_usd_per_session":
            [round(means[int(.025*reps)],3),round(means[int(.975*reps)-1],3)],
        "pnl_excluding_best_day":round(sum(byday.values())-best[0],2),
        "pnl_excluding_best_three_days":round(sum(byday.values())-sum(best[:3]),2),
        "not_selection_bias_adjusted":True,
    }


def run(root:Path,dest:Path)->dict:
    days=[]
    ledgers=[]
    history=[]
    diag=defaultdict(int)
    for file in _research_files(root):
        frames=list(load_canonical_path(file))
        if not frames:continue
        day=frames[0].timestamp.astimezone(ET).date().isoformat()
        days.append(day)
        prior=[x for prior_day in history[-PRIOR_SESSIONS:]
               for x in prior_day["examples"]]
        today=candidates_for_day(frames)
        today_examples,policy_trades,stats=day_evaluate(frames,day,today,prior)
        ledgers.extend(policy_trades)
        for key,value in stats.items():diag[key]+=value
        history.append({"day":day,"examples":today_examples})
        diag["one_minute_frames"]+=len(frames)
    days=sorted(set(days))
    postwarm=days[PRIOR_SESSIONS:]
    postwarm_set=set(postwarm)
    test=[x for x in ledgers if x["day"] in postwarm_set]
    report={
        "study":"prior_only_option_after_friction_break_even_vs_spy_direction",
        "existing_history_days":len(days),"training_only_prior_sessions":PRIOR_SESSIONS,
        "postwarmup_days":len(postwarm),
        "experiment_status":"RESEARCH_ONLY_NOT_UNSEEN_HOLDOUT",
        "model":"distance-weighted-neighbor prediction of modeled option NET PnL per strategy expert, regularized to prior cohort mean",
        "candidate_experts":list(EXPERTS),
        "predeclared_policies":list(POLICIES),
        "option_break_even_estimate":"(bid_ask_spread + roundtrip_fee/slippage + $2 extra stress)/abs(delta)/SPY_spot",
        "exit":{"trail_pct":TRAIL_PCT,"hard_initial_pct":STOP_PCT,
                "maximum_hold_min":MAX_HOLD,
                "fixed_take_profit":False,"partial_exits":False},
        "option_quotes_true_event_time_nbbo":False,
        "spy_opening_range_true_high_low":False,
        "spy_full_chain_intraday_put_call_ratio_available":False,
        "real_broker_fill_performance":False,"funded_weekly_returns":False,
        "data_purchased":False,"orders_submitted":False,
        "actual_event_data_inventory":event_data_audit(root),
        "coverage":dict(diag),
        "results":{arm:{
            "postwarmup_83_sessions":outcome_summary(test,postwarm,arm,"all_inspected"),
            "all_128_sessions_descriptive":outcome_summary(ledgers,days,arm,"all_inspected"),
            "period_breakdown":{name:outcome_summary(test,postwarm,arm,name)
                for name in GROUPS[:-1]},
            "uncertainty":block_interval(test,postwarm,arm),
        } for arm in POLICIES},
        "limitations":[
            "Already-inspected 128 dates: time-forward model fitting is not untouched holdout.",
            "Candidate option labels and execution use minute-bucket CBBO; actual NBBO fills unavailable.",
            "Preselected option uses modeled first entry quote; missing exit quote data undercounts outcomes.",
            "12 hand-scaled features and model policy hypotheses are exploratory, not calibrated probabilities.",
            "This is NOT a real account or an autonomous profitable trading strategy.",
        ],
    }
    dest.mkdir(parents=True,exist_ok=True)
    (dest/"report.json").write_text(json.dumps(report,sort_keys=True,indent=2)+"\n")
    columns=("day","arm","expert","signal_at","entry_at","exit_at",
             "symbol","side","pnl_usd","premium_return_pct","entry_debit_usd",
             "predicted_stressed_ev","uncertainty_penalized_ev","sample_size")
    with (dest/"trades.csv").open("w",newline="") as f:
        writer=csv.DictWriter(f,fieldnames=columns)
        writer.writeheader();writer.writerows(ledgers)
    print("OPTION_NET_EDGE_RESULT "+json.dumps(report,sort_keys=True),flush=True)
    return report


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--data-dir",type=Path,default=Path("/data/research"))
    p.add_argument("--output-dir",type=Path,default=Path("/data/research/option_net_edge"))
    a=p.parse_args()
    run(a.data_dir,a.output_dir)


if __name__=="__main__":
    main()
