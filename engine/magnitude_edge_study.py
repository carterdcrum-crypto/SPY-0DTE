"""SPY intraday *underlying* move-magnitude forecasts with strictly causal gates.

Research-only on archived, licensed minute CLOSE data; no new data purchase,
option quotes, execution fills, implied option breakeven, account equity or
live trading. The 2/5 bp barriers are hypothetical SPY underlying sensitivity
hurdles, explicitly NOT 0DTE option break-even requirements.

Every forecast uses already-completed SPY bars at the T+3 confirmation clock.
The signed forecast outcome uses the NEXT completed minute's CLOSE and the
close 10 minutes afterward: neither is a guaranteed executable price.
Past-expectation gate may see only events from 40 PRIOR completed sessions,
including those skipped by its own decisions, never today's future outcomes.
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
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, Sequence

from .burst_research import _research_files, iter_research_directory
from .causal_breakout_router import (
    DelayedEvent, RouterConfig, session_delayed_events,
)
from .data import HistoricalFrame
from .free_signal_screen import ET, PERIODS, _bps

FORECAST_HOLD_MINUTES = 10
PRIOR_COMPLETE_SESSIONS = 40
MIN_BASELINE_BUCKET_OBSERVATIONS = 30
MIN_CONTINUATION_OBSERVATIONS = 8
ONE_SIDED_Z = 1.645  # fixed conservative screen; NOT valid holdout inference
FRICTION_BPS = 2.0
MIN_FORECAST_MAGNITUDE_BPS = 5.0
HIGH_FORECAST_MAGNITUDE_BPS = 10.0
BOOTSTRAP_BLOCK_SESSIONS = 5
BOOTSTRAP_REPS = 1000
BOOTSTRAP_SEED = 20931
POLICIES = (
    "same_clock_delayed_baseline",
    "spot_magnitude_ge_5bps",
    "spot_magnitude_ge_10bps",
    "past40_lowerbound_plus_magnitude",
    "confirmed_continuation_baseline",
    "confirmed_continuation_plus_magnitude",
    "confirmed_continuation_past40_lowerbound",
)


@dataclass(frozen=True)
class MagnitudeObservation:
    original: DelayedEvent
    forecast_at: str
    spot_30min_standard_deviation_one_minute_bps: float
    forecast_expected_abs_spy_10min_bps: float
    observed_absolute_spy_10min_bps: float
    observed_signed_spy_10min_bps: float
    historical_bucket: str
    # All three quantities below are ONLY OUTCOME LABELS, not gate inputs:
    later_abs_move_gt_5bps: bool
    later_signed_move_gt_2bps: bool
    later_signed_move_gt_5bps: bool


@dataclass(frozen=True)
class PastEvidence:
    complete_prior_sessions: int
    past_matching_events: int
    mean_past_net_spy_bps: float | None
    lower_bound_past_net_spy_bps: float | None
    historical_positive_after_2bps_fraction: float | None
    gate_open: bool


@dataclass(frozen=True)
class ScoredDecision:
    observation: MagnitudeObservation
    strategy: str
    accepted: bool
    prior_evidence: PastEvidence
    period: str


def _std(xs:Sequence[float])->float:
    return statistics.stdev(xs) if len(xs)>1 else 0.


def _bucket(forecast:float)->str:
    return "predicted_high_ge_5bp" if forecast>=MIN_FORECAST_MAGNITUDE_BPS else "predicted_low_below_5bp"


def build_session_observations(frames:Sequence[HistoricalFrame],
                               cfg:RouterConfig=RouterConfig())->list[MagnitudeObservation]:
    """Only information through confirmation minute may enter forecast."""
    if not frames:
        return []
    del_events=session_delayed_events(frames,cfg)
    times=[f.timestamp for f in frames]
    price=[f.market.spot for f in frames]
    by_completed={stamp+timedelta(minutes=1):index
                  for index,stamp in enumerate(times)}
    out=[]
    for ev in del_events:
        confirm=by_completed[datetime.fromisoformat(ev.confirmation_at)]
        if confirm<30:
            continue
        # Upstream router already verified a continuous 30 minute history
        # and complete outcome horizon. Recheck to fail closed standalone.
        if any((times[i+1]-times[i]).total_seconds()!=60
               for i in range(confirm-30,confirm)):
            continue
        sample=[_bps(price[k],price[k-1])
                for k in range(confirm-29,confirm+1)]
        if any(not math.isfinite(q) for q in sample):
            continue
        sigma=_std(sample)
        # Forecast expected absolute magnitude of a zero-drift normal random
        # walk: E|X_10|=sigma_1m*sqrt(10)*sqrt(2/pi). Descriptive baseline,
        # NOT a fitted predictor of CALL vs PUT return or option delta.
        expected=sigma*math.sqrt(FORECAST_HOLD_MINUTES)*math.sqrt(2/math.pi)
        realized=ev.delayed_raw_breakout_direction_signed_spy_bps
        out.append(MagnitudeObservation(
            original=ev,forecast_at=ev.confirmation_at,
            spot_30min_standard_deviation_one_minute_bps=sigma,
            forecast_expected_abs_spy_10min_bps=expected,
            observed_absolute_spy_10min_bps=abs(realized),
            observed_signed_spy_10min_bps=realized,
            historical_bucket=_bucket(expected),
            later_abs_move_gt_5bps=abs(realized)>MIN_FORECAST_MAGNITUDE_BPS,
            later_signed_move_gt_2bps=realized>FRICTION_BPS,
            later_signed_move_gt_5bps=realized>MIN_FORECAST_MAGNITUDE_BPS,
        ))
    return out


def prior_evidence(
    prior:Sequence[Sequence[MagnitudeObservation]],
    current:MagnitudeObservation,
    *,
    mode:str,
)->PastEvidence:
    """Only previously CLOSED sessions; all eligible events are recorded.

    Gate rules fixed before data analysis; no fitted coefficients or
    cross-validation based on current/outcome. May be underpowered.
    """
    if mode not in ("baseline","continuation"):
        raise ValueError("invalid evidence category")
    candidates=[obs for session in prior[-PRIOR_COMPLETE_SESSIONS:] for obs in session
                if (obs.original.continuation_accepted
                    if mode=="continuation"
                    else obs.historical_bucket==current.historical_bucket)]
    n=len(candidates)
    enough=(len(prior)>=PRIOR_COMPLETE_SESSIONS and n>=
            (MIN_CONTINUATION_OBSERVATIONS if mode=="continuation"
             else MIN_BASELINE_BUCKET_OBSERVATIONS))
    if not candidates:
        return PastEvidence(len(prior),0,None,None,None,False)
    net=[e.observed_signed_spy_10min_bps-FRICTION_BPS for e in candidates]
    mu=statistics.fmean(net)
    lower=mu-ONE_SIDED_Z*_std(net)/math.sqrt(n) if n>=2 else float("-inf")
    wins=sum(v>0 for v in net)/n
    return PastEvidence(
        len(prior),n,mu,lower,wins,
        bool(enough and lower>0 and
             current.forecast_expected_abs_spy_10min_bps>=MIN_FORECAST_MAGNITUDE_BPS),
    )


def choose(policy:str,obs:MagnitudeObservation,
           evidence_base:PastEvidence,evidence_cont:PastEvidence)->bool:
    """Absolutely no future outcome reference here (test with poisoned labels)."""
    if policy not in POLICIES:
        raise ValueError(f"unknown fixed magnitude research rule: {policy}")
    magnitude=obs.forecast_expected_abs_spy_10min_bps
    cont=obs.original.continuation_accepted
    if policy=="same_clock_delayed_baseline":
        return True
    if policy=="spot_magnitude_ge_5bps":
        return magnitude>=MIN_FORECAST_MAGNITUDE_BPS
    if policy=="spot_magnitude_ge_10bps":
        return magnitude>=HIGH_FORECAST_MAGNITUDE_BPS
    if policy=="past40_lowerbound_plus_magnitude":
        return evidence_base.gate_open
    if policy=="confirmed_continuation_baseline":
        return cont
    if policy=="confirmed_continuation_plus_magnitude":
        return cont and magnitude>=MIN_FORECAST_MAGNITUDE_BPS
    return cont and evidence_cont.gate_open


def _period(d:date)->str:
    for label,first,last in PERIODS:
        if first<=d<=last:
            return label
    return "outside_studied_periods"


def _bootstrap(net_by_day:Sequence[float])->dict:
    if not net_by_day:
        return {"available":False}
    n=len(net_by_day)
    chunk=min(n,BOOTSTRAP_BLOCK_SESSIONS)
    rng=random.Random(BOOTSTRAP_SEED)
    estimates=[]
    for _ in range(BOOTSTRAP_REPS):
        indices=[]
        while len(indices)<n:
            start=rng.randrange(n)
            indices.extend((start+i)%n for i in range(chunk))
        estimates.append(statistics.fmean(net_by_day[index] for index in indices[:n]))
    estimates.sort()
    return {
        "samples":BOOTSTRAP_REPS,
        "circular_block_complete_sessions":chunk,
        "95pct_daily_signed_spy_bps_after_2bp_proxy":[
            round(estimates[round(.025*(len(estimates)-1))],4),
            round(estimates[round(.975*(len(estimates)-1))],4)],
        "not_financial_account_returns":True,
    }


def _summary(decisions:Sequence[ScoredDecision],
             calendar_days:Sequence[str])->dict:
    accepted=[d.observation for d in decisions if d.accepted]
    predictions=[r.forecast_expected_abs_spy_10min_bps for r in accepted]
    raw=[r.observed_signed_spy_10min_bps for r in accepted]
    net2=[v-FRICTION_BPS for v in raw]
    net5=[v-MIN_FORECAST_MAGNITUDE_BPS for v in raw]
    actual_magnitude=[o.observed_absolute_spy_10min_bps for o in accepted]
    net_by_day=defaultdict(float)
    for o,v in zip(accepted,net2):
        net_by_day[o.original.session]+=v
    daily=[net_by_day[d] for d in calendar_days]
    weekly=defaultdict(float)
    for d,v in zip(calendar_days,daily):
        iso=date.fromisoformat(d).isocalendar()
        weekly[(iso.year,iso.week)]+=v
    cumulative=0.
    top=0.
    decline=0.
    for v in daily:
        cumulative+=v
        top=max(top,cumulative)
        decline=max(decline,top-cumulative)
    return {
        "observed_sessions":len(calendar_days),
        "eligible_same_clock_events":len(decisions),
        "accepted":len(accepted),"abstained":len(decisions)-len(accepted),
        "signal_days":len({r.original.session for r in accepted}),
        "correct_spy_direction":sum(x>0 for x in raw),
        "positive_after_2bp_underlying_proxy":sum(x>0 for x in net2),
        "positive_after_5bp_underlying_proxy":sum(x>0 for x in net5),
        "actual_spy_abs_gt5bp_events":sum(x>MIN_FORECAST_MAGNITUDE_BPS for x in actual_magnitude),
        "hit_rate_spy_direction":sum(x>0 for x in raw)/len(raw) if raw else None,
        "fraction_signed_move_exceeds_hypothetical_2bp_hurdle":sum(x>0 for x in net2)/len(net2) if net2 else None,
        "fraction_signed_move_exceeds_hypothetical_5bp_hurdle":sum(x>0 for x in net5)/len(net5) if net5 else None,
        "mean_forecast_abs_spy10m_bps":round(statistics.fmean(predictions),4) if predictions else None,
        "mean_realized_abs_spy10m_bps":round(statistics.fmean(actual_magnitude),4) if accepted else None,
        "mean_absolute_forecast_error_spy_bps":(
            round(statistics.fmean(abs(a-b) for a,b in zip(predictions,actual_magnitude)),4)
            if predictions else None),
        "mean_signed_spy_bps":round(statistics.fmean(raw),4) if raw else None,
        "mean_signed_spy_bps_minus_2bp_assumption":round(statistics.fmean(net2),4) if net2 else None,
        "mean_signed_spy_bps_minus_5bp_assumption":round(statistics.fmean(net5),4) if net5 else None,
        "mean_signed_spy_bps_per_session_after_2bp_incl_abstentions":(
            round(sum(net2)/len(calendar_days),4) if calendar_days else None),
        "worst_single_session_signed_spy_bps_after_2bp":round(min(daily),4) if daily else None,
        "worst_week_additive_signed_spy_bps_after_2bp":round(min(weekly.values()),4) if weekly else None,
        "max_additive_signed_spy_bps_decline_NOT_ACCOUNT_DRAWDOWN":round(decline,4),
        "positive_week_fraction_signed_spy_bps_NOT_ACCOUNT_RETURN":(
            sum(v>0 for v in weekly.values())/len(weekly) if weekly else None),
        "bootstrap":_bootstrap(daily),
        "account_return":None,"actual_option_profit":None,"account_max_drawdown":None,
    }


def _calibration(rows:Sequence[MagnitudeObservation],period:str)->list[dict]:
    groups=defaultdict(list)
    for row in rows:
        groups[row.historical_bucket].append(row)
    out=[]
    for bucket,xs in sorted(groups.items()):
        returns=[e.observed_signed_spy_10min_bps for e in xs]
        actual=[e.observed_absolute_spy_10min_bps for e in xs]
        preds=[e.forecast_expected_abs_spy_10min_bps for e in xs]
        out.append({
            "period":period,"magnitude_forecast_bin":bucket,
            "count":len(xs),
            "predicted_mean_abs_spy10m_bps":round(statistics.fmean(preds),4),
            "realized_mean_abs_spy10m_bps":round(statistics.fmean(actual),4),
            "abs_move_gt5bp_fraction":sum(a>5 for a in actual)/len(xs),
            "signed_move_gt2bp_fraction":sum(a>2 for a in returns)/len(xs),
            "signed_move_gt5bp_fraction":sum(a>5 for a in returns)/len(xs),
            "signed_move_gt2bp_for_continuation_subset":(
                sum(e.observed_signed_spy_10min_bps>2 for e in xs
                    if e.original.continuation_accepted)/
                sum(e.original.continuation_accepted for e in xs)
                if sum(e.original.continuation_accepted for e in xs) else None),
            "continuation_count":sum(e.original.continuation_accepted for e in xs),
            "interpretation":"posthoc group calibration, not an optimized trade signal or real 0DTE breakeven",
        })
    return out


def run_study(frames:Iterable[HistoricalFrame])->dict:
    """Replay day in ascending order; assess day's decisions BEFORE storing labels."""
    prior:list[list[MagnitudeObservation]]=[]
    last_day=None
    current=[]
    days=defaultdict(list)
    rows=defaultdict(list)
    decisions=defaultdict(lambda:defaultdict(list))
    gates=[]
    framecount=0
    sessions=0

    def flush()->None:
        nonlocal current,sessions
        if not current:
            return
        date_=current[0].timestamp.astimezone(ET).date()
        per=_period(date_)
        sessions+=1
        observed=build_session_observations(current)
        if per in {x[0] for x in PERIODS}:
            days[per].append(date_.isoformat())
            rows[per].extend(observed)
        for obs in observed:
            # Both histories contain only preceding FULL completed sessions.
            base=prior_evidence(prior,obs,mode="baseline")
            cont=prior_evidence(prior,obs,mode="continuation")
            if per in {x[0] for x in PERIODS}:
                for policy in POLICIES:
                    decisions[per][policy].append(
                        ScoredDecision(obs,policy,choose(policy,obs,base,cont),base
                                       if policy=="past40_lowerbound_plus_magnitude"
                                       else cont,per))
            gates.append({
                "session":date_.isoformat(),
                "original_signal_at":obs.original.signal_at,
                "forecast_at":obs.forecast_at,
                "entry_proxy_at":obs.original.proxy_entry_at,
                "direction":obs.original.original_direction,
                "predicted_spy_absolute_10min_bps":round(obs.forecast_expected_abs_spy_10min_bps,5),
                "actual_spy_absolute_10min_bps_LABEL_ONLY":round(obs.observed_absolute_spy_10min_bps,5),
                "actual_spy_signed_10min_bps_LABEL_ONLY":round(obs.observed_signed_spy_10min_bps,5),
                "continuation_at_signal":obs.original.continuation_accepted,
                "prior_base_n":base.past_matching_events,
                "prior_base_lower_bound_spy_bps":base.lower_bound_past_net_spy_bps,
                "prior_cont_n":cont.past_matching_events,
                "prior_cont_lower_bound_spy_bps":cont.lower_bound_past_net_spy_bps,
                **{policy:choose(policy,obs,base,cont) for policy in POLICIES},
            })
        prior.append(observed)
        current=[]

    for f in frames:
        day=f.timestamp.astimezone(ET).date()
        if last_day is not None and day<last_day:
            raise ValueError("SPY sessions must be in chronological order")
        if last_day is not None and day!=last_day:
            flush()
        current.append(f)
        last_day=day
        framecount+=1
    flush()
    summaries={}
    calib=[]
    for name,_,_ in PERIODS:
        summaries[name]={}
        for policy in POLICIES:
            # Every same-clock event gets an explicit accept/abstain row;
            # includes no-event sessions when computing net daily bps.
            summaries[name][policy]=_summary(decisions[name][policy],days[name])
        calib.extend(_calibration(rows[name],name))
    return {
        "experiment":"causal_spy_expected_magnitude_vs_expected_signed_move_v1",
        "days_read":sessions,"minute_frames_read":framecount,
        "forecaster":"rolling 30 prior one-minute close-to-close SPY bps sample stdev; zero-drift Gaussian random walk expected abs10=sigma*sqrt(10)*sqrt(2/pi)",
        "policies":POLICIES,
        "fixed_hurdles_underlying_bps":[FRICTION_BPS,MIN_FORECAST_MAGNITUDE_BPS,HIGH_FORECAST_MAGNITUDE_BPS],
        "prior_training_sessions":PRIOR_COMPLETE_SESSIONS,
        "past_mean_lower_bound_z":ONE_SIDED_Z,
        "past_baseline_min_sample":MIN_BASELINE_BUCKET_OBSERVATIONS,
        "past_continuation_min_sample":MIN_CONTINUATION_OBSERVATIONS,
        "chronological_periods_PREVIOUSLY_STUDIED_NOT_UNTOUCHED":True,
        "models":summaries,
        "calibration":calib,
        "decision_ledger":gates,
        "options_trades_executed":0,
        "new_market_data_purchased":False,
        "option_PnL":None,"account_return":None,
        "evidence_weekly_account_100pct":False,
    }


def _save_csv(path:Path,rows:Sequence[dict])->None:
    with path.open("w",newline="",encoding="utf-8") as f:
        if rows:
            writer=csv.DictWriter(f,fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)


def main(argv:Sequence[str]|None=None)->int:
    arg=argparse.ArgumentParser(description=__doc__)
    arg.add_argument("--data-dir",type=Path,default=Path("/data/research"))
    arg.add_argument("--output-dir",type=Path,default=Path("/data/research/magnitude_edge"))
    a=arg.parse_args(argv)
    sources=_research_files(a.data_dir)
    if not sources:
        print("MAGNITUDE STUDY BLOCKED: archive missing",flush=True)
        return 2
    results=run_study(iter_research_directory(a.data_dir))
    a.output_dir.mkdir(exist_ok=True,parents=True)
    ledger=results.pop("decision_ledger")
    calib=results.pop("calibration")
    _save_csv(a.output_dir/"ALL_EVENT_PAST_ONLY_GATE_DECISIONS_NOT_OPTIONS_PNL.csv",ledger)
    _save_csv(a.output_dir/"volatility_magnitude_calibration_NOT_OPTION_BREAK_EVEN.csv",calib)
    fp=hashlib.sha256()
    for f in sources:
        fp.update(f.name.encode())
        fp.update(str(f.stat().st_size).encode())
    results["source_archives"]=len(sources)
    results["file_fingerprint_sha256"]=fp.hexdigest()
    results["purpose"]="magnitude-vs-direction research; not executable option backtest"
    (a.output_dir/"results.json").write_text(json.dumps(results,indent=2,sort_keys=True))
    print("MAGNITUDE STUDY COMPLETE: "+json.dumps({
        "days":results["days_read"],"frames":results["minute_frames_read"],
        "source_archives":len(sources),"event_count":len(ledger),
        "new_data_purchase_usd":0,
        "path":str(a.output_dir)},sort_keys=True),flush=True)
    for period,models in results["models"].items():
        for policy,stats in models.items():
            print("MAGNITUDE POLICY RESULT: "+json.dumps({
                "period":period,"policy":policy,
                "events":stats["eligible_same_clock_events"],
                "accepted":stats["accepted"],
                "correct_spy_direction":stats["correct_spy_direction"],
                "signed_gt2bp":stats["positive_after_2bp_underlying_proxy"],
                "signed_gt5bp":stats["positive_after_5bp_underlying_proxy"],
                "forecast_abs10bps":stats["mean_forecast_abs_spy10m_bps"],
                "actual_abs10bps":stats["mean_realized_abs_spy10m_bps"],
                "mean_after_2bp_proxy":stats["mean_signed_spy_bps_minus_2bp_assumption"],
                "net_spy_bps_daily_including_no_trades":stats["mean_signed_spy_bps_per_session_after_2bp_incl_abstentions"],
                "daily_bootstrap95":stats["bootstrap"].get("95pct_daily_signed_spy_bps_after_2bp_proxy"),
                "real_account_pnl":None,
            },sort_keys=True),flush=True)
    for row in calib:
        print("MAGNITUDE CALIBRATION: "+json.dumps(row,sort_keys=True),flush=True)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
