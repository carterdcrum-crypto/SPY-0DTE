"""Research-only breakout failure analysis on previously licensed SPY minute CLOSE data.

No true high/low ORB, transaction VWAP, tick-order flow, options fill,
account return, live execution, or data purchase. Uses EXACTLY the baseline
non-overlapping close breakouts from free_signal_screen, with predeclared
causal filters. Outcome diagnostics can use future observations but filter
decisions cannot. All displayed returns are SPY signed BASIS POINTS (bps).
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
from datetime import date, datetime, timedelta, time
from pathlib import Path
from typing import Iterable, Sequence

from .burst_research import iter_research_directory, _research_files
from .data import HistoricalFrame
from .free_signal_screen import ET, PERIODS, ScreenConfig, SignalRecord, screen_session, _bps

FILTERS = (
    "baseline_all",
    "volume_plus_five_minute_trend",
    "thirty_minute_trend_plus_efficiency",
    "low_extension_plus_volume",
    "combined_conservative",
)
EARLY_WINDOW_MINUTES = 3


@dataclass(frozen=True)
class FilterPolicy:
    min_volume_ratio: float = 1.25
    min_signed_five_minute_bps: float = 3.0
    min_signed_thirty_minute_bps: float = 8.0
    min_efficiency_ratio: float = 0.35
    max_breakout_extension_bps: float = 8.0
    early_failure_bps: float = 3.0
    proxy_friction_bps: float = 2.0
    moving_bootstrap_block_days: int = 5
    bootstrap_reps: int = 1000
    bootstrap_seed: int = 8021


@dataclass(frozen=True)
class BreakoutObservation:
    session: str
    signal_at: str
    proxy_entry_at: str
    proxy_exit_at: str
    direction: str
    signal_spot: float
    prior_fifteen_close_boundary: float
    extension_bps: float
    signal_volume_ratio: float
    signed_five_minute_trend_bps: float
    signed_thirty_minute_trend_bps: float
    efficiency_last_fifteen: float
    pre_signal_history_complete: bool
    time_bucket: str
    # Future-only diagnostics. Forbidden in select_filter.
    signed_next_ten_minute_spy_bps: float
    worst_close_only_adverse_bps: float
    best_close_only_favorable_bps: float
    first_three_minute_adverse_bps: float
    snap_back_inside_range_first_three_minutes: bool
    net_negative_after_2bp_proxy_friction: bool


def _continuous_prior(times: Sequence[datetime], i: int) -> bool:
    if i < 31:
        return False
    return all((times[j+1]-times[j]).total_seconds() == 60
               for j in range(i-30, i))


def _time_bucket(now: datetime) -> str:
    clock = now.astimezone(ET).time()
    if clock < time(11):
        return "open_1000_to_1100"
    if clock < time(13,30):
        return "midday_1100_to_1330"
    return "late_1330_to_1525"


def _efficiency(closes: Sequence[float], i: int, n: int = 15) -> float:
    denominator=sum(abs(closes[j]-closes[j-1])
                    for j in range(i-n+1,i+1))
    if denominator<=0:
        return 0.0
    return min(1.,abs(closes[i]-closes[i-n])/denominator)


def annotate_baseline_session(
    frames: Sequence[HistoricalFrame], *,
    screen_config: ScreenConfig = ScreenConfig(),
    policy: FilterPolicy = FilterPolicy(),
) -> list[BreakoutObservation]:
    """Annotate *unchanged* baseline close breakouts, no incremental peeking."""
    events=[r for r in screen_session(frames,screen_config)
            if r.family=="close_breakout_baseline"]
    times=[f.timestamp for f in frames]
    closes=[f.market.spot for f in frames]
    ratio=[f.market.volume_ratio for f in frames]
    at={t+timedelta(minutes=1):i for i,t in enumerate(times)}
    results=[]
    for r in events:
        i=at[datetime.fromisoformat(r.signal_timestamp)]
        j=i+1
        k=j+screen_config.hold_minutes
        side=1 if r.direction=="call" else -1
        boundary=(max(closes[i-15:i]) if side==1
                  else min(closes[i-15:i]))
        extension=side*_bps(closes[i],boundary)
        history_ok=_continuous_prior(times,i)
        five=side*_bps(closes[i],closes[i-5]) if history_ok else float("nan")
        thirty=side*_bps(closes[i],closes[i-30]) if history_ok else float("nan")
        efficiency=_efficiency(closes,i) if history_ok else float("nan")
        future_signed=[side*_bps(closes[x],closes[j]) for x in range(j+1,k+1)]
        first3=future_signed[:min(EARLY_WINDOW_MINUTES,len(future_signed))]
        snap_back=any(side*_bps(closes[x],boundary)<0
                      for x in range(j+1,min(j+EARLY_WINDOW_MINUTES,k)+1))
        results.append(BreakoutObservation(
            session=r.session,signal_at=r.signal_timestamp,
            proxy_entry_at=r.proxy_entry_timestamp,proxy_exit_at=r.proxy_exit_timestamp,
            direction=r.direction,signal_spot=r.signal_spot,
            prior_fifteen_close_boundary=boundary,
            extension_bps=extension,signal_volume_ratio=ratio[i],
            signed_five_minute_trend_bps=five,
            signed_thirty_minute_trend_bps=thirty,
            efficiency_last_fifteen=efficiency,
            pre_signal_history_complete=history_ok,
            time_bucket=_time_bucket(datetime.fromisoformat(r.signal_timestamp)),
            signed_next_ten_minute_spy_bps=r.signed_spy_move_bps,
            worst_close_only_adverse_bps=min((0.0,*future_signed)),
            best_close_only_favorable_bps=max((0.0,*future_signed)),
            first_three_minute_adverse_bps=min((0.0,*first3)),
            snap_back_inside_range_first_three_minutes=snap_back,
            net_negative_after_2bp_proxy_friction=(
                r.signed_spy_move_bps<=policy.proxy_friction_bps),
        ))
    return results


def select_filter(o:BreakoutObservation,name:str,policy:FilterPolicy=FilterPolicy())->bool:
    """Causal filter: NEVER inspect future outcome fields of observation."""
    if name not in FILTERS:
        raise ValueError(f"unknown preregistered filter {name}")
    if name=="baseline_all":
        return True
    if not o.pre_signal_history_complete:
        return False
    vol=math.isfinite(o.signal_volume_ratio) and o.signal_volume_ratio>=policy.min_volume_ratio
    five=math.isfinite(o.signed_five_minute_trend_bps) and (
        o.signed_five_minute_trend_bps>=policy.min_signed_five_minute_bps)
    thirty=math.isfinite(o.signed_thirty_minute_trend_bps) and (
        o.signed_thirty_minute_trend_bps>=policy.min_signed_thirty_minute_bps)
    efficiency=math.isfinite(o.efficiency_last_fifteen) and (
        o.efficiency_last_fifteen>=policy.min_efficiency_ratio)
    low_ext=math.isfinite(o.extension_bps) and (
        0<=o.extension_bps<=policy.max_breakout_extension_bps)
    if name=="volume_plus_five_minute_trend":
        return vol and five
    if name=="thirty_minute_trend_plus_efficiency":
        return thirty and efficiency
    if name=="low_extension_plus_volume":
        return low_ext and vol
    return vol and five and thirty and efficiency and low_ext


def _session_bs(
    filtered:Sequence[BreakoutObservation],
    baseline:Sequence[BreakoutObservation],
    days:Sequence[date],
    policy:FilterPolicy,
)->dict:
    """Moving nonoverlapping five-day block *index* bootstrap, preserving
    dependence across adjacent sessions within each sampled block.

    Pair filters with baseline using identical sampled days, including zero-
    trade sessions; return difference is filter minus baseline after proxy
    friction (NOT financial returns). Circular block boundaries approximate
    temporal dependence and cannot create truly fresh regimes.
    """
    if not days:
        return {"available":False}
    f=defaultdict(float)
    b=defaultdict(float)
    for o in filtered:
        f[o.session]+=o.signed_next_ten_minute_spy_bps-policy.proxy_friction_bps
    for o in baseline:
        b[o.session]+=o.signed_next_ten_minute_spy_bps-policy.proxy_friction_bps
    f_day=[f[str(d)] for d in days]
    b_day=[b[str(d)] for d in days]
    n=len(days)
    block=min(n,policy.moving_bootstrap_block_days)
    rng=random.Random(policy.bootstrap_seed)
    vals=[]
    differences=[]
    for _ in range(policy.bootstrap_reps):
        idx=[]
        while len(idx)<n:
            start=rng.randrange(n)
            idx.extend((start+j)%n for j in range(block))
        idx=idx[:n]
        vals.append(sum(f_day[i] for i in idx)/n)
        differences.append(sum(f_day[i]-b_day[i] for i in idx)/n)
    def ci(xs:Sequence[float])->list[float]:
        seq=sorted(xs)
        return [round(seq[round(.025*(len(seq)-1))],4),
                round(seq[round(.975*(len(seq)-1))],4)]
    return {
        "sample_sessions":n,
        "replicates":policy.bootstrap_reps,
        "circular_block_length_sessions":block,
        "friction_adjusted_signed_bps_per_day_95pct_ci":ci(vals),
        "paired_change_vs_baseline_bps_per_day_95pct_ci":ci(differences),
        "limitations":"Same previously studied archive; 5-session circular blocks cannot prove independent market-regime generalization; underlying SPY proxies are NOT option returns.",
    }


def _metrics(
    selected:Sequence[BreakoutObservation],
    baseline:Sequence[BreakoutObservation],
    days:Sequence[date],
    policy:FilterPolicy,
)->dict:
    observed=[o.signed_next_ten_minute_spy_bps for o in selected]
    after=[r-policy.proxy_friction_bps for r in observed]
    daily=defaultdict(float)
    weekly=defaultdict(float)
    week_sessions=set()
    for day in days:
        cal=day.isocalendar()
        week_sessions.add((cal.year,cal.week))
    for o,r in zip(selected,after):
        daily[o.session]+=r
        iso=date.fromisoformat(o.session).isocalendar()
        weekly[(iso.year,iso.week)]+=r
    weeks=[weekly[k] for k in sorted(week_sessions)]
    per_day=[daily[str(d)] for d in days]
    # Additive signed basis-points series is an underlying-price diagnostic.
    # It is NOT compounded account equity, profit, or executable drawdown.
    peak=0.0
    wealth=0.0
    max_additive_dd=0.0
    for val in per_day:
        wealth+=val
        peak=max(peak,wealth)
        max_additive_dd=max(max_additive_dd,peak-wealth)
    return {
        "baseline_opportunities":len(baseline),
        "accepted":len(selected),
        "skipped":len(baseline)-len(selected),
        "sessions_in_period":len(days),
        "signal_sessions":len({o.session for o in selected}),
        "hit_fraction":sum(r>0 for r in observed)/len(observed) if observed else None,
        "after_2bp_proxy_hit_fraction":sum(r>policy.proxy_friction_bps for r in observed)/len(observed) if observed else None,
        "mean_spy_signed_bps_per_event":round(statistics.fmean(observed),4) if observed else None,
        "mean_spy_signed_bps_per_event_after_2bp_proxy":round(statistics.fmean(after),4) if after else None,
        "total_spy_signed_bps_after_2bp_proxy":round(sum(after),4),
        "mean_spy_signed_bps_per_session_after_2bp_proxy":round(
            sum(after)/len(days),4) if days else None,
        "worst_session_signed_bps_after_2bp_proxy":round(min(per_day),4) if per_day else None,
        "max_cumulative_signed_bps_decline_NOT_account_drawdown":round(max_additive_dd,4),
        "positive_week_fraction_spy_bps_NOT_account_returns":(
            sum(v>0 for v in weeks)/len(weeks) if weeks else None),
        "worst_week_signed_spy_bps_after_2bp_proxy":round(min(weeks),4) if weeks else None,
        "failed_direction_fraction":sum(r<=0 for r in observed)/len(observed) if observed else None,
        "snap_back_first_3_minutes_fraction":(
            sum(o.snap_back_inside_range_first_three_minutes for o in selected)/len(selected)
            if selected else None),
        "early_3minute_adverse_3bps_fraction":(
            sum(o.first_three_minute_adverse_bps<=-policy.early_failure_bps for o in selected)/len(selected)
            if selected else None),
        "mean_close_only_worst_adverse_spy_bps":(
            round(statistics.fmean(o.worst_close_only_adverse_bps for o in selected),4)
            if selected else None),
        "day_block_bootstrap":_session_bs(selected,baseline,days,policy),
        "options_trades":None,
        "account_return":None,
        "account_max_drawdown":None,
        "hypothesis_100pct_weekly_proven":False,
    }


def _strata(rows:Sequence[BreakoutObservation],period:str,policy:FilterPolicy)->list[dict]:
    """Post-hoc mechanism diagnostics, NOT a strategy/tuning selection."""
    grouped=defaultdict(list)
    for o in rows:
        labels={
            "time_of_day":o.time_bucket,
            "volume_ratio":"high_1.25_or_more" if o.signal_volume_ratio>=policy.min_volume_ratio else "low_below_1.25",
            "breakout_extension":"far_above_8bp" if o.extension_bps>policy.max_breakout_extension_bps else "near_upto_8bp",
            "thirty_min_trend_alignment":("aligned_8bp_plus" if
                o.signed_thirty_minute_trend_bps>=policy.min_signed_thirty_minute_bps
                else "not_aligned"),
            "efficiency":("efficient_0.35_plus" if
                o.efficiency_last_fifteen>=policy.min_efficiency_ratio
                else "choppy_below_0.35"),
            "direction":o.direction,
        }
        for factor,label in labels.items():
            grouped[(factor,label)].append(o)
    output=[]
    for (factor,label),group in sorted(grouped.items()):
        moves=[o.signed_next_ten_minute_spy_bps for o in group]
        output.append({
            "period":period,"factor":factor,"bucket":label,"events":len(group),
            "small_group_fewer_than_20":len(group)<20,
            "mean_signed_spy_bps":round(statistics.fmean(moves),4),
            "mean_signed_spy_bps_minus_2_proxy":round(statistics.fmean(moves)-2,4),
            "wrong_direction_fraction":sum(x<=0 for x in moves)/len(moves),
            "3min_snapback_fraction":sum(o.snap_back_inside_range_first_three_minutes for o in group)/len(group),
            "3min_adverse_over_3bps_fraction":sum(o.first_three_minute_adverse_bps<=-policy.early_failure_bps for o in group)/len(group),
        })
    return output


def run_audit(
    frames:Iterable[HistoricalFrame],
    *,
    policy:FilterPolicy=FilterPolicy(),
    screen_cfg:ScreenConfig=ScreenConfig(),
)->dict:
    partition={label:{"days":[],"rows":[]} for label,_,_ in PERIODS}
    sessions_seen=0
    source_frames=0
    day_now=None
    current:list[HistoricalFrame]=[]

    def flush()->None:
        nonlocal sessions_seen,current
        if not current:
            return
        day=current[0].timestamp.astimezone(ET).date()
        sessions_seen+=1
        diagnosed=annotate_baseline_session(current,screen_config=screen_cfg,policy=policy)
        for label,start,stop in PERIODS:
            if start<=day<=stop:
                partition[label]["days"].append(day)
                partition[label]["rows"].extend(diagnosed)
                break
        current=[]
    for frame in frames:
        day=frame.timestamp.astimezone(ET).date()
        if day_now is not None and day<day_now:
            raise ValueError("market frames out of chronological order")
        if day_now!=day:
            flush()
            day_now=day
        current.append(frame)
        source_frames+=1
    flush()
    metrics={}
    strata=[]
    records=[]
    for period,data in partition.items():
        days=data["days"]
        baseline=data["rows"]
        metrics[period]={}
        for name in FILTERS:
            selected=[o for o in baseline if select_filter(o,name,policy)]
            metrics[period][name]=_metrics(selected,baseline,days,policy)
        strata.extend(_strata(baseline,period,policy))
        records.extend(baseline)
    return {
        "experiment":"free_breakout_failure_audit_v1",
        "classification":"CAUSAL SPY CLOSE PRICE PROXY, NOT OPTIONS BACKTEST",
        "source_sessions":sessions_seen,
        "source_frames":source_frames,
        "predeclared_filters":list(FILTERS),
        "policy":asdict(policy),
        "screen_config":asdict(screen_cfg),
        "partitions":metrics,
        "all_opportunity_rows":tuple(records),
        "posthoc_mechanism_diagnostics":strata,
        "live_trading_modified":False,
        "new_market_data_purchased":False,
        "no_holdout_claim":True,
    }


def _csv(path:Path,rows:Sequence[dict])->None:
    with path.open("w",encoding="utf-8",newline="") as handle:
        if not rows:
            handle.write("")
            return
        writer=csv.DictWriter(handle,fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main(argv:Sequence[str]|None=None)->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir",type=Path,default=Path("/data/research"))
    parser.add_argument("--output-dir",type=Path,
                        default=Path("/data/research/breakout_failure_audit"))
    args=parser.parse_args(argv)
    files=_research_files(args.data_dir)
    if not files:
        print("BREAKOUT FAILURE BLOCKED: no licensed prior SPY minute snapshots",flush=True)
        return 2
    fingerprint=hashlib.sha256()
    for path in files:
        fingerprint.update(path.name.encode())
        fingerprint.update(str(path.stat().st_size).encode())
    results=run_audit(iter_research_directory(args.data_dir))
    args.output_dir.mkdir(parents=True,exist_ok=True)
    full=results.pop("all_opportunity_rows")
    strata=results.pop("posthoc_mechanism_diagnostics")
    results["dataset_file_metadata_fingerprint_sha256"]=fingerprint.hexdigest()
    results["source_file_count"]=len(files)
    _csv(args.output_dir/"all_close_breakout_opportunities.csv",
         [asdict(row) for row in full])
    _csv(args.output_dir/"breakout_failure_strata.csv",strata)
    summaries=[]
    for period,policies in results["partitions"].items():
        for name,stats in policies.items():
            row={"period":period,"filter":name,**{k:v for k,v in stats.items()
                    if k!="day_block_bootstrap"}}
            row["paired_bootstrap_interval"]=stats["day_block_bootstrap"].get(
                "paired_change_vs_baseline_bps_per_day_95pct_ci")
            summaries.append(row)
            print("BREAKOUT FAILURE RESULT: "+json.dumps({
                "period":period,"filter":name,
                "opportunities":stats["baseline_opportunities"],
                "accepted":stats["accepted"],
                "hit":stats["hit_fraction"],
                "mean_raw_bps":stats["mean_spy_signed_bps_per_event"],
                "mean_after_2bp_proxy":stats["mean_spy_signed_bps_per_event_after_2bp_proxy"],
                "daily_after_2bp_proxy":stats["mean_spy_signed_bps_per_session_after_2bp_proxy"],
                "max_cumulative_decline_bps_not_equity":stats["max_cumulative_signed_bps_decline_NOT_account_drawdown"],
                "paired_bootstrap_delta_vs_baseline_ci":row["paired_bootstrap_interval"],
                "options_account_return":None,
            },sort_keys=True),flush=True)
    _csv(args.output_dir/"breakout_filter_comparisons.csv",summaries)
    (args.output_dir/"audit_summary.json").write_text(
        json.dumps(results,indent=2,sort_keys=True),encoding="utf-8")
    print("BREAKOUT FAILURE AUDIT COMPLETE: "+json.dumps({
        "sessions":results["source_sessions"],"frames":results["source_frames"],
        "candidates":len(FILTERS),"cost_new_usd":0,
        "output":str(args.output_dir),
    },sort_keys=True),flush=True)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
