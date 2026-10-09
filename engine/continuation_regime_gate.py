"""Free investigation of the 10/13 vs 3/7 confirmed SPY breakout forecasts.

Research-only, existing archived minute-close data, no vendor fetch or orders.
Signed SPY basis points are NOT option profits, account returns, or fills.

One FIXED prior-session-only adaptive gate is evaluated as a prospective
*mechanical historical replay* on previously inspected data: each day's go/no-go
decision must use the immediately previous 30 *complete* sessions only. No
tuning, hindsight gating, same-day lookahead or claims of untouched holdout.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import statistics
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Iterable, Sequence

from .burst_research import _research_files, iter_research_directory
from .data import HistoricalFrame
from .causal_breakout_router import (
    DelayedEvent, RouterConfig, session_delayed_events, POLICIES,
)
from .free_signal_screen import ET, PERIODS

PRIOR_SESSIONS = 30
MIN_PRIOR_CONFIRMED_EVENTS = 8
MIN_PRIOR_HIT_RATE = 0.60
MIN_PRIOR_MEAN_NET_BPS = 0.0
FRICTION_BPS = 2.0
BOOTSTRAP_SESSIONS = 5
BOOTSTRAP_REPS = 1000
BOOTSTRAP_SEED = 79413


@dataclass(frozen=True)
class GateRecord:
    session: str
    date_start: str
    history_sessions: int
    history_events: int
    history_hit_fraction: float | None
    history_net_spy_bps_per_event: float | None
    gate_open: bool
    new_confirmed_signals: int
    taken: int
    new_raw_signed_spy_bps: float
    new_net_signed_spy_bps: float
    session_period: str


def period_name(day:date)->str:
    for label,first,last in PERIODS:
        if first<=day<=last:
            return label
    return "outside_studied_periods"


def winsorless_mean(value:Sequence[float])->float|None:
    return statistics.fmean(value) if value else None


def wilson_interval(success:int,n:int)->tuple[float|None,float|None]:
    if n==0:
        return None,None
    z=1.95996398454
    p=success/n
    denom=1+z*z/n
    midpoint=(p+z*z/(2*n))/denom
    margin=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/denom
    return max(0.,midpoint-margin),min(1.,midpoint+margin)


def fisher_exact_two_sided(a:int,b:int,c:int,d:int)->float:
    """Exact conditional two-sided hypergeometric test (NO SciPy needed).

    This is exploratory: these periods have repeated prior researcher access,
    serial market dependence and changing regimes. An insignificant p does not
    prove that two win probabilities are equal.
    """
    if min(a,b,c,d)<0:
        raise ValueError("negative table count")
    successes=a+c
    failures=b+d
    first=a+b
    total=successes+failures
    if total==0:
        return 1.
    denom=math.comb(total,first)
    def probability(yes:int)->float:
        no=first-yes
        if not (0<=yes<=successes and 0<=no<=failures):
            return 0.
        return math.comb(successes,yes)*math.comb(failures,no)/denom
    observed=probability(a)
    low=max(0,first-failures)
    high=min(first,successes)
    return min(1.,sum(probability(k) for k in range(low,high+1)
                      if probability(k)<=observed+1e-12))


def causal_gate(prior_sessions:Sequence[Sequence[DelayedEvent]])->tuple[bool,int,float|None,float|None]:
    """Evaluate only events in previous FULL sessions. No current-day future."""
    history=[e for session in prior_sessions[-PRIOR_SESSIONS:]
             for e in session if e.continuation_accepted]
    n=len(history)
    if n==0:
        return False,0,None,None
    hits=sum(e.delayed_raw_breakout_direction_signed_spy_bps>0 for e in history)
    mean=statistics.fmean(e.delayed_raw_breakout_direction_signed_spy_bps-FRICTION_BPS
                          for e in history)
    rate=hits/n
    authorized=(len(prior_sessions)>=PRIOR_SESSIONS
                and n>=MIN_PRIOR_CONFIRMED_EVENTS
                and rate>=MIN_PRIOR_HIT_RATE
                and mean>MIN_PRIOR_MEAN_NET_BPS)
    return authorized,n,rate,mean


def _bucket(event:DelayedEvent,field:str)->str:
    if field=="direction":
        return event.original_direction
    if field=="time":
        t=datetime.fromisoformat(event.signal_at).astimezone(ET).time()
        if t<time(11):
            return "open_10_to_11"
        if t<time(13,30):
            return "midday_11_to_1330"
        return "later_1330_to_1525"
    if field=="volume":
        v=event.original_at_signal_volume_ratio
        return "high_ge_1.75" if v>=1.75 else "lower_1.25_to_1.75"
    if field=="trend30":
        x=event.original_30min_trend_signed_bps
        return "strong_ge_15bps" if x>=15 else "minimum_8_to_15bps"
    if field=="efficiency":
        v=event.original_15close_efficiency
        return "efficient_ge_0.6" if v>=.6 else "minimum_0.35_to_0.6"
    if field=="confirm_extension":
        x=event.confirm_extension_signed_bps
        return "near_le_4bps" if x<=4 else "far_4_to_8bps"
    raise ValueError(field)


def _group_details(events:Sequence[DelayedEvent],name:str)->dict:
    returns=[e.delayed_raw_breakout_direction_signed_spy_bps for e in events]
    after=[r-FRICTION_BPS for r in returns]
    win=sum(r>0 for r in returns)
    lower,upper=wilson_interval(win,len(returns))
    loo_means=[]
    if len(returns)>1:
        loo_means=[(sum(after)-v)/(len(after)-1) for v in after]
    days=len({e.session for e in events})
    return {
        "name":name,
        "accepted_events":len(events),
        "days_with_signal":days,
        "correct_spy_direction":win,
        "wrong_or_flat_spy_direction":len(returns)-win,
        "direction_accuracy":win/len(returns) if returns else None,
        "wilson95_spy_direction_hit_interval":(
            [round(lower,4),round(upper,4)] if lower is not None else [None,None]),
        "mean_signed_spy_bps":round(statistics.fmean(returns),4) if returns else None,
        "mean_after_2bp_underlying_friction_proxy":(
            round(statistics.fmean(after),4) if after else None),
        "mean_after_5bp_underlying_friction_proxy":(
            round(statistics.fmean(returns)-5,4) if returns else None),
        "median_after_2bp_proxy":round(statistics.median(after),4) if after else None,
        "worst_event_net_bps":round(min(after),4) if after else None,
        "best_event_net_bps":round(max(after),4) if after else None,
        "sum_net_bps_all_signals_NOT_ACCOUNT_RETURN":round(sum(after),4),
        "one_deleted_net_average_min":round(min(loo_means),4) if loo_means else None,
        "one_deleted_net_average_max":round(max(loo_means),4) if loo_means else None,
        "data_interpretation":"SPY underlying next-close proxy only, NO option profits",
    }


def _strata(events:Sequence[DelayedEvent],period:str)->list[dict]:
    result=[]
    for feature in ("direction","time","volume","trend30","efficiency","confirm_extension"):
        groups=defaultdict(list)
        for event in events:
            groups[_bucket(event,feature)].append(event)
        for label,values in sorted(groups.items()):
            raw=[ev.delayed_raw_breakout_direction_signed_spy_bps for ev in values]
            result.append({
                "period":period,"feature":feature,"bucket":label,
                "events":len(values),
                "warning_very_small_stratum":len(values)<10,
                "direction_hit":sum(x>0 for x in raw),
                "direction_hit_rate":sum(x>0 for x in raw)/len(raw),
                "mean_raw_spy_bps":round(statistics.fmean(raw),4),
                "mean_minus_2bp_spy_proxy":round(statistics.fmean(raw)-FRICTION_BPS,4),
            })
    return result


def _daily_bootstrap(selected:Sequence[GateRecord],all_days:Sequence[str])->dict:
    by_day={d:0. for d in all_days}
    for record in selected:
        by_day[record.session]+=record.new_net_signed_spy_bps
    arr=[by_day[d] for d in all_days]
    if not arr:
        return {"available":False}
    rng=random.Random(BOOTSTRAP_SEED)
    n=len(arr)
    block=min(BOOTSTRAP_SESSIONS,n)
    samples=[]
    for _ in range(BOOTSTRAP_REPS):
        ix=[]
        while len(ix)<n:
            start=rng.randrange(n)
            ix.extend((start+j)%n for j in range(block))
        samples.append(sum(arr[i] for i in ix[:n])/n)
    samples.sort()
    return {
        "circular_block_sessions":block,
        "replicates":BOOTSTRAP_REPS,
        "daily_signed_spy_bps_95pct_interval":[
            round(samples[round(.025*(len(samples)-1))],4),
            round(samples[round(.975*(len(samples)-1))],4)],
        "not_account_return":True,
    }


def run_diagnostic(frames:Iterable[HistoricalFrame])->dict:
    """Streaming session-level causal replay. Past day cannot see today's labels."""
    daily_records=[]
    period_events={name:[] for name,_,_ in PERIODS}
    gate_trades={name:[] for name,_,_ in PERIODS}
    past:list[list[DelayedEvent]]=[]
    current=[]
    old_day=None
    read_frames=0
    read_sessions=0

    def flush():
        nonlocal current,read_sessions
        if not current:
            return
        day=current[0].timestamp.astimezone(ET).date()
        name=period_name(day)
        read_sessions+=1
        # Determine gate BEFORE today's events become part of model history:
        allow,n,hit,net=causal_gate(past)
        todays=[e for e in session_delayed_events(current) if e.continuation_accepted]
        todays_taken=todays if allow else []
        if name in period_events:
            period_events[name].extend(todays)
            gate_trades[name].extend(todays_taken)
        raw=sum(e.delayed_raw_breakout_direction_signed_spy_bps for e in todays_taken)
        net_sum=raw-FRICTION_BPS*len(todays_taken)
        daily_records.append(GateRecord(
            session=day.isoformat(),date_start=current[0].timestamp.isoformat(),
            history_sessions=len(past),
            history_events=n,history_hit_fraction=hit,
            history_net_spy_bps_per_event=net,gate_open=allow,
            new_confirmed_signals=len(todays),taken=len(todays_taken),
            new_raw_signed_spy_bps=round(raw,6),
            new_net_signed_spy_bps=round(net_sum,6),
            session_period=name,
        ))
        past.append(todays)
        current=[]

    for f in frames:
        d=f.timestamp.astimezone(ET).date()
        if old_day is not None and d<old_day:
            raise ValueError("historical market rows not chronological")
        if old_day is not None and d!=old_day:
            flush()
        old_day=d
        current.append(f)
        read_frames+=1
    flush()

    periods={}
    small_groups=[]
    individual=[]
    for name,_,_ in PERIODS:
        events=period_events[name]
        taken=gate_trades[name]
        per_day=[x for x in daily_records if x.session_period==name]
        for e in events:
            returned=e.delayed_raw_breakout_direction_signed_spy_bps
            individual.append({
                "period":name,"session":e.session,"signal_at":e.signal_at,
                "original_direction":e.original_direction,
                "confirmation_at":e.confirmation_at,
                "entry_proxy_at":e.proxy_entry_at,
                "volume_ratio":round(e.original_at_signal_volume_ratio,4),
                "signed30min_trend_bps":round(e.original_30min_trend_signed_bps,4),
                "efficiency_15close":round(e.original_15close_efficiency,4),
                "confirm_extension_bps":round(e.confirm_extension_signed_bps,4),
                "three_minute_change_bps":round(e.confirm_change_3min_signed_bps,4),
                "signed_spy_direction_10min_bps":round(returned,4),
                "correct_direction":returned>0,
                "net_after_2bp_underlying_proxy":round(returned-FRICTION_BPS,4),
                "static_rule_accepted":True,
                "causal_session_gate_accepted":e in taken,
            })
        static=_group_details(events,name)
        gate=_group_details(taken,name+"_prior_session_gate")
        gate_per_day=sum(x.new_net_signed_spy_bps for x in per_day)/len(per_day) if per_day else None
        static_net_per_day=(
            sum(e.delayed_raw_breakout_direction_signed_spy_bps-FRICTION_BPS
                for e in events)/len(per_day)) if per_day else None
        periods[name]={
            "eligible_sessions":len(per_day),
            "static_confirmed":static,
            "prior_session_gate":gate,
            "past_only_gate_open_sessions":sum(x.gate_open for x in per_day),
            "past_only_gate_open_with_signals":sum(x.gate_open and x.new_confirmed_signals>0 for x in per_day),
            "past_only_gate_daily_net_signed_spy_bps_incl_zero":round(gate_per_day,4) if gate_per_day is not None else None,
            "static_rule_daily_net_signed_spy_bps_incl_zero":round(static_net_per_day,4) if static_net_per_day is not None else None,
            "gate_whole_day_block_bootstrap":_daily_bootstrap(per_day,[r.session for r in per_day]),
            "equity_curve":None,"account_drawdown":None,"real_option_pnl":None,
        }
        small_groups.extend(_strata(events,name))
    a=periods["validation"]["static_confirmed"]
    b=periods["previously_studied_diagnostic"]["static_confirmed"]
    p=fisher_exact_two_sided(
        a["correct_spy_direction"],a["wrong_or_flat_spy_direction"],
        b["correct_spy_direction"],b["wrong_or_flat_spy_direction"],
    )
    return {
        "experiment":"audit_of_confirmed_SPY_direction_predictions_and_past_only_regime_gate_v1",
        "frames":read_frames,"sessions":read_sessions,
        "frozen_gate":{
            "lookback_prior_complete_sessions":PRIOR_SESSIONS,
            "minimum_previous_confirmed_opportunities":MIN_PRIOR_CONFIRMED_EVENTS,
            "minimum_previous_direction_hit_fraction":MIN_PRIOR_HIT_RATE,
            "minimum_previous_average_net_spy_bps_strictly_greater_than":MIN_PRIOR_MEAN_NET_BPS,
            "hypothetical_underlying_friction_bp":FRICTION_BPS,
            "gate_computed_before_current_session":True,
        },
        "previously_seen_time_periods_not_untouched_holdout":True,
        "fisher_exact_2sided_10of13_vs_3of7_exploratory_p":round(p,5),
        "periods":periods,
        "per_signal_table":individual,
        "predefined_posthoc_feature_strata_NOT_ADDITIONAL_TRADE_RULES":small_groups,
        "daily_gate_decisions":daily_records,
        "real_options_profitability":None,
        "100pct_weekly_return_evidence":False,
        "new_market_data_purchased":False,
    }


def _csv(path:Path,rows:Sequence[dict])->None:
    if not rows:
        path.write_text("")
        return
    with path.open("w",encoding="utf-8",newline="") as file:
        w=csv.DictWriter(file,fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def main(argv:Sequence[str]|None=None)->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir",type=Path,default=Path("/data/research"))
    parser.add_argument("--output-dir",type=Path,default=Path("/data/research/continuation_regime_gate"))
    args=parser.parse_args(argv)
    paths=_research_files(args.data_dir)
    if not paths:
        print("REGIME GATE BLOCKED: no licensed archived SPY minute closes",flush=True)
        return 2
    results=run_diagnostic(iter_research_directory(args.data_dir))
    dest=args.output_dir
    dest.mkdir(parents=True,exist_ok=True)
    signals=results.pop("per_signal_table")
    strata=results.pop("predefined_posthoc_feature_strata_NOT_ADDITIONAL_TRADE_RULES")
    days=results.pop("daily_gate_decisions")
    _csv(dest/"static_13_and_7_and_other_signals_NOT_OPTIONS_TRADES.csv",signals)
    _csv(dest/"FEATURES_POSTHOC_NOT_VALIDATED_STRATEGIES.csv",strata)
    _csv(dest/"PAST_30_SESSIONS_gate_daily_decisions.csv",[asdict(d) for d in days])
    h=hashlib.sha256()
    for path in paths:
        h.update(path.name.encode())
        h.update(str(path.stat().st_size).encode())
    results["file_count"]=len(paths)
    results["file_metadata_sha256"]=h.hexdigest()
    (dest/"summary.json").write_text(json.dumps(results,indent=2,sort_keys=True))
    print("REGIME GATE DATA COVERAGE: "+json.dumps({
        "sessions":results["sessions"],"frames":results["frames"],
        "archive_count":len(paths),"new_data_cost_usd":0,
        "folder":str(dest)},sort_keys=True),flush=True)
    print("REGIME GATE EXACT HIT DIFFERENCE: "+json.dumps({
        "fisher_two_sided_p_exploratory":results["fisher_exact_2sided_10of13_vs_3of7_exploratory_p"],
        "warning":"20 signals, prior-inspected dates, serial dependence, not proof or new holdout",
    },sort_keys=True),flush=True)
    for period,stats in results["periods"].items():
        print("REGIME GATE PERIOD: "+json.dumps({
            "period":period,"calendar_sessions":stats["eligible_sessions"],
            "original_confirmed":stats["static_confirmed"]["accepted_events"],
            "static_winners":stats["static_confirmed"]["correct_spy_direction"],
            "static_wilson95":stats["static_confirmed"]["wilson95_spy_direction_hit_interval"],
            "static_mean_net_spy_bps":stats["static_confirmed"]["mean_after_2bp_underlying_friction_proxy"],
            "static_mean_net_5bp_proxy":stats["static_confirmed"]["mean_after_5bp_underlying_friction_proxy"],
            "static_loo_minmax":[stats["static_confirmed"]["one_deleted_net_average_min"],
                                 stats["static_confirmed"]["one_deleted_net_average_max"]],
            "gate_open_days":stats["past_only_gate_open_sessions"],
            "gate_taken":stats["prior_session_gate"]["accepted_events"],
            "gate_correct_direction":stats["prior_session_gate"]["correct_spy_direction"],
            "gate_net_spy_bps_per_calendar_session":stats["past_only_gate_daily_net_signed_spy_bps_incl_zero"],
            "gate_bootstrap95":stats["gate_whole_day_block_bootstrap"].get(
                "daily_signed_spy_bps_95pct_interval"),
            "options_account_return":None,
        },sort_keys=True),flush=True)
    for row in signals:
        if row["period"] in ("validation","previously_studied_diagnostic"):
            print("REGIME GATE SIGNAL: "+json.dumps(row,sort_keys=True),flush=True)
    for row in strata:
        if row["period"] in ("validation","previously_studied_diagnostic"):
            print("REGIME GATE POSTHOC FEATURE: "+json.dumps(row,sort_keys=True),flush=True)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
