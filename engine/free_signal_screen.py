"""Zero-new-data-cost SPY intraday signal screening; NOT an options backtest.

Research uses only observed same-day SPY 1-minute CLOSE and lagged
volume_ratio from already licensed canonical minute snapshots. Original
OHLCV high/low and transaction-level VWAP were discarded by normalization:
therefore never relabel this as a true ORB/VWAP or executable options P&L.

Signals are decided AFTER a completed bar; the proxy entry is the CLOSE
of the NEXT completed minute, and exit is 10 more completed minutes later.
Proxy entry/exit are NOT assumed executable (real option bid/ask feed absent).
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
from typing import Iterable, Literal, Sequence
from zoneinfo import ZoneInfo

from .burst_research import iter_research_directory
from .data import HistoricalFrame

ET = ZoneInfo("America/New_York")
FAMILIES: tuple[str,...] = (
    "close_breakout_baseline",
    "close_breakout_volume",
    "rolling_mean_reversal",
    "trend_pullback_reclaim",
)
PERIODS: tuple[tuple[str,date,date], ...] = (
    ("development",date(2026,4,1),date(2026,7,31)),
    ("validation",date(2026,8,3),date(2026,9,4)),
    # Period already inspected in other research: NOT an untouched holdout.
    ("previously_studied_diagnostic",date(2026,9,8),date(2026,10,6)),
)


@dataclass(frozen=True)
class ScreenConfig:
    hold_minutes: int = 10
    breakout_prior_closes: int = 15
    min_volume_ratio: float = 1.25
    min_5min_trend_bps: float = 3.0
    min_30min_trend_bps: float = 8.0
    mean_reversion_deviation_bps: float = 7.0
    maximum_signal_gap_minutes: int = 1
    bootstrap_seed: int = 4378
    bootstrap_reps: int = 1000


@dataclass(frozen=True)
class SignalRecord:
    session: str
    family: str
    direction: str
    signal_timestamp: str
    proxy_entry_timestamp: str
    proxy_exit_timestamp: str
    signal_spot: float
    entry_spot_proxy: float
    exit_spot_proxy: float
    signed_spy_move_bps: float
    correct_direction: bool


def _bps(current: float, past: float) -> float:
    return (current / past - 1.0) * 10000 if past > 0 else 0.0


def _family_signals(closes: Sequence[float], volumes: Sequence[float],
                    cfg: ScreenConfig) -> dict[str,str]:
    """Deterministic decision on *historical up-to-now* 1m close observations."""
    x=closes[-1]
    prev=closes[-2]
    out:dict[str,str]={}
    look=cfg.breakout_prior_closes
    if len(closes)>=max(32,look+2):
        past=closes[-look-1:-1]
        if x>max(past) and prev<=max(closes[-look-2:-2]):
            out["close_breakout_baseline"]="call"
        elif x<min(past) and prev>=min(closes[-look-2:-2]):
            out["close_breakout_baseline"]="put"
        if "close_breakout_baseline" in out:
            side=out["close_breakout_baseline"]
            trend=_bps(x,closes[-6])
            valid_trend=trend>=cfg.min_5min_trend_bps if side=="call" else trend<=-cfg.min_5min_trend_bps
            if valid_trend and volumes[-1]>=cfg.min_volume_ratio:
                out["close_breakout_volume"]=side

        last20=closes[-21:-1]
        mean20=statistics.fmean(last20)
        deviation=_bps(x,mean20)
        prev_deviation=_bps(prev,statistics.fmean(closes[-22:-2]))
        # Sign reverses direction on expansion away from the last 20
        # CLOSES; this is NOT volume-weighted VWAP and no bar lows/highs.
        threshold=cfg.mean_reversion_deviation_bps
        if deviation>=threshold and prev_deviation<threshold:
            out["rolling_mean_reversal"]="put"
        elif deviation<=-threshold and prev_deviation>-threshold:
            out["rolling_mean_reversal"]="call"

        trend30=_bps(x,closes[-31])
        # Momentum established over completed last 30 minutes, previous
        # bar pulled below/above a causal 5-close mean, current reclaims.
        mean5prior=statistics.fmean(closes[-7:-2])
        mean5now=statistics.fmean(closes[-6:-1])
        if (trend30>=cfg.min_30min_trend_bps and
            prev<=mean5prior and x>mean5now and x>prev):
            out["trend_pullback_reclaim"]="call"
        elif (trend30<=-cfg.min_30min_trend_bps and
              prev>=mean5prior and x<mean5now and x<prev):
            out["trend_pullback_reclaim"]="put"
    return out


def screen_session(frames: Sequence[HistoricalFrame],
                   cfg: ScreenConfig = ScreenConfig()) -> list[SignalRecord]:
    if not frames:
        return []
    day=frames[0].timestamp.astimezone(ET).date()
    if any(f.timestamp.astimezone(ET).date()!=day for f in frames):
        raise ValueError("session cannot contain another trading date")
    if any(a.timestamp>=b.timestamp for a,b in zip(frames,frames[1:])):
        raise ValueError("SPY frames must be in strict observed-time order")
    times=[f.timestamp for f in frames]
    closes=[f.market.spot for f in frames]
    vols=[f.market.volume_ratio for f in frames]
    out=[]
    lock_until={family:0 for family in FAMILIES}
    hold=cfg.hold_minutes
    for i in range(31,len(frames)-hold-1):
        local=times[i].astimezone(ET)
        # First 30 completed bars needed, and avoid close/expiration window.
        if not (time(10,0)<=local.time()<=time(15,25)):
            continue
        # Signal at end of completed minute i (i.time+60 sec).
        # Entry NEXT completed bar i+1 (close available at t+2 minutes),
        # exit completed bar i+1+hold (available at t+hold+2).
        j=i+1
        k=j+hold
        if k>=len(frames):
            continue
        # A gap invalidates the candidate, rather than compressing elapsed
        # minutes or benefiting from missing/disconnected market periods.
        if any((times[z+1]-times[z]).total_seconds()!=60
               for z in range(i,k)):
            continue
        if any(not math.isfinite(v) or v<=0 for v in (closes[i],closes[j],closes[k])):
            continue
        signals=_family_signals(closes[:i+1],vols[:i+1],cfg)
        for family,side in signals.items():
            if i<lock_until[family]:
                continue
            signed=(1 if side=="call" else -1)*_bps(closes[k],closes[j])
            out.append(SignalRecord(
                session=day.isoformat(),family=family,direction=side,
                signal_timestamp=(times[i]+timedelta(minutes=1)).isoformat(),
                proxy_entry_timestamp=(times[j]+timedelta(minutes=1)).isoformat(),
                proxy_exit_timestamp=(times[k]+timedelta(minutes=1)).isoformat(),
                signal_spot=closes[i],entry_spot_proxy=closes[j],
                exit_spot_proxy=closes[k],signed_spy_move_bps=signed,
                correct_direction=signed>0,
            ))
            # No overlapping 10-minute observations per strategy family.
            lock_until[family]=k+1
    return out


def _daily_block_bootstrap(records:Sequence[SignalRecord],days:Sequence[date],
                           cfg:ScreenConfig)->dict:
    """Bootstrap entire market sessions, preserving within-day dependence.

    Zero-signal sessions contribute zeros to *bps per session* statistic.
    Results are descriptive due to prior dataset reuse and limited tail size.
    """
    grouped={day.isoformat():[] for day in days}
    for rec in records:
        grouped[rec.session].append(rec.signed_spy_move_bps)
    per_day=[sum(grouped[d.isoformat()]) for d in days]
    if not per_day:
        return {"n_days":0,"per_day_signed_bps_95pct_ci":[None,None]}
    rng=random.Random(cfg.bootstrap_seed)
    reps=[]
    for _ in range(cfg.bootstrap_reps):
        sample=[per_day[rng.randrange(len(per_day))] for _ in per_day]
        reps.append(statistics.fmean(sample))
    reps.sort()
    low=reps[round(.025*(len(reps)-1))]
    high=reps[round(.975*(len(reps)-1))]
    return {"n_days":len(days),"per_day_signed_bps_95pct_ci":[round(low,4),round(high,4)],
            "bootstrap_reps":cfg.bootstrap_reps,
            "method":"iid whole-day bootstrap, no-trade days included; NOT options P&L or a live predictive confidence guarantee"}


def _metrics(records: Sequence[SignalRecord],days:Sequence[date],cfg:ScreenConfig)->dict:
    by_day=defaultdict(list)
    for r in records:
        by_day[r.session].append(r)
    ret=[r.signed_spy_move_bps for r in records]
    raw_underlying=[_bps(r.exit_spot_proxy,r.entry_spot_proxy) for r in records]
    weekly={}
    for day in days:
        iso=day.isocalendar()
        key=f"{iso.year}-W{iso.week:02d}"
        weekly.setdefault(key,0.)
        weekly[key]+=sum(r.signed_spy_move_bps for r in by_day[day.isoformat()])
    # Weekly signal basis points are NOT compounded account returns.
    week_vals=list(weekly.values())
    return {
        "session_count":len(days),"signal_count":len(records),
        "signal_sessions":sum(bool(by_day[d.isoformat()]) for d in days),
        "calls":sum(r.direction=="call" for r in records),
        "puts":sum(r.direction=="put" for r in records),
        "directional_hit_rate":sum(x>0 for x in ret)/len(ret) if ret else None,
        "mean_signed_spy_move_bps":round(statistics.fmean(ret),4) if ret else None,
        "same_timestamps_always_call_spy_bps":round(statistics.fmean(raw_underlying),4) if ret else None,
        "same_timestamps_always_put_spy_bps":round(-statistics.fmean(raw_underlying),4) if ret else None,
        "mean_signed_minus_2bps_hypothetical_spy_proxy":round(statistics.fmean(ret)-2,4) if ret else None,
        "mean_signed_minus_5bps_hypothetical_spy_proxy":round(statistics.fmean(ret)-5,4) if ret else None,
        "median_signed_spy_move_bps":round(statistics.median(ret),4) if ret else None,
        "mean_sum_signed_spy_bps_per_session_including_no_trade":round(
            sum(ret)/len(days),4) if days else None,
        "weeks_observed":len(weekly),
        "positive_signal_weeks_fraction":sum(x>0 for x in week_vals)/len(week_vals)
            if week_vals else None,
        "worst_week_sum_signed_spy_bps":round(min(week_vals),4) if week_vals else None,
        "largest_week_sum_signed_spy_bps":round(max(week_vals),4) if week_vals else None,
        "bootstrap":_daily_block_bootstrap(records,days,cfg),
        "account_returns":None,"options_trade_fills":None,"option_pnl":None,
    }


def run_screen(frames: Iterable[HistoricalFrame],*,cfg:ScreenConfig=ScreenConfig()):
    partitions={label:{"days":[],"signals":[]} for label,_,_ in PERIODS}
    received_sessions=[]
    current=[]
    current_day=None
    seen=0

    def flush() -> None:
        nonlocal current
        if not current:
            return
        day=current[0].timestamp.astimezone(ET).date()
        received_sessions.append(day)
        events=screen_session(current,cfg)
        for name,first,last in PERIODS:
            if first<=day<=last:
                partitions[name]["days"].append(day)
                partitions[name]["signals"].extend(events)
                break
        current=[]
    for frame in frames:
        day=frame.timestamp.astimezone(ET).date()
        if current_day is not None and day<current_day:
            raise ValueError("global SPY historical frames must be chronological")
        if day!=current_day:
            flush()
            current_day=day
        current.append(frame)
        seen+=1
    flush()
    outputs={}
    for name,obj in partitions.items():
        days=obj["days"]
        signals=obj["signals"]
        outputs[name]={family:_metrics([r for r in signals if r.family==family],days,cfg)
                       for family in FAMILIES}
    return {"source_frames":seen,"source_sessions":len(received_sessions),
            "calendar_first_session":str(received_sessions[0]) if received_sessions else None,
            "calendar_last_session":str(received_sessions[-1]) if received_sessions else None,
            "config":asdict(cfg),
            "periods":outputs,
            "all_signals":tuple(r for obj in partitions.values() for r in obj["signals"])}


def main(argv:Sequence[str]|None=None)->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir",default="/data/research")
    parser.add_argument("--output-dir",default="/data/research/free_signal_screen")
    args=parser.parse_args(argv)
    from .burst_research import _research_files
    paths=_research_files(Path(args.data_dir))
    if not paths:
        print("FREE SCREEN BLOCKED: no canonical SPY historical files",flush=True)
        return 2
    digest=hashlib.sha256()
    for path in paths:
        digest.update(path.name.encode())
        digest.update(str(path.stat().st_size).encode())
    result=run_screen(iter_research_directory(args.data_dir))
    dest=Path(args.output_dir)
    dest.mkdir(parents=True,exist_ok=True)
    ledger=dest/"all_price_signals_NOT_OPTIONS_PNL.csv"
    with ledger.open("w",newline="") as f:
        writer=csv.DictWriter(f,fieldnames=list(SignalRecord.__dataclass_fields__))
        writer.writeheader()
        writer.writerows(asdict(r) for r in result["all_signals"])
    report={key:val for key,val in result.items() if key!="all_signals"}
    report["dataset_file_size_fingerprint_sha256"]=digest.hexdigest()
    report["source_file_count"]=len(paths)
    report["ledger_path"]=str(ledger)
    report["classification"]="SIGNAL-DIRECTION STUDY; no executable option quotes, no 0DTE P&L, no claim of 100% weekly return"
    (dest/"research_summary.json").write_text(json.dumps(report,indent=2,sort_keys=True))
    print("FREE SPY SCREEN SUMMARY: "+json.dumps({
        "source_sessions":report["source_sessions"],"source_frames":report["source_frames"],
        "source_file_count":report["source_file_count"],"path":str(dest)
    },sort_keys=True),flush=True)
    for period,all_models in result["periods"].items():
        for family,m in all_models.items():
            print("FREE SPY SCREEN RESULT: "+json.dumps({
                "period":period,"strategy":family,
                "days":m["session_count"],"signals":m["signal_count"],
                "calls":m["calls"],"puts":m["puts"],
                "hit_rate":m["directional_hit_rate"],
                "mean_signed_spy_bps":m["mean_signed_spy_move_bps"],
                "matched_always_call_spy_bps":m["same_timestamps_always_call_spy_bps"],
                "mean_after_2bp_proxy_friction":m["mean_signed_minus_2bps_hypothetical_spy_proxy"],
                "mean_after_5bp_proxy_friction":m["mean_signed_minus_5bps_hypothetical_spy_proxy"],
                "mean_signed_spy_bps_per_session":m["mean_sum_signed_spy_bps_per_session_including_no_trade"],
                "bootstrap_95pct_day_bps":m["bootstrap"]["per_day_signed_bps_95pct_ci"],
                "positive_signal_weeks_fraction":m["positive_signal_weeks_fraction"],
                "option_account_return":None
            },sort_keys=True),flush=True)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
