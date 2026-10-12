"""EDGEFORGE — research-only, bidirectional SPY 0DTE entry selector.

The original four-factor momentum/reversal candidate is just an EVENT.
For each event, examine separately whether a CALL, a PUT, or NO TRADE
has the best estimated after-friction economics. A trailing 30% stop,
independent 35% initial stop, 10-minute hard hold and FULL exit remain
fixed across every arm. NEVER change a live/paper order or buy any data.

Causal boundary:
  - Features: <= completed signal bar, plus the sampled option entry quote.
  - Labels: historical trailing-only OPTION bid exits, not SPY direction.
  - Forecast training: PRIOR COMPLETED TRADING SESSIONS ONLY.
  - Same-day future trades are never training samples.
The historical CBBO-1m quotes are minute buckets, NOT true event-timestamped
NBBO. Incomplete sampled SPY 0DTE option quotes cannot produce a true
full-chain put/call volume ratio. No real fill validation.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from .burst_research import _research_files
from .data import load_canonical_path
from .exit_policy_comparison import _select_entry, _sampled_path
from .free_signal_screen import ET, _bps
from .spy_pcr_momentum_reversal import session_classifications, select
from .trailing_only_sweep import simulate, period

ARCHIVE_SESSIONS = 128
TRAIL_PERCENT = 30
INITIAL_HARD_STOP = 35
MAX_HOLD_MINUTES = 10
PRIOR_MARKET_SESSIONS = 45
NEIGHBOR_COUNT = 40
MIN_PRIOR_SIDE_SAMPLES = 30
EXTRA_FRICTION_USD = 2.0
SIMULATED_MAX_ONE_CONTRACT_BUDGET_USD = 115.0  # study, NOT user account funds
SEED = 20261011

# Predeclared feature emphasis, no selection from archive outcomes.
FEATURE_WEIGHTS = (2.5, 1.1, 1.1, .8, .8, .8, .6, .6, .6, .5, 1.0, .8, .7, .5)
ARMS = (
    "original_signal_direction",
    "contrarian_direction",
    "edgeforge_best_expected_net",
    "edgeforge_positive_expert_consensus",
    "edgeforge_precise_60",
    "edgeforge_precise_75",
    "edgeforge_expert_consensus_115_cap",
)
PERIODS = ("development", "validation", "previously_studied_diagnostic", "all_inspected")


def bounded(value:float, low:float=-3.0, high:float=3.0)->float:
    return min(high,max(low,value))


def observed_signal_index(frames,signal_at:str)->int|None:
    """Candidate timing reflects close of the previous archived minute."""
    when=datetime.fromisoformat(signal_at)-timedelta(minutes=1)
    for i,frame in enumerate(frames):
        if frame.timestamp==when:
            return i
    return None


def candidate_features(row,option,frames,idx:int,right:str)->tuple[float,...]:
    """As-of bar + as-of simulated entry quote. Never future option path."""
    if idx<30:
        raise ValueError("inadequate completed bar context")
    if right not in ("call","put"):
        raise ValueError("invalid option right")
    historical=frames[idx-30:idx+1]
    closes=[f.market.spot for f in historical]
    direction=1 if right=="call" else -1
    orig=row.opportunity.original_direction
    counter=1.0 if right!=orig else -1.0
    ret5=direction*_bps(closes[-1],closes[-6])
    ret15=direction*_bps(closes[-1],closes[-16])
    ret30=direction*_bps(closes[-1],closes[-31])
    churn=sum(abs(_bps(closes[j],closes[j-1]))
              for j in range(1,len(closes)))
    efficiency=ret30/churn if churn>0 else 0.0
    clock=datetime.fromisoformat(row.opportunity.signal_at).astimezone(ET)
    spot=frames[idx].market
    ratio=spot.implied_volatility-spot.realized_volatility
    # Any opposite-side signal uses its true opposite-side directional
    # history; the archived original signal's MACD vote is never flipped
    # into an invented opposite MACD observation.
    macd_vote=(1.0 if row.macd_histo_aligned_or_improving else -1.0)
    return (
        counter,
        bounded(ret5/10.0),
        bounded(ret15/20.0),
        bounded(ret30/30.0),
        bounded(efficiency*2.0),
        bounded((row.opportunity.volume_ratio_at_signal-1.0)),
        1.0 if row.opportunity.setup=="trend_pullback_reclaim" else -1.0,
        macd_vote*(1 if right==orig else -1),
        bounded((row.factor_score-5)/2.0),
        bounded(((clock.hour*60+clock.minute)-720)/150.0),
        bounded(option.spread_fraction/.12),
        bounded((abs(option.delta)-.5)/.15),
        bounded(option.ask/2.0),
        bounded(ratio/.25),
    )


def distance(a:tuple[float,...],b:tuple[float,...])->float:
    if len(a)!=len(FEATURE_WEIGHTS) or len(b)!=len(FEATURE_WEIGHTS):
        raise ValueError("incorrect feature vector shape")
    return sum(w*(x-y)**2 for w,x,y in zip(FEATURE_WEIGHTS,a,b))


def analog_forecast(prior:list[dict],features:tuple[float,...])->dict|None:
    """Non-parametric conditional option P&L forecast with shrinkage.

    Prior is all historical BUY options known by older completed dates.
    Neighbor selection never uses an outcome from the current market day.
    Win percentage is descriptive historical likelihood, NOT calibrated
    probability. Conservative lower bound discourages artificial precision.
    """
    if len(prior)<MIN_PRIOR_SIDE_SAMPLES:
        return None
    # The countertrend bit is a genuine hypothesis group; restrict analogs
    # to matching follow/counter choices to avoid spurious cross pooling.
    cohort=[x for x in prior if x["features"][0]==features[0]]
    if len(cohort)<MIN_PRIOR_SIDE_SAMPLES:
        return None
    neighbors=sorted(cohort,key=lambda x:(
        distance(x["features"],features),x["day"],x["signal_at"]))[:NEIGHBOR_COUNT]
    wg=[1.0/(1.0+distance(x["features"],features)) for x in neighbors]
    sw=sum(wg)
    local_net=sum(w*x["pnl_usd"] for w,x in zip(wg,neighbors))/sw
    local_win=sum(w*(x["pnl_usd"]>0) for w,x in zip(wg,neighbors))/sw
    pooled_net=statistics.fmean(x["pnl_usd"] for x in cohort)
    pooled_win=statistics.fmean(x["pnl_usd"]>0 for x in cohort)
    # Predeclared 65/35 shrink toward the same directional expert.
    ev=.65*local_net+.35*pooled_net-EXTRA_FRICTION_USD
    predicted_win=.65*local_win+.35*pooled_win
    variance=sum(w*(x["pnl_usd"]-local_net)**2
                 for w,x in zip(wg,neighbors))/sw
    eff_n=sw**2/sum(w*w for w in wg)
    lower=ev-1.645*math.sqrt(variance/max(1.,eff_n))
    return {
        "prior_same_side_contracts":len(cohort),
        "analog_contracts":len(neighbors),
        "predicted_win_pct":round(predicted_win*100,3),
        "predicted_stressed_net_usd":round(ev,4),
        "local_neighbor_unstressed_pnl_usd":round(local_net,4),
        "cohort_unstressed_pnl_usd":round(pooled_net,4),
        "estimated_lower_bound_stressed_net_usd":round(lower,4),
        "estimated_effective_neighbors":round(eff_n,2),
    }


def choose(event:dict, forecasts:dict[str,dict|None], arm:str)->dict|None:
    """Return an actual simulated candidate OR abstain; never peek at P&L."""
    if arm not in ARMS:
        raise ValueError("unknown entry policy")
    avail=event["sides"]
    original=event["original_right"]
    opposite="put" if original=="call" else "call"
    if arm=="original_signal_direction":
        return avail.get(original)
    if arm=="contrarian_direction":
        return avail.get(opposite)
    eligible=[]
    for right,candidate in avail.items():
        forecast=forecasts[right]
        if forecast is None:
            continue
        ev=forecast["predicted_stressed_net_usd"]
        prob=forecast["predicted_win_pct"]
        local=forecast["local_neighbor_unstressed_pnl_usd"]-EXTRA_FRICTION_USD
        pooled=forecast["cohort_unstressed_pnl_usd"]-EXTRA_FRICTION_USD
        if arm=="edgeforge_best_expected_net":
            admitted=ev>0
        elif arm=="edgeforge_positive_expert_consensus":
            admitted=ev>0 and local>0 and pooled>0
        elif arm=="edgeforge_precise_60":
            admitted=(ev>0 and local>0 and pooled>0
                      and prob>=60 and
                      forecast["estimated_lower_bound_stressed_net_usd"]>0)
        elif arm=="edgeforge_precise_75":
            admitted=(ev>0 and local>0 and pooled>0
                      and prob>=75 and
                      forecast["estimated_lower_bound_stressed_net_usd"]>0)
        elif arm=="edgeforge_expert_consensus_115_cap":
            admitted=(ev>0 and local>0 and pooled>0
                      and candidate["entry_debit_one_contract"]<=SIMULATED_MAX_ONE_CONTRACT_BUDGET_USD)
        else:
            raise AssertionError("invalid arm mapping")
        if admitted:
            eligible.append((ev,right,candidate))
    if not eligible:
        return None
    # Ties broken deterministically, never via future option P&L.
    return sorted(eligible,key=lambda x:(-x[0],x[1]))[0][2]


def summarize(ledger:list[dict], days:list[str], arm:str, reg:str)->dict:
    subset=[x for x in ledger if x["arm"]==arm and
            (reg=="all_inspected" or period(x["day"])==reg)]
    calendar=[d for d in days if reg=="all_inspected" or period(d)==reg]
    pnls=[x["pnl_usd"] for x in subset]
    premium_returns=[x["option_return_pct"] for x in subset]
    daily=defaultdict(float)
    for x in subset:
        daily[x["day"]]+=x["pnl_usd"]
    high=equity=dd=0.
    for p in pnls:
        equity+=p
        high=max(high,equity)
        dd=max(dd,high-equity)
    return {
        "sessions":len(calendar),"trades":len(subset),
        "correct_original_direction_trades":sum(x["side"]==x["original_right"] for x in subset),
        "opposite_direction_trades":sum(x["side"]!=x["original_right"] for x in subset),
        "win_rate_pct":round(100*sum(x>0 for x in pnls)/len(pnls),2) if pnls else None,
        "mean_option_return_pct":round(statistics.fmean(premium_returns),3) if pnls else None,
        "hypothetical_one_contract_pnl_usd_NOT_ACCOUNT":round(sum(pnls),2),
        "additional_2usd_per_contract_stressed_pnl":round(
            sum(pnls)-len(pnls)*EXTRA_FRICTION_USD,2),
        "positive_days":sum(daily[d]>0 for d in calendar),
        "negative_days":sum(daily[d]<0 for d in calendar),
        "flat_or_no_trade_days":sum(daily[d]==0 for d in calendar),
        "additive_closed_trade_pnl_drawdown_not_account_usd":round(dd,2),
    }


def daily_block_bootstrap(ledger:list[dict], days:list[str],arm:str,reps:int=1000)->dict:
    byday=defaultdict(float)
    count=defaultdict(int)
    for row in ledger:
        if row["arm"]==arm:
            byday[row["day"]]+=row["pnl_usd"]
            count[row["day"]]+=1
    pnl=[byday[d] for d in days]
    stressed=[byday[d]-count[d]*EXTRA_FRICTION_USD for d in days]
    rng=random.Random(SEED)
    def ci(source):
        if not source: return None
        samples=[]
        for _ in range(reps):
            draws=[]
            while len(draws)<len(source):
                start=rng.randrange(len(source))
                draws.extend(source[(start+j)%len(source)] for j in range(5))
            samples.append(statistics.fmean(draws[:len(source)]))
        samples.sort()
        return [round(samples[24],4),round(samples[974],4)]
    return {"five_session_block_bootstrap_average_pnl_per_calendar_day_usd":ci(pnl),
            "additional_2usd_friction_same_interval":ci(stressed),
            "model_selection_adjusted":False,
            "untouched_holdout":False}


def run(root:Path,output:Path)->dict:
    coverage=defaultdict(int)
    daily=[]
    for path in _research_files(root):
        frames=list(load_canonical_path(path))
        if not frames:continue
        day=frames[0].timestamp.astimezone(ET).date().isoformat()
        classified=session_classifications(frames)
        selected=select(classified,"momentum_reversal_four_factors")
        events=[]
        for signal in selected:
            sig=signal.opportunity
            idx=observed_signal_index(frames,sig.signal_at)
            if idx is None:
                coverage["no_exact_completed_signal_close"]+=1
                continue
            sides={}
            for side in ("call","put"):
                entry=_select_entry(frames,datetime.fromisoformat(sig.signal_at),side)
                if entry is None:
                    coverage[f"missing_entry_quote_{side}"]+=1
                    continue
                at_idx,q=entry
                trace=_sampled_path(frames,at_idx,q.symbol)
                if trace is None:
                    coverage[f"missing_complete_exit_path_{side}"]+=1
                    continue
                sim=simulate(q.ask,trace,TRAIL_PERCENT)
                x=candidate_features(signal,q,frames,idx,side)
                sides[side]={
                    "day":day,"signal_at":sig.signal_at,
                    "original_right":sig.original_direction,
                    "side":side,"symbol":q.symbol,
                    "features":x,"pnl_usd":sim["pnl_usd"],
                    "won":sim["pnl_usd"]>0,
                    "option_return_pct":sim["return_on_premium_pct"],
                    "entry_debit_one_contract":round((q.ask+.01)*100+.65,2),
                    "spread_fraction":q.spread_fraction,
                    "relative_volume":sig.volume_ratio_at_signal,
                    "setup":sig.setup,
                }
                coverage[f"matched_{side}"]+=1
            if sides:
                events.append({"day":day,"signal_at":sig.signal_at,
                               "original_right":sig.original_direction,
                               "sides":sides})
            if len(sides)==2:
                coverage["matched_both_direction_events"]+=1
        daily.append({"day":day,"events":events})
        coverage["sessions"]+=1
        coverage["minute_frames"]+=len(frames)
        coverage["original_signals"]+=len(selected)
    daily.sort(key=lambda d:d["day"])
    dates=[d["day"] for d in daily]
    out=[]
    diagnostics=[]
    for i,day in enumerate(daily):
        # Rolling 45-day fit; each side's historical outcomes are first
        # observable after the prior market session is completed.
        prior=[candidate
               for prior_day in daily[max(0,i-PRIOR_MARKET_SESSIONS):i]
               for event in prior_day["events"]
               for candidate in event["sides"].values()]
        for event in day["events"]:
            forecasts={side:analog_forecast(prior,c["features"])
                       for side,c in event["sides"].items()}
            for arm in ARMS:
                # The first 45 sessions are WARMUP ONLY for any forecast
                # policy; the two static arms are reference strategies.
                if i<PRIOR_MARKET_SESSIONS and arm not in ARMS[:2]:
                    continue
                picked=choose(event,forecasts,arm)
                if not picked:
                    continue
                side=picked["side"]
                predicted=forecasts.get(side)
                out.append({
                    "day":day["day"],"arm":arm,"signal_at":event["signal_at"],
                    "original_right":event["original_right"],
                    "side":side,"option_symbol":picked["symbol"],
                    "pnl_usd":picked["pnl_usd"],
                    "option_return_pct":picked["option_return_pct"],
                    "entry_debit_one_contract":picked["entry_debit_one_contract"],
                    "predicted_stressed_net_usd":predicted["predicted_stressed_net_usd"]
                        if predicted else None,
                    "predicted_win_pct":predicted["predicted_win_pct"]
                        if predicted else None,
                })
            if i>=PRIOR_MARKET_SESSIONS:
                diagnostics.append({
                    "day":day["day"],"original_right":event["original_right"],
                    "signal_at":event["signal_at"],
                    "paired_quotes":len(event["sides"])==2,
                    "call_model":forecasts.get("call"),
                    "put_model":forecasts.get("put"),
                })
    eval_dates=dates[PRIOR_MARKET_SESSIONS:]
    after=[x for x in out if x["day"] in set(eval_dates)]
    per={arm:{
        "post_warmup_all_sessions":summarize(after,eval_dates,arm,"all_inspected"),
        "predeclared_regimes":{label:summarize(after,eval_dates,arm,label)
                                for label in PERIODS[:-1]},
        "five_day_block_bootstrap":daily_block_bootstrap(after,eval_dates,arm),
    } for arm in ARMS}
    result={
        "study":"EDGEFORGE_BIDIRECTIONAL_OPTION_NET_PNL_ANALOG_FROZEN_30PCT_TRAIL",
        "status":"RESEARCH_ONLY_NOT_BROKER_FILLS",
        "new_market_data_cost_usd":0,"orders_submitted":0,
        "frozen_trailing_stop_pct":TRAIL_PERCENT,
        "independent_hard_loss_pct":INITIAL_HARD_STOP,
        "max_hold_minutes":MAX_HOLD_MINUTES,
        "profit_targets":False,"partial_exits":False,
        "seen_session_count":len(dates),
        "prior_completed_market_sessions":PRIOR_MARKET_SESSIONS,
        "test_sessions_after_warmup":len(eval_dates),
        "fixed_policy_count":len(ARMS),
        "modeled_extra_roundtrip_slippage_usd":EXTRA_FRICTION_USD,
        "cash_affordability_study_cap_usd_NOT_CURRENT_ACCOUNT":
            SIMULATED_MAX_ONE_CONTRACT_BUDGET_USD,
        "coverage":dict(coverage),"results":per,
        "limitations":[
            "All dates were inspected previously; no fully untouched holdout.",
            "CBBO-1m snapshots lack independently event-observed executable NBBO.",
            "Real option fill size, slippage, and cash settlement not validated.",
            "Complete intraday SPY-specific full-chain put/call volume unavailable.",
            "Paired long CALL/PUT hypothetical options are independent comparisons not hedged positions.",
            "Model estimates are descriptive frequencies, not calibrated 75% success predictions.",
            "Extra friction stress does not guarantee a realistic execution.",
        ],
        "decision":"Only promote if future prospective paper/fill studies show durable NET expected value.",
    }
    output.mkdir(parents=True,exist_ok=True)
    (output/"report.json").write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
    with (output/"trade_ledger.csv").open("w",newline="") as f:
        cols=["day","arm","signal_at","original_right","side","option_symbol",
              "pnl_usd","option_return_pct","entry_debit_one_contract",
              "predicted_stressed_net_usd","predicted_win_pct"]
        writer=csv.DictWriter(f,fieldnames=cols)
        writer.writeheader()
        writer.writerows(out)
    (output/"forecast_diagnostics.json").write_text(
        json.dumps(diagnostics,indent=2)+"\n")
    print("EDGEFORGE_RESULT "+json.dumps(result,sort_keys=True),flush=True)
    return result


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--data-dir",type=Path,default=Path("/data/research"))
    parser.add_argument("--output-dir",type=Path,default=Path("/data/research/edgeforge"))
    args=parser.parse_args()
    run(args.data_dir,args.output_dir)


if __name__=="__main__":
    main()
