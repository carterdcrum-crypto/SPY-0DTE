"""Weighted multi-setup SPY intraday price forecasts: DAILY coverage research.

Three preexisting causal setup families share ONE 10-minute nonoverlap
lock, to avoid simultaneous/duplicate hypothetical positions. We test
whether flexible additive indicator scoring raises signal availability
WITHOUT forcing a daily bet, and assess positive/negative/zero days.

Research ONLY. Signed SPY next-minute-close basis points are NOT
SPY 0DTE options profit, fills, contract cost, account P&L or drawdown.
No synthetic 'tape reading' and no broker actions. Known inspected data.
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
from .data import HistoricalFrame
from .free_signal_screen import ET, PERIODS, screen_session
from .macd_rsi_sma_tape_experiment import (
    _macd, _rsi_wilder, _sma_at, _bps, _cross_recent,
)

FAMILIES = ("close_breakout_baseline", "trend_pullback_reclaim", "rolling_mean_reversal")
POLICIES = ("all_setups_nonoverlapping", "weighted_score_ge4", "weighted_score_ge6")
WEIGHTS = {"recent_macd_crossover": 2, "rsi": 2, "sma": 2, "price_action": 2, "volume_proxy": 1}
HYPOTHETICAL_SPY_PRICE_HURDLE_BPS = 2.0
MIN_INDICATOR_HISTORY = 75
HOLD_MINUTES = 10
BOOTSTRAP_BLOCK_DAYS = 5
BOOTSTRAP_REPS = 1000
BOOTSTRAP_SEED = 17377


@dataclass(frozen=True)
class Opportunity:
    day: str
    setup: str
    original_direction: str
    signal_at: str
    entry_proxy_at: str
    exit_proxy_at: str
    rsi14: float
    sma20: float
    sma50: float
    macd: float
    macd_signal: float
    volume_ratio_at_signal: float
    recent_macd_crossover_points: int
    rsi_points: int
    sma_points: int
    price_action_points: int
    volume_proxy_points: int
    weighted_total: int
    # Future label: NEVER allowed into a strategy decision.
    signed_spy_ten_minute_close_change_bps: float


def decision(opportunity:Opportunity,policy:str)->bool:
    """Only pre-entry features; returns whether a setup qualifies."""
    if policy not in POLICIES:
        raise ValueError("unknown fixed weighted research policy")
    if policy == "all_setups_nonoverlapping":
        return True
    return opportunity.weighted_total >= (4 if policy == "weighted_score_ge4" else 6)


def session_opportunities(frames:Sequence[HistoricalFrame])->list[Opportunity]:
    if not frames:
        return []
    dates={f.timestamp.astimezone(ET).date() for f in frames}
    if len(dates)!=1:
        raise ValueError("one trading session required")
    spots=[f.market.spot for f in frames]
    volume=[f.market.volume_ratio for f in frames]
    times=[f.timestamp for f in frames]
    macd,signal=_macd(spots)
    rsi=_rsi_wilder(spots)
    sma20=_sma_at(spots,20)
    sma50=_sma_at(spots,50)
    idx_by_completed={t+timedelta(minutes=1):idx for idx,t in enumerate(times)}
    candidate=[r for r in screen_session(frames) if r.family in FAMILIES]
    results=[]
    for row in candidate:
        i=idx_by_completed[datetime.fromisoformat(row.signal_timestamp)]
        if i<MIN_INDICATOR_HISTORY:
            continue
        if any((times[k+1]-times[k]).total_seconds()!=60
               for k in range(i-MIN_INDICATOR_HISTORY,i)):
            continue
        if any(v is None or not math.isfinite(v)
               for v in (macd[i],signal[i],rsi[i],sma20[i],sma50[i])):
            continue
        side=1 if row.direction=="call" else -1
        cross=bool(_cross_recent(macd,signal,i,side>0))
        rsi_ok=(50<=rsi[i]<=75) if side>0 else (25<=rsi[i]<=50)
        sma_ok=(spots[i]>sma20[i]>sma50[i]) if side>0 else (
            spots[i]<sma20[i]<sma50[i])
        # Completed-CLOSE price action, NOT true OHLC / tick tape:
        # (1) directional 3-minute movement, (2) no >2bp adverse
        # intermediate CLOSE during the last 3 completed minutes.
        action=(side*_bps(spots[i],spots[i-3])>=1
                and min(side*_bps(spots[j],spots[i-3])
                        for j in range(i-2,i+1))>=-2)
        vol_ok=math.isfinite(volume[i]) and volume[i]>=1.25
        x=Opportunity(
            day=row.session,setup=row.family,original_direction=row.direction,
            signal_at=row.signal_timestamp,
            entry_proxy_at=row.proxy_entry_timestamp,
            exit_proxy_at=row.proxy_exit_timestamp,
            rsi14=rsi[i],sma20=sma20[i],sma50=sma50[i],
            macd=macd[i],macd_signal=signal[i],
            volume_ratio_at_signal=volume[i],
            recent_macd_crossover_points=2 if cross else 0,
            rsi_points=2 if rsi_ok else 0,
            sma_points=2 if sma_ok else 0,
            price_action_points=2 if action else 0,
            volume_proxy_points=1 if vol_ok else 0,
            weighted_total=(2*int(cross)+2*int(rsi_ok)+2*int(sma_ok)
                            +2*int(action)+int(vol_ok)),
            signed_spy_ten_minute_close_change_bps=row.signed_spy_move_bps,
        )
        results.append(x)
    return sorted(results,key=lambda x:(x.signal_at,x.setup))


def select_nonoverlapping(rows:Sequence[Opportunity],policy:str)->list[Opportunity]:
    """Lock after an accepted signal, not after a rejected candidate.

    The shared lock prevents multiple overlapping entries from three
    different strategies. Same-minute ties have predetermined order
    from FAMILIES, never selected using future move or profit.
    """
    if policy not in POLICIES:
        raise ValueError("invalid fixed policy")
    result=[]
    locked_until=None
    for x in sorted(rows,key=lambda x:(x.signal_at,FAMILIES.index(x.setup))):
        if not decision(x,policy):
            continue
        begin=datetime.fromisoformat(x.entry_proxy_at)
        exit_=datetime.fromisoformat(x.exit_proxy_at)
        if locked_until is not None and begin<=locked_until:
            continue
        result.append(x)
        locked_until=exit_
    return result


def _bootstrap(days:Sequence[str],selected:Sequence[Opportunity])->dict:
    if not days:
        return {"ci95_signed_underlying_bps_per_day":None}
    d=defaultdict(float)
    for x in selected:
        d[x.day]+=x.signed_spy_ten_minute_close_change_bps-HYPOTHETICAL_SPY_PRICE_HURDLE_BPS
    seq=[d[day] for day in days]
    n=len(seq)
    width=min(n,BOOTSTRAP_BLOCK_DAYS)
    rng=random.Random(BOOTSTRAP_SEED)
    simulated=[]
    for _ in range(BOOTSTRAP_REPS):
        picks=[]
        while len(picks)<n:
            start=rng.randrange(n)
            picks.extend((start+j)%n for j in range(width))
        simulated.append(statistics.fmean(seq[i] for i in picks[:n]))
    simulated.sort()
    return {"ci95_signed_underlying_bps_per_day":[
        round(simulated[round(.025*(len(simulated)-1))],4),
        round(simulated[round(.975*(len(simulated)-1))],4)],
        "days_including_zero_signal_days":n,
        "method":"paired-within-period circular five-session block bootstrap; previously inspected data",
    }


def _summary(days:Sequence[str],potential:Sequence[Opportunity],
             policy:str)->dict:
    chosen=[]
    by_day=defaultdict(list)
    for day in days:
        candidates=[x for x in potential if x.day==day]
        picked=select_nonoverlapping(candidates,policy)
        chosen.extend(picked)
        by_day[day].extend(picked)
    net2=[x.signed_spy_ten_minute_close_change_bps-HYPOTHETICAL_SPY_PRICE_HURDLE_BPS
          for x in chosen]
    net5=[x.signed_spy_ten_minute_close_change_bps-5.0 for x in chosen]
    daily={day:sum(x.signed_spy_ten_minute_close_change_bps-HYPOTHETICAL_SPY_PRICE_HURDLE_BPS
                   for x in by_day[day]) for day in days}
    week=defaultdict(float)
    for day,total in daily.items():
        d=date.fromisoformat(day).isocalendar()
        week[(d.year,d.week)]+=total
    cum=peak=decline=0.
    for day in days:
        cum+=daily[day]
        peak=max(peak,cum)
        decline=max(decline,peak-cum)
    return {
        "calendar_sessions":len(days),
        "eligible_family_opportunities_BEFORE_SHARED_LOCK":len(potential),
        "selected_signals":len(chosen),
        "signals_per_trading_session":round(len(chosen)/len(days),4) if days else None,
        "days_with_signals":sum(bool(by_day[day]) for day in days),
        "days_no_signals":sum(not by_day[day] for day in days),
        "days_positive_signed_spy_bps_after_2bp_proxy":sum(v>0 for v in daily.values()),
        "days_negative_signed_spy_bps_after_2bp_proxy":sum(v<0 for v in daily.values()),
        "days_zero_signed_spy_bps_after_2bp_proxy":sum(v==0 for v in daily.values()),
        "daily_positive_fraction_all_market_days":(
            sum(v>0 for v in daily.values())/len(days) if days else None),
        "daily_negative_fraction_all_market_days":(
            sum(v<0 for v in daily.values())/len(days) if days else None),
        "direction_correct":sum(x.signed_spy_ten_minute_close_change_bps>0 for x in chosen),
        "signed_spy_moves_over_2bps":sum(x.signed_spy_ten_minute_close_change_bps>2 for x in chosen),
        "signed_spy_moves_over_5bps":sum(x.signed_spy_ten_minute_close_change_bps>5 for x in chosen),
        "mean_signed_spy_bps_per_event_after_hypothetical_2bp":(
            round(statistics.fmean(net2),4) if net2 else None),
        "mean_signed_spy_bps_per_event_after_hypothetical_5bp":(
            round(statistics.fmean(net5),4) if net5 else None),
        "mean_signed_spy_bps_per_market_day_after_hypothetical_2bp":(
            round(sum(net2)/len(days),4) if days else None),
        "worst_market_day_additive_spy_bps_after_2bp":(
            round(min(daily.values()),4) if days else None),
        "best_market_day_additive_spy_bps_after_2bp":(
            round(max(daily.values()),4) if days else None),
        "worst_week_additive_spy_bps_after_2bp":round(min(week.values()),4) if week else None,
        "peak_to_trough_additive_spy_bps_NOT_account_drawdown":round(decline,4),
        "bootstrap":_bootstrap(days,chosen),
        "account_pnl":None,
        "actual_SPY_0DTE_options_net_pnl":None,
        "account_max_drawdown":None,
    }


def run_study(frames:Iterable[HistoricalFrame])->dict:
    periods={k:{"days":[],"opportunities":[]} for k,_,_ in PERIODS}
    sessions=frames_seen=0
    prior=None
    current=[]
    def flush()->None:
        nonlocal sessions,current
        if not current:return
        sessions+=1
        day=current[0].timestamp.astimezone(ET).date()
        eligible=session_opportunities(current)
        for label,start,end in PERIODS:
            if start<=day<=end:
                periods[label]["days"].append(day.isoformat())
                periods[label]["opportunities"].extend(eligible)
                break
        current=[]
    for frame in frames:
        day=frame.timestamp.astimezone(ET).date()
        if prior is not None and day<prior:
            raise ValueError("market sessions out of chronological order")
        if prior is not None and day!=prior:
            flush()
        current.append(frame)
        frames_seen+=1
        prior=day
    flush()
    results={}
    ledger=[]
    for label,_,_ in PERIODS:
        d=periods[label]
        results[label]={}
        for policy in POLICIES:
            results[label][policy]=_summary(d["days"],d["opportunities"],policy)
        ledger.extend(d["opportunities"])
    return {
        "experiment":"weighted_three_family_spy_close_momentum_mean_reversion_daily_coverage_v1",
        "sessions":sessions,"minute_frames":frames_seen,
        "policies":POLICIES,"setups":FAMILIES,
        "fixed_weights":WEIGHTS,"fixed_cutoffs":[4,6],
        "adaptive_trailing_return_gate":False,
        "tape_source":None,"real_option_bid_ask_event_data":None,
        "research_only":True,"orders_placed":0,
        "already_inspected_dates_NOT_untouched_holdout":True,
        "incremental_market_data_purchase_usd":0,
        "results":results,"opportunity_ledger":ledger,
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
                        default=Path("/data/research/weighted_daily_coverage"))
    args=parser.parse_args(argv)
    paths=_research_files(args.data_dir)
    if not paths:
        print("DAILY COVERAGE BLOCKED: no archived SPY minute frames",flush=True)
        return 2
    r=run_study(iter_research_directory(args.data_dir))
    args.output_dir.mkdir(parents=True,exist_ok=True)
    ledger=r.pop("opportunity_ledger")
    _csv(args.output_dir/"all_weighted_setups_NOT_OPTIONS_PNL.csv",
         [asdict(x) for x in ledger])
    digest=hashlib.sha256()
    for f in paths:
        digest.update(f.name.encode())
        digest.update(str(f.stat().st_size).encode())
    r["source_files"]=len(paths)
    r["source_file_size_fingerprint_sha256"]=digest.hexdigest()
    (args.output_dir/"report.json").write_text(json.dumps(r,indent=2,sort_keys=True))
    print("DAILY COVERAGE ARCHIVE: "+json.dumps({
        "sessions":r["sessions"],"frames":r["minute_frames"],
        "opportunities":len(ledger),"cost_new_market_data_usd":0,
        "folder":str(args.output_dir),
    },sort_keys=True),flush=True)
    for period,details in r["results"].items():
        for model,m in details.items():
            print("DAILY COVERAGE RESULT: "+json.dumps({
                "period":period,"policy":model,
                "days":m["calendar_sessions"],
                "signals":m["selected_signals"],
                "signals_per_day":m["signals_per_trading_session"],
                "days_with_signals":m["days_with_signals"],
                "days_positive_spy_bps_after_2bp":m["days_positive_signed_spy_bps_after_2bp_proxy"],
                "days_negative_spy_bps_after_2bp":m["days_negative_signed_spy_bps_after_2bp_proxy"],
                "days_no_trades":m["days_no_signals"],
                "net_spy_bps_per_event_after_2bp":m["mean_signed_spy_bps_per_event_after_hypothetical_2bp"],
                "net_spy_bps_per_day_after_2bp":m["mean_signed_spy_bps_per_market_day_after_hypothetical_2bp"],
                "ci95_daily":m["bootstrap"]["ci95_signed_underlying_bps_per_day"],
                "real_options_net_pnl":None,
            },sort_keys=True),flush=True)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
