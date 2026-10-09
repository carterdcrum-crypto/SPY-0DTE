"""Free, research-only delayed-confirmation SPY close breakout router.

This experiment uses existing archived SPY one-minute CLOSE and contemporaneous
volume ratio. It NEVER claims to measure actual opening-range high/low,
transaction VWAP, executable option fills, premium P&L, or equity drawdowns.

All baseline opportunities are the same non-overlapping events published in
free_signal_screen. The first three subsequent completed minute closes are
observed BEFORE a decision; accepted hypothetical entries occur on the NEXT
completed SPY minute close. Same-clock delayed baseline, zero-trade and two
conditional strategies are compared. Every outcome uses 10 minutes of signed
underlying bps, never an options account return.
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

from .burst_research import iter_research_directory, _research_files
from .data import HistoricalFrame
from .free_signal_screen import ET, PERIODS, screen_session, _bps

POLICIES = (
    "delayed_baseline_all",
    "confirmed_trend_continuation",
    "confirmed_failed_break_fade",
    "conditional_router",
)
OBSERVE_MINUTES = 3
HOLD_MINUTES = 10


@dataclass(frozen=True)
class RouterConfig:
    volume_ratio_min: float = 1.25
    aligned_30min_trend_bps_min: float = 8.0
    efficiency_15close_min: float = 0.35
    extension_bps_max: float = 8.0
    snapback_inside_boundary_bps_min: float = 2.0
    fade_requires_weak_prior_trend_bps_max: float = 8.0
    confirm_three_min_change_bps_min: float = 2.0
    underlying_friction_assumption_bps: float = 2.0
    block_sessions: int = 5
    bootstrap_reps: int = 1000
    bootstrap_seed: int = 28157


@dataclass(frozen=True)
class DelayedEvent:
    session: str
    original_direction: str
    signal_at: str
    confirmation_at: str
    proxy_entry_at: str
    proxy_exit_at: str
    signal_spot: float
    previous_15_close_boundary: float
    three_min_confirm_spot: float
    # all causal as of confirmation_at:
    original_at_signal_volume_ratio: float
    original_30min_trend_signed_bps: float
    original_15close_efficiency: float
    confirm_extension_signed_bps: float
    confirm_change_3min_signed_bps: float
    observed_first3_snapback: bool
    continuation_accepted: bool
    failed_break_fade_accepted: bool
    # Only AFTER entry: future outcome diagnostic, NEVER used by selector
    delayed_raw_breakout_direction_signed_spy_bps: float


def _efficiency(spots: Sequence[float], i: int) -> float:
    moves=[abs(spots[j]-spots[j-1]) for j in range(i-14,i+1)]
    total=sum(moves)
    return min(1.,abs(spots[i]-spots[i-15])/total) if total>0 else 0.0


def _continuous(times:Sequence[datetime],first:int,last:int)->bool:
    return all((times[j+1]-times[j]).total_seconds()==60
               for j in range(first,last))


def session_delayed_events(frames:Sequence[HistoricalFrame],
                           cfg:RouterConfig=RouterConfig())->list[DelayedEvent]:
    """Return SAME original baseline events, now observed 3m later.

    There can be no overlap within the upstream baseline's event stream.
    In this delayed experiment, baseline signal admission is unchanged.
    """
    if not frames:
        return []
    timestamps=[f.timestamp for f in frames]
    spots=[f.market.spot for f in frames]
    ratios=[f.market.volume_ratio for f in frames]
    lookup={stamp+timedelta(minutes=1):i for i,stamp in enumerate(timestamps)}
    original=[e for e in screen_session(frames)
              if e.family=="close_breakout_baseline"]
    found=[]
    for original_event in original:
        i=lookup[datetime.fromisoformat(original_event.signal_timestamp)]
        confirm=i+OBSERVE_MINUTES
        entry=confirm+1
        exit_idx=entry+HOLD_MINUTES
        if i<31 or exit_idx>=len(frames):
            continue
        # We cannot compress missing market minutes or tolerate disconnected
        # causal features: prior 30 mins, confirmation, entry and hold required.
        if not _continuous(timestamps,i-30,exit_idx):
            continue
        if any(not math.isfinite(v) or v<=0 for v in
               (spots[i],spots[confirm],spots[entry],spots[exit_idx])):
            continue
        side=1 if original_event.direction=="call" else -1
        boundary=(max(spots[i-15:i]) if side==1 else min(spots[i-15:i]))
        trend30=side*_bps(spots[i],spots[i-30])
        efficiency=_efficiency(spots,i)
        # The confirmation clock is CLOSE of 3rd minute after signal;
        # all below are publicly knowable before the T+4 next-close proxy.
        ext=side*_bps(spots[confirm],boundary)
        change=side*_bps(spots[confirm],spots[i])
        snapback=any(side*_bps(spots[t],boundary)<0
                     for t in range(i+1,confirm+1))
        vol=ratios[i]
        # Predeclared conservative continuation: clean 3-minute follow
        # through, aligned prior regime and an earlier volume signal.
        cont=(math.isfinite(vol) and vol>=cfg.volume_ratio_min
              and trend30>=cfg.aligned_30min_trend_bps_min
              and efficiency>=cfg.efficiency_15close_min
              and 0<=ext<=cfg.extension_bps_max
              and change>=0
              and not snapback)
        # Failed breakout *only after evidence is actually observed*.
        # Fade a clean rejection >=2bp back inside the old boundary,
        # but avoid fading strongly trending 30min prior tapes.
        fade=(ext<=-cfg.snapback_inside_boundary_bps_min
              and change<=-cfg.confirm_three_min_change_bps_min
              and trend30<cfg.fade_requires_weak_prior_trend_bps_max)
        forecast=side*_bps(spots[exit_idx],spots[entry])
        found.append(DelayedEvent(
            session=original_event.session,
            original_direction=original_event.direction,
            signal_at=original_event.signal_timestamp,
            confirmation_at=(timestamps[confirm]+timedelta(minutes=1)).isoformat(),
            proxy_entry_at=(timestamps[entry]+timedelta(minutes=1)).isoformat(),
            proxy_exit_at=(timestamps[exit_idx]+timedelta(minutes=1)).isoformat(),
            signal_spot=spots[i],
            previous_15_close_boundary=boundary,
            three_min_confirm_spot=spots[confirm],
            original_at_signal_volume_ratio=vol,
            original_30min_trend_signed_bps=trend30,
            original_15close_efficiency=efficiency,
            confirm_extension_signed_bps=ext,
            confirm_change_3min_signed_bps=change,
            observed_first3_snapback=snapback,
            continuation_accepted=cont,
            failed_break_fade_accepted=fade,
            delayed_raw_breakout_direction_signed_spy_bps=forecast,
        ))
    return found


def decision(event:DelayedEvent,policy:str)->int:
    """Return +1 breakout side, -1 reversal, 0 abstain. Never read outcomes."""
    if policy not in POLICIES:
        raise ValueError(f"Unknown fixed strategy {policy}")
    if policy=="delayed_baseline_all":
        return 1
    if policy=="confirmed_trend_continuation":
        return int(event.continuation_accepted)
    if policy=="confirmed_failed_break_fade":
        return -int(event.failed_break_fade_accepted)
    if event.continuation_accepted:
        return 1
    if event.failed_break_fade_accepted:
        return -1
    return 0


def _daily_bs(selected:Sequence[tuple[DelayedEvent,int]],
              baseline:Sequence[DelayedEvent],days:Sequence[date],
              cfg:RouterConfig)->dict:
    """Paired circular five-session block bootstrap on day totals.

    Comparison includes all sessions with zero returns for abstention,
    and uses the SAME clock and SAME candidates for baseline and filters.
    This is diagnostic of archived time-series consistency, not prospective
    option account returns. Positive delta vs a negative baseline is NOT edge.
    """
    if not days:
        return {"available":False}
    p=defaultdict(float)
    b=defaultdict(float)
    for event,side in selected:
        p[event.session]+=side*event.delayed_raw_breakout_direction_signed_spy_bps-cfg.underlying_friction_assumption_bps
    for event in baseline:
        b[event.session]+=event.delayed_raw_breakout_direction_signed_spy_bps-cfg.underlying_friction_assumption_bps
    ours=[p[d.isoformat()] for d in days]
    control=[b[d.isoformat()] for d in days]
    n=len(days)
    block=min(cfg.block_sessions,n)
    randomizer=random.Random(cfg.bootstrap_seed)
    simulated=[]
    paired=[]
    for _ in range(cfg.bootstrap_reps):
        indices=[]
        while len(indices)<n:
            start=randomizer.randrange(n)
            indices.extend((start+j)%n for j in range(block))
        indices=indices[:n]
        simulated.append(sum(ours[i] for i in indices)/n)
        paired.append(sum(ours[i]-control[i] for i in indices)/n)
    def ci(rows:Sequence[float])->list[float]:
        ordered=sorted(rows)
        return [round(ordered[round(.025*(len(ordered)-1))],4),
                round(ordered[round(.975*(len(ordered)-1))],4)]
    return {"n_sessions":n,"block_sessions":block,
            "resamples":cfg.bootstrap_reps,
            "daily_net_spy_bps_95pct_ci":ci(simulated),
            "paired_vs_delayed_baseline_spy_bps_95pct_ci":ci(paired)}


def _summarize(original:Sequence[DelayedEvent],policy:str,
               days:Sequence[date],cfg:RouterConfig)->dict:
    selected=[(ev,decision(ev,policy)) for ev in original]
    accepted=[(ev,s) for ev,s in selected if s]
    raw=[s*ev.delayed_raw_breakout_direction_signed_spy_bps for ev,s in accepted]
    after=[x-cfg.underlying_friction_assumption_bps for x in raw]
    daily=defaultdict(float)
    weekly=defaultdict(float)
    for (ev,_),val in zip(accepted,after):
        daily[ev.session]+=val
        iso=date.fromisoformat(ev.session).isocalendar()
        weekly[(iso.year,iso.week)]+=val
    allweeks={(d.isocalendar().year,d.isocalendar().week) for d in days}
    seq=[daily[d.isoformat()] for d in days]
    sums=[weekly[key] for key in sorted(allweeks)]
    cumulative=0.
    peak=0.
    worst_fall=0.
    for value in seq:
        cumulative+=value
        peak=max(peak,cumulative)
        worst_fall=max(worst_fall,peak-cumulative)
    flips=[(ev,s) for ev,s in accepted if s<0]
    same_clock_baseline_for_selected=[
        ev.delayed_raw_breakout_direction_signed_spy_bps for ev,_ in accepted
    ]
    return {
        "eligible_days":len(days),
        "original_baseline_opportunities":len(original),
        "accepted":len(accepted),"abstained":len(original)-len(accepted),
        "reversed":len(flips),"continued":len(accepted)-len(flips),
        "signal_days":len({ev.session for ev,_ in accepted}),
        "direction_hit_rate":(sum(x>0 for x in raw)/len(raw) if raw else None),
        "mean_raw_signed_spy_bps":round(statistics.fmean(raw),4) if raw else None,
        "mean_after_assumed_2bp_spy_bps":round(statistics.fmean(after),4) if after else None,
        "matched_baseline_on_selected_timestamps_spy_bps":(
            round(statistics.fmean(same_clock_baseline_for_selected),4)
            if same_clock_baseline_for_selected else None),
        "net_signed_spy_bps_per_calendar_session_including_no_trade":(
            round(sum(after)/len(days),4) if days else None),
        "total_signed_spy_bps_after_friction":round(sum(after),4),
        "worst_day_signed_spy_bps":round(min(seq),4) if seq else None,
        "worst_week_signed_spy_bps":round(min(sums),4) if sums else None,
        "positive_week_fraction":(
            sum(v>0 for v in sums)/len(sums) if sums else None),
        "max_peak_to_trough_additive_spy_bps_not_account_drawdown":round(worst_fall,4),
        "bootstrap":_daily_bs(accepted,original,days,cfg),
        "actual_options_pnl":None,"account_return":None,
        "account_max_drawdown":None,
        "repeatable_options_edge_proven":False,
    }


def run_research(frames:Iterable[HistoricalFrame],
                 cfg:RouterConfig=RouterConfig())->dict:
    partitions={label:{"days":[],"events":[]} for label,_,_ in PERIODS}
    observed_days=0
    total_frames=0
    current=[]
    previous=None
    def flush()->None:
        nonlocal observed_days,current
        if not current:
            return
        observed_days+=1
        d=current[0].timestamp.astimezone(ET).date()
        events=session_delayed_events(current,cfg)
        for label,start,end in PERIODS:
            if start<=d<=end:
                partitions[label]["days"].append(d)
                partitions[label]["events"].extend(events)
                break
        current=[]
    for frame in frames:
        d=frame.timestamp.astimezone(ET).date()
        if previous is not None and d<previous:
            raise ValueError("SPY input must be in chronological session order")
        if previous is not None and d!=previous:
            flush()
        previous=d
        current.append(frame)
        total_frames+=1
    flush()
    reports={}
    all_events=[]
    actions=[]
    for period,data in partitions.items():
        days=data["days"]
        events=data["events"]
        reports[period]={}
        for name in POLICIES:
            stats=_summarize(events,name,days,cfg)
            reports[period][name]=stats
            for event in events:
                action=decision(event,name)
                if action:
                    actions.append({
                        "period":period,"strategy":name,
                        "session":event.session,
                        "signal_at":event.signal_at,
                        "confirmation_at":event.confirmation_at,
                        "proxy_entry_at":event.proxy_entry_at,
                        "proxy_exit_at":event.proxy_exit_at,
                        "direction":(event.original_direction if action==1 else
                            "put" if event.original_direction=="call" else "call"),
                        "is_reversal":action==-1,
                        "signed_spy_10min_bps":round(
                            action*event.delayed_raw_breakout_direction_signed_spy_bps,6),
                        "signed_minus_2bp_underlying_proxy":round(
                            action*event.delayed_raw_breakout_direction_signed_spy_bps-
                            cfg.underlying_friction_assumption_bps,6),
                    })
        all_events.extend(events)
    return {
        "experiment":"causal_delayed_spy_breakout_confirmation_router_v1",
        "frames_read":total_frames,
        "sessions_read":observed_days,
        "fixed_strategies":POLICIES,
        "policy":asdict(cfg),
        "periods":reports,
        "events":tuple(all_events),
        "actions":tuple(actions),
        "new_market_data_purchased":False,
        "no_untouched_holdout_available":True,
        "requires_future_real_quotes_before_any_0dte_pnl_claim":True,
    }


def _write_csv(path:Path,records:Sequence[dict])->None:
    if not records:
        path.write_text("")
        return
    with path.open("w",newline="",encoding="utf-8") as f:
        writer=csv.DictWriter(f,fieldnames=tuple(records[0]))
        writer.writeheader()
        writer.writerows(records)


def main(argv:Sequence[str]|None=None)->int:
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--data-dir",type=Path,default=Path("/data/research"))
    cli.add_argument("--output-dir",type=Path,
                     default=Path("/data/research/causal_breakout_router"))
    args=cli.parse_args(argv)
    files=_research_files(args.data_dir)
    if not files:
        print("CAUSAL ROUTER BLOCKED: no existing licensed minute data",flush=True)
        return 2
    result=run_research(iter_research_directory(args.data_dir))
    args.output_dir.mkdir(parents=True,exist_ok=True)
    events=result.pop("events")
    actions=result.pop("actions")
    _write_csv(args.output_dir/"baseline_delayed_confirmation_opportunities.csv",
               [asdict(ev) for ev in events])
    _write_csv(args.output_dir/"accepted_SPY_direction_proxy_NOT_option_trades.csv",
               list(actions))
    file_fingerprint=hashlib.sha256()
    for file in files:
        file_fingerprint.update(file.name.encode())
        file_fingerprint.update(str(file.stat().st_size).encode())
    result["source_file_count"]=len(files)
    result["source_file_metadata_sha256"]=file_fingerprint.hexdigest()
    result["classification"]="CONDITIONAL FORECAST SCREEN, NOT EXECUTABLE OPTIONS PROFIT OR ACCOUNT RETURN"
    (args.output_dir/"report.json").write_text(json.dumps(result,indent=2,sort_keys=True))
    print("CAUSAL ROUTER COVERAGE: "+json.dumps({
        "sessions":result["sessions_read"],"market_frames":result["frames_read"],
        "baseline_eligible_opportunities":len(events),
        "stored_actions":len(actions),"data_purchase_usd":0,
        "path":str(args.output_dir)},sort_keys=True),flush=True)
    for period,policies in result["periods"].items():
        for name,stats in policies.items():
            print("CAUSAL ROUTER RESULT: "+json.dumps({
                "period":period,"strategy":name,
                "opportunities":stats["original_baseline_opportunities"],
                "accepted":stats["accepted"],"reversals":stats["reversed"],
                "direction_accuracy":stats["direction_hit_rate"],
                "raw_spy_bps_per_event":stats["mean_raw_signed_spy_bps"],
                "mean_after_2bp_proxy":stats["mean_after_assumed_2bp_spy_bps"],
                "net_spy_bps_per_session":stats["net_signed_spy_bps_per_calendar_session_including_no_trade"],
                "bootstrap_95pct_daily_spy_bps":stats["bootstrap"].get("daily_net_spy_bps_95pct_ci"),
                "paired_baseline_change_95pct":stats["bootstrap"].get("paired_vs_delayed_baseline_spy_bps_95pct_ci"),
                "max_additive_spy_bps_decline_NOT_ACCOUNT_DD":stats["max_peak_to_trough_additive_spy_bps_not_account_drawdown"],
                "actual_option_profit":None,
            },sort_keys=True),flush=True)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
