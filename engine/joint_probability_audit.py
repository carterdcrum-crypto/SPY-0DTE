"""Causal SPY directional-magnitude JOINT probability audit, research-only.

Predict the probability a completed-bar close breakout will be followed by
a signed SPY 10-minute price move >0, >2, >5, or <-5 basis points.
All labels are underlying SPY next-bar close forecast PROXIES (NOT actual
options bid/ask fills, account P&L, or proof of 0DTE breakeven).

This code deliberately DOES NOT select trades, turn on adaptive filtering,
connect to a broker, alter Android, or retrieve/purchase any market data.

All probability estimates for session D use outcomes from only the LAST
60 FULLY COMPLETED trading sessions before D. This is a historical
causal replay on ALREADY-INSPECTED data, NOT an untouched holdout.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import statistics
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Sequence

from .burst_research import _research_files, iter_research_directory
from .data import HistoricalFrame
from .free_signal_screen import ET, PERIODS
from .magnitude_edge_study import MagnitudeObservation, build_session_observations

PRIOR_COMPLETE_SESSIONS = 60
MIN_TRAINING_SESSIONS = 40
MIN_TRAINING_EVENTS = 100
CONDITIONAL_SHRINKAGE_PSEUDO_EVENTS = 40
BOOTSTRAP_BLOCK_SESSIONS = 5
BOOTSTRAP_REPS = 1000
BOOTSTRAP_SEED = 4921

TARGETS:tuple[str,...] = ("gt0","gt2","gt5","ltminus5")
FORECAST_VARIANTS = ("past_only_baseline","causal_shrunk_2feature")


@dataclass(frozen=True)
class JointPrediction:
    day: str
    period: str
    signal_at: str
    observed_as_of: str
    proxy_entry_at: str
    proxy_exit_at: str
    breakout_side: str
    confirmed_continuation: bool
    predicted_abs_spy_10m_bps_from_prior_30min: float
    expected_magnitude_bucket: str
    prior_complete_days: int
    prior_event_count: int
    prior_same_feature_event_count: int
    # The probabilities are entirely from older sessions; no future labels.
    past_base_probs: tuple[float,float,float,float]
    past_conditional_probs: tuple[float,float,float,float]
    past_base_expected_signed_spy_bps: float
    past_conditional_expected_signed_spy_bps: float
    # Outcome labels, strictly for AFTER forecast scoring.
    actual_signed_spy_price_proxy_bps: float
    actual_gt0: bool
    actual_gt2: bool
    actual_gt5: bool
    actual_ltminus5: bool


def _date_period(day:date)->str:
    for name,start,stop in PERIODS:
        if start<=day<=stop:
            return name
    return "outside_studied_periods"


def _features(x:MagnitudeObservation)->tuple[bool,str]:
    """Both features are knowable by the 3-minute confirmation close."""
    return x.original.continuation_accepted,x.historical_bucket


def _target(x:MagnitudeObservation)->tuple[int,int,int,int]:
    signed=x.observed_signed_spy_10min_bps
    return (int(signed>0),int(signed>2),int(signed>5),int(signed< -5))


def _laplace_prob(rows:Sequence[MagnitudeObservation])->tuple[float,float,float,float]:
    n=len(rows)
    sums=[0,0,0,0]
    for obs in rows:
        for j,value in enumerate(_target(obs)):
            sums[j]+=value
    # Smoothed with fixed Beta(1,1) prior for all 4 outcomes.
    return tuple((count+1)/(n+2) for count in sums)


def _probability_pair(prior:Sequence[MagnitudeObservation],
                      today:MagnitudeObservation):
    overall=_laplace_prob(prior)
    feature=_features(today)
    local=[obs for obs in prior if _features(obs)==feature]
    n=len(local)
    counts=[0,0,0,0]
    for row in local:
        for i,value in enumerate(_target(row)):
            counts[i]+=value
    shrink=CONDITIONAL_SHRINKAGE_PSEUDO_EVENTS
    shrunk=tuple((counts[i]+shrink*overall[i])/(n+shrink)
                 for i in range(4))
    base_mean=statistics.fmean(x.observed_signed_spy_10min_bps for x in prior)
    local_sum=sum(x.observed_signed_spy_10min_bps for x in local)
    mean=(local_sum+shrink*base_mean)/(n+shrink)
    return overall,shrunk,n,base_mean,mean


def forecast_day(prior:Sequence[Sequence[MagnitudeObservation]],
                 today:Sequence[MagnitudeObservation],
                 day:date)->list[JointPrediction]:
    """Outcome audit only. The preexisting research adaptive gate remains OFF."""
    if len(prior)<MIN_TRAINING_SESSIONS:
        return []
    # Train exclusively on 60 earlier FULL trading sessions incl. zero-event.
    train=[x for row in prior[-PRIOR_COMPLETE_SESSIONS:] for x in row]
    if len(train)<MIN_TRAINING_EVENTS:
        return []
    period=_date_period(day)
    predicted=[]
    for obs in today:
        # This computes conditional distributions ONLY from older labels.
        base,conditional,n,base_mean,conditional_mean=_probability_pair(train,obs)
        signed=obs.observed_signed_spy_10min_bps
        predicted.append(JointPrediction(
            day=day.isoformat(),period=period,
            signal_at=obs.original.signal_at,
            observed_as_of=obs.original.confirmation_at,
            proxy_entry_at=obs.original.proxy_entry_at,
            proxy_exit_at=obs.original.proxy_exit_at,
            breakout_side=obs.original.original_direction,
            confirmed_continuation=obs.original.continuation_accepted,
            predicted_abs_spy_10m_bps_from_prior_30min=obs.forecast_expected_abs_spy_10min_bps,
            expected_magnitude_bucket=obs.historical_bucket,
            prior_complete_days=min(len(prior),PRIOR_COMPLETE_SESSIONS),
            prior_event_count=len(train),
            prior_same_feature_event_count=n,
            past_base_probs=base,past_conditional_probs=conditional,
            past_base_expected_signed_spy_bps=base_mean,
            past_conditional_expected_signed_spy_bps=conditional_mean,
            actual_signed_spy_price_proxy_bps=signed,
            actual_gt0=signed>0,actual_gt2=signed>2,
            actual_gt5=signed>5,actual_ltminus5=signed< -5,
        ))
    return predicted


def brier(prob:float,label:bool)->float:
    if not math.isfinite(prob) or not 0<=prob<=1:
        raise ValueError("invalid causal probability")
    return (prob-float(label))**2


def _ci_daily_delta(rows:Sequence[JointPrediction],days:Sequence[str],
                    target:int)->dict:
    if not days:
        return {"ci":None,"days":0}
    base=defaultdict(list)
    model=defaultdict(list)
    field="actual_"+TARGETS[target]
    for r in rows:
        outcome=getattr(r,field)
        base[r.day].append(brier(r.past_base_probs[target],outcome))
        model[r.day].append(brier(r.past_conditional_probs[target],outcome))
    # Equal market-day weighting, ZERO contribution for no-signal sessions.
    d=[(statistics.fmean(model[day])-statistics.fmean(base[day])
        if day in base else 0.) for day in days]
    n=len(d)
    rng=random.Random(BOOTSTRAP_SEED)
    block=min(BOOTSTRAP_BLOCK_SESSIONS,n)
    sample=[]
    for _ in range(BOOTSTRAP_REPS):
        pick=[]
        while len(pick)<n:
            start=rng.randrange(n)
            pick.extend((start+i)%n for i in range(block))
        sample.append(statistics.fmean(d[k] for k in pick[:n]))
    sample.sort()
    return {
        "ci":[round(sample[round(.025*(len(sample)-1))],7),
              round(sample[round(.975*(len(sample)-1))],7)],
        "observed_paired_daily_delta_brier_conditional_minus_baseline":round(statistics.fmean(d),7),
        "days_including_no_signal":n,
        "days_with_predictions":len(base),
        "method":"paired circular 5 complete-session blocks; descriptive, known archive",
        "negative_delta_is_better":True,
    }


def _calibration(rows:Sequence[JointPrediction],which:int,
                 model:str)->list[dict]:
    field="actual_"+TARGETS[which]
    probs=[
        (r.past_base_probs[which] if model=="past_only_baseline"
         else r.past_conditional_probs[which]) for r in rows]
    outcomes=[getattr(r,field) for r in rows]
    report=[]
    # Fixed decile boundaries; small groups flagged.
    for bucket in range(10):
        indices=[i for i,p in enumerate(probs)
                 if min(9,int(p*10))==bucket]
        if not indices:
            continue
        report.append({
            "target":TARGETS[which],"model":model,
            "probability_interval":f"[{bucket/10:.1f},{(bucket+1)/10:.1f}]",
            "count":len(indices),"small_bucket_fewer_than_20":len(indices)<20,
            "average_forecast":round(statistics.fmean(probs[i] for i in indices),6),
            "actual_event_rate":round(sum(outcomes[i] for i in indices)/len(indices),6),
        })
    return report


def _metrics(rows:Sequence[JointPrediction],days:Sequence[str])->dict:
    out={
        "calendar_days":len(days),"eligible_predictions":len(rows),
        "days_with_predictions":len({r.day for r in rows}),
        "options_fills":None,"option_PnL":None,"account_return":None,
        "account_max_drawdown":None,"autonomous_trades_created":0,
    }
    for i,target in enumerate(TARGETS):
        outcomes=[getattr(r,"actual_"+target) for r in rows]
        observed=sum(outcomes)
        base=[brier(r.past_base_probs[i],outcomes[k])
              for k,r in enumerate(rows)]
        condition=[brier(r.past_conditional_probs[i],outcomes[k])
                   for k,r in enumerate(rows)]
        out[target]={
            "outcomes":observed,"actual_rate":observed/len(rows) if rows else None,
            "baseline_brier":round(statistics.fmean(base),7) if base else None,
            "conditional_brier":round(statistics.fmean(condition),7) if condition else None,
            "conditional_minus_baseline_brier":(
                round(statistics.fmean(condition)-statistics.fmean(base),7)
                if base else None),
            "day_block_delta_ci":_ci_daily_delta(rows,days,i),
        }
    return out


def run_study(frames:Iterable[HistoricalFrame])->dict:
    past=[]
    current=[]
    previous=None
    minutes=0
    sessions=0
    by_period=defaultdict(list)
    included_days=defaultdict(list)
    ledger=[]

    def flush():
        nonlocal current,sessions
        if not current:
            return
        day=current[0].timestamp.astimezone(ET).date()
        sessions+=1
        observed=build_session_observations(current)
        per=_date_period(day)
        if per in {label for label,_,_ in PERIODS}:
            included_days[per].append(day.isoformat())
        # ALL predictions made from past only; append today's labels LAST.
        forecasted=forecast_day(past,observed,day)
        if per in {label for label,_,_ in PERIODS}:
            by_period[per].extend(forecasted)
        ledger.extend(forecasted)
        past.append(observed)
        current=[]
    for frame in frames:
        day=frame.timestamp.astimezone(ET).date()
        if previous is not None and day<previous:
            raise ValueError("session input out of chronological order")
        if previous is not None and day!=previous:
            flush()
        current.append(frame)
        previous=day
        minutes+=1
    flush()
    summary={}
    calibration=[]
    for name,_,_ in PERIODS:
        rows=by_period[name]
        # Warmup days lack predictions and are explicitly included as days.
        summary[name]=_metrics(rows,included_days[name])
        for target_index in range(len(TARGETS)):
            for model in FORECAST_VARIANTS:
                for item in _calibration(rows,target_index,model):
                    calibration.append({"period":name,**item})
    return {
        "experiment":"causal_joint_direction_magnitude_probability_score_v1",
        "minutes":minutes,"sessions":sessions,
        "configuration":{
            "prior_full_market_sessions":PRIOR_COMPLETE_SESSIONS,
            "minimum_prior_sessions":MIN_TRAINING_SESSIONS,
            "minimum_training_events":MIN_TRAINING_EVENTS,
            "conditional_pseudoevents":CONDITIONAL_SHRINKAGE_PSEUDO_EVENTS,
            "features":"only confirmation at T+3: prior rule continuation true/false and expected underlying SPY abs10m >=5bps",
            "targets":TARGETS,
            "method":"lagged empirical base rates, Laplace beta prior, same-feature empirical Bayes 40-pseudo-event shrinkage",
        },
        "comparison_to_zero_trade":"NO trades are authorized or simulated; Brier proper score, not an equity return",
        "reused_historical_data_not_untouched_holdout":True,
        "retrospective_model_evidence_not_proof_of_future_generalization":True,
        "adaptive_signal_filter_enabled":False,
        "live_auto_trade_modified":False,
        "new_data_purchased_usd":0,
        "reports":summary,
        "calibration":calibration,
        "signal_ledger":ledger,
    }


def _csv(path:Path,rows:Sequence[dict])->None:
    with path.open("w",newline="",encoding="utf8") as f:
        if rows:
            writer=csv.DictWriter(f,fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)


def main(argv:Sequence[str]|None=None)->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir",type=Path,default=Path("/data/research"))
    parser.add_argument("--output-dir",type=Path,
                        default=Path("/data/research/joint_probability"))
    args=parser.parse_args(argv)
    files=_research_files(args.data_dir)
    if not files:
        print("JOINT PROBABILITY BLOCKED: no previously licensed archives",flush=True)
        return 2
    result=run_study(iter_research_directory(args.data_dir))
    dest=args.output_dir
    dest.mkdir(parents=True,exist_ok=True)
    predictions=result.pop("signal_ledger")
    cal=result.pop("calibration")
    ledger=[]
    for pred in predictions:
        d=asdict(pred)
        for name,probs in (
            ("p_base",pred.past_base_probs),
            ("p_cond",pred.past_conditional_probs),
        ):
            for index,target in enumerate(TARGETS):
                d[f"{name}_{target}"]=probs[index]
        del d["past_base_probs"]
        del d["past_conditional_probs"]
        ledger.append(d)
    _csv(dest/"predictions_AND_future_outcome_labels_NOT_OPTION_TRADES.csv",ledger)
    _csv(dest/"fixed_probability_bins.csv",cal)
    sha=hashlib.sha256()
    for f in files:
        sha.update(f.name.encode())
        sha.update(str(f.stat().st_size).encode())
    result["source_files"]=len(files)
    result["source_size_fingerprint"]=sha.hexdigest()
    (dest/"summary.json").write_text(json.dumps(result,indent=2,sort_keys=True))
    print("JOINT PREDICTION COVERAGE: "+json.dumps({
        "sessions":result["sessions"],"market_frames":result["minutes"],
        "predictions":len(ledger),"archives":len(files),
        "data_purchases_usd":0,"adaptive_gate_off":True,
        "report_path":str(dest),
    },sort_keys=True),flush=True)
    for period,data in result["reports"].items():
        for target in TARGETS:
            d=data[target]
            print("JOINT PREDICTION BRIER: "+json.dumps({
                "period":period,"target":target,
                "n":data["eligible_predictions"],
                "eligible_sessions":data["calendar_days"],
                "actual_successes":d["outcomes"],
                "actual_event_rate":d["actual_rate"],
                "baseline_brier":d["baseline_brier"],
                "conditional_brier":d["conditional_brier"],
                "conditional_minus_baseline":d["conditional_minus_baseline_brier"],
                "paired_dayblock_ci":d["day_block_delta_ci"]["ci"],
                "real_option_pnl":None,
            },sort_keys=True),flush=True)
    for row in cal:
        if row["target"] in ("gt2","gt5"):
            print("JOINT PREDICTION CALIBRATION: "+json.dumps(row,sort_keys=True),flush=True)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
