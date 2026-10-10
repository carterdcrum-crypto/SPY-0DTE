"""SPY 0DTE momentum + volume + price-action/reversal + MACD + PUT/CALL
VOLUME-RATIO experiment, with honest data coverage and no broker orders.

The archived 128 sessions contain SPY one-minute CLOSE & volume_ratio
snapshots, NOT complete point-in-time SPY option-chain put/call volumes.
Never infer put/call ratio from incomplete sampled contracts. Without a
verified full-chain cumulative-volume time series, only the indicator-only
ABLATION can run. The actual five-factor fusion is marked NOT TESTED.
All returns here are underlying SPY 10m CLOSE PRICE PROXIES, not option P&L.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import random
import statistics
from collections import defaultdict
from dataclasses import asdict,dataclass
from datetime import date,datetime,timedelta
from pathlib import Path
from typing import Iterable,Sequence

from .burst_research import _research_files,iter_research_directory
from .data import HistoricalFrame
from .free_signal_screen import ET,PERIODS
from .macd_rsi_sma_tape_experiment import _macd,_rsi_wilder,_bps
from .weighted_daily_coverage import (
    Opportunity,session_opportunities,select_nonoverlapping,
)

POLICIES=("prior_all_three_families","momentum_reversal_four_factors",
          "momentum_reversal_plus_verified_spy_pcr")
HYPOTHETICAL_SPY_SPOT_COST_BPS=2.0
MAX_PCR_AGE_SECONDS=300
BOOTSTRAP_REPS=1000
BOOTSTRAP_SEED=20261009


@dataclass(frozen=True)
class PCRPoint:
    """Cumulative observed, complete SPY option put/call CONTRACT volume."""
    observed_at:datetime
    put_volume_so_far:int
    call_volume_so_far:int
    source:str
    coverage:str

    def __post_init__(self):
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("PCR point must have timezone-aware observation")
        if min(self.put_volume_so_far,self.call_volume_so_far)<0:
            raise ValueError("cumulative SPY option volumes must be nonnegative")
        if self.call_volume_so_far==0:
            raise ValueError("PCR cannot divide by zero call volume")
        if self.coverage!="all_spy_listed_options":
            raise ValueError("PCR must cover all SPY option contracts, not an incomplete sampled chain")
        if not self.source.strip():
            raise ValueError("real full-SPY options volume provenance required")

    @property
    def ratio(self)->float:
        return self.put_volume_so_far/self.call_volume_so_far


@dataclass(frozen=True)
class Classified:
    opportunity:Opportunity
    family_mode:str
    factor_score:int
    price_action_confirmed:bool
    macd_histo_aligned_or_improving:bool
    volume_confirmed:bool
    reversal_confirmed:bool
    pcr_value:float|None
    pcr_observed_at:str|None
    pcr_confirms:bool|None
    # no outcome field is consulted by decision()
    reasons:tuple[str,...]


def _csv_open(path:Path):
    return gzip.open(path,"rt",encoding="utf8",newline="") if path.suffix==".gz" else path.open("r",encoding="utf8",newline="")


def load_complete_spy_pcr(files:Sequence[Path])->dict[str,tuple[PCRPoint,...]]:
    """No external data fetch: verify point-in-time full-chain *volume* feed.

    Required CSV:
    observed_at,put_volume_so_far,call_volume_so_far,coverage,source.
    Observed timestamps may not be end-of-day timestamps retro-applied
    to earlier signals; missing PCR means UNKNOWN, never neutral.
    """
    grouped=defaultdict(list)
    for file in sorted(files):
        with _csv_open(file) as f:
            reader=csv.DictReader(f)
            required={"observed_at","put_volume_so_far","call_volume_so_far",
                      "coverage","source"}
            if not required.issubset(reader.fieldnames or []):
                raise ValueError("PCR needs genuine full-chain volume and observation columns")
            for row in reader:
                t=datetime.fromisoformat(row["observed_at"].replace("Z","+00:00"))
                point=PCRPoint(
                    observed_at=t,
                    put_volume_so_far=int(row["put_volume_so_far"]),
                    call_volume_so_far=int(row["call_volume_so_far"]),
                    source=row["source"],coverage=row["coverage"],
                )
                day=t.astimezone(ET).date().isoformat()
                if grouped[day]:
                    prev=grouped[day][-1]
                    if t<=prev.observed_at or (
                        point.put_volume_so_far<prev.put_volume_so_far or
                        point.call_volume_so_far<prev.call_volume_so_far
                    ):
                        raise ValueError("noncausal, duplicate or decreasing PCR volume snapshots")
                grouped[day].append(point)
    return {k:tuple(v) for k,v in grouped.items()}


def pcr_at(history:Sequence[PCRPoint]|None,signal_at:datetime)->PCRPoint|None:
    if not history:
        return None
    present=[x for x in history
             if x.observed_at<=signal_at
             and 0<=(signal_at-x.observed_at).total_seconds()<=MAX_PCR_AGE_SECONDS]
    return present[-1] if present else None


def _confirmed_pcr(op:Opportunity,ratio:float)->bool:
    """Pre-registered different hypotheses for momentum and reversals.

    Momentum: follow call-heavy CALL / put-heavy PUT volume.
    Reversal: trade against unusually put-heavy (CALL) / call-heavy (PUT).
    No threshold selected from historical outcomes.
    """
    if op.setup=="rolling_mean_reversal":
        return ratio>=1.15 if op.original_direction=="call" else ratio<=.85
    return ratio<=.95 if op.original_direction=="call" else ratio>=1.05


def session_classifications(
    frames:Sequence[HistoricalFrame],pcr_day:Sequence[PCRPoint]|None=None,
)->list[Classified]:
    if not frames:
        return []
    opens=[x.market.spot for x in frames]
    volumes=[x.market.volume_ratio for x in frames]
    times=[x.timestamp for x in frames]
    macd,signal=_macd(opens)
    rsi=_rsi_wilder(opens)
    by_completed={t+timedelta(minutes=1):i for i,t in enumerate(times)}
    candidates=session_opportunities(frames)
    result=[]
    for op in candidates:
        i=by_completed[datetime.fromisoformat(op.signal_at)]
        if i<75 or any(v is None for v in (macd[i],signal[i],macd[i-1],signal[i-1],rsi[i])):
            continue
        side=1 if op.original_direction=="call" else -1
        hist=macd[i]-signal[i]
        prev_hist=macd[i-1]-signal[i-1]
        d3=side*_bps(opens[i],opens[i-3])
        d1=side*_bps(opens[i],opens[i-1])
        family="reversal" if op.setup=="rolling_mean_reversal" else "momentum"
        if family=="momentum":
            price_action=d3>=2 and d1>=0
            macd_ok=side*hist>0
            volume_ok=math.isfinite(volumes[i]) and volumes[i]>=1.25
            rev=False
            score=2*int(price_action)+2*int(macd_ok)+2*int(volume_ok)
            # 5-minute momentum direction. Independent vote from
            # the completed last-minute price action.
            score+=int(side*_bps(opens[i],opens[i-5])>=3)
            passed=score>=5 and volume_ok
        else:
            # Reversal from CLOSE excursions: turn back toward proposed
            # direction in last bar and improving MACD histogram.
            price_action=d1>=.5
            macd_ok=side*(hist-prev_hist)>0
            volume_ok=math.isfinite(volumes[i]) and volumes[i]>=1.10
            rev=(rsi[i]<45 if side>0 else rsi[i]>55)
            score=2*int(price_action)+2*int(macd_ok)+int(volume_ok)+2*int(rev)
            passed=score>=5 and price_action
        # Retain factor score and predecided pass flag in reasons;
        # PCR availability CANNOT turn an otherwise failed factor rule on.
        as_of=datetime.fromisoformat(op.signal_at)
        point=pcr_at(pcr_day,as_of)
        pcr_ok=_confirmed_pcr(op,point.ratio) if point else None
        result.append(Classified(
            opportunity=op,family_mode=family,factor_score=score,
            price_action_confirmed=price_action,
            macd_histo_aligned_or_improving=macd_ok,
            volume_confirmed=volume_ok,reversal_confirmed=rev,
            pcr_value=point.ratio if point else None,
            pcr_observed_at=point.observed_at.isoformat() if point else None,
            pcr_confirms=pcr_ok,
            reasons=("base_four_factors_PASS" if passed else "base_four_factors_REJECT",),
        ))
    return result


def select(rows:Sequence[Classified],policy:str)->list[Classified]:
    if policy not in POLICIES:
        raise ValueError("invalid frozen strategy variant")
    def eligible(x:Classified)->bool:
        if policy=="prior_all_three_families":
            return True
        if "base_four_factors_PASS" not in x.reasons:
            return False
        if policy==POLICIES[-1]:
            return x.pcr_confirms is True
        return True
    accepted=[]
    locked=None
    # Tie priority immutable: breakout, pullback, reversal.
    priority={"close_breakout_baseline":0,"trend_pullback_reclaim":1,
              "rolling_mean_reversal":2}
    for x in sorted(rows,key=lambda r:(
        r.opportunity.signal_at,priority[r.opportunity.setup]
    )):
        if not eligible(x):
            continue
        op=x.opportunity
        entry=datetime.fromisoformat(op.entry_proxy_at)
        exit_=datetime.fromisoformat(op.exit_proxy_at)
        if locked is not None and entry<=locked:
            continue
        accepted.append(x)
        locked=exit_
    return accepted


def _stats(days:Sequence[str],opportunities:Sequence[Classified],
           policy:str,pcr_complete:bool)->dict:
    if policy==POLICIES[-1] and not pcr_complete:
        return {
            "status":"NOT_TESTED_INCOMPLETE_VERIFIED_INTRADAY_SPY_PCR",
            "accepted":None,"correct_direction":None,
            "signed_spy_bps_per_day_minus_2bp_proxy":None,
            "option_pnl":None,"account_drawdown":None,
        }
    grouped=defaultdict(list)
    for x in opportunities:
        grouped[x.opportunity.day].append(x)
    trades=[x for day in days for x in select(grouped[day],policy)]
    raw=[x.opportunity.signed_spy_ten_minute_close_change_bps for x in trades]
    net=[x-HYPOTHETICAL_SPY_SPOT_COST_BPS for x in raw]
    sums={day:0. for day in days}
    for x,v in zip(trades,net):
        sums[x.opportunity.day]+=v
    weekly=defaultdict(float)
    peak=cumulative=decline=0.
    for day in days:
        d=date.fromisoformat(day).isocalendar()
        weekly[(d.year,d.week)]+=sums[day]
        cumulative+=sums[day]
        peak=max(peak,cumulative)
        decline=max(decline,peak-cumulative)
    n=len(days)
    if n:
        rng=random.Random(BOOTSTRAP_SEED)
        means=[]
        span=min(n,5)
        seq=list(sums.values())
        for _ in range(BOOTSTRAP_REPS):
            idx=[]
            while len(idx)<n:
                start=rng.randrange(n)
                idx.extend((start+j)%n for j in range(span))
            means.append(statistics.fmean(seq[i] for i in idx[:n]))
        means.sort()
        ci=[round(means[round(.025*(len(means)-1))],4),
            round(means[round(.975*(len(means)-1))],4)]
    else:
        ci=[None,None]
    return {
        "status":"RESEARCH_UNDERLYING_SPY_PRICE_ONLY",
        "calendar_sessions":n,"available_opportunities":len(opportunities),
        "accepted":len(trades),"signal_days":len({x.opportunity.day for x in trades}),
        "correct_direction":sum(v>0 for v in raw),
        "signed_move_gt2bps":sum(v>2 for v in raw),
        "signed_move_gt5bps":sum(v>5 for v in raw),
        "positive_spy_proxy_days":sum(x>0 for x in sums.values()),
        "negative_spy_proxy_days":sum(x<0 for x in sums.values()),
        "zero_spy_proxy_days":sum(x==0 for x in sums.values()),
        "average_signed_spy_bps_minus_hypothetical_2bp":(
            round(statistics.fmean(net),4) if net else None),
        "signed_spy_bps_per_day_minus_2bp_proxy":round(sum(net)/n,4) if n else None,
        "max_additive_SPY_bps_decline_NOT_ACCOUNT_DD":round(decline,4),
        "worst_week_additive_SPY_bps":round(min(weekly.values()),4) if weekly else None,
        "five_session_block_bootstrap95_daily_bps":ci,
        "option_pnl":None,"account_drawdown":None,
        "no_option_execution_data":True,
    }


def run_study(frames:Iterable[HistoricalFrame],pcr:dict[str,tuple[PCRPoint,...]]|None=None)->dict:
    days=defaultdict(list)
    pool=defaultdict(list)
    current=[]
    last=None
    nframes=sessions=0
    pcr_missing_days=defaultdict(list)
    labels={label for label,_,_ in PERIODS}
    def flush():
        nonlocal current,sessions
        if not current:return
        day=current[0].timestamp.astimezone(ET).date()
        per=next((k for k,a,b in PERIODS if a<=day<=b),None)
        sessions+=1
        classified=session_classifications(current,(pcr or {}).get(day.isoformat()))
        if per in labels:
            days[per].append(day.isoformat())
            pool[per].extend(classified)
            if any(x.pcr_value is None and
                   "base_four_factors_PASS" in x.reasons for x in classified):
                pcr_missing_days[per].append(day.isoformat())
        current=[]
    for frame in frames:
        day=frame.timestamp.astimezone(ET).date()
        if last is not None and day<last:
            raise ValueError("frames must be chronologically ordered")
        if last is not None and day!=last:
            flush()
        current.append(frame)
        last=day
        nframes+=1
    flush()
    result={}
    for name,_,_ in PERIODS:
        all_rows=pool[name]
        eligible_pcr=sum("base_four_factors_PASS" in r.reasons for r in all_rows)
        observed_pcr=sum("base_four_factors_PASS" in r.reasons and r.pcr_value is not None for r in all_rows)
        # Full-chain PCR signals must be available for EVERY prospective
        # eligible timestamp, otherwise date/market-based missingness biases
        # the result. If there are zero eligible opportunities, unavailable
        # means no evidence, NOT proof of profit.
        complete=bool(pcr is not None and eligible_pcr>0 and eligible_pcr==observed_pcr)
        result[name]={
            "trading_days":len(days[name]),
            "base_four_factor_candidates":eligible_pcr,
            "verified_pcr_at_eligible_signal_count":observed_pcr,
            "missing_pcr_dates":pcr_missing_days[name],
            "models":{
                policy:_stats(days[name],all_rows,policy,complete)
                for policy in POLICIES},
        }
    return {
        "study":"SPY_0DTE_momentum_volume_close_price_reversal_MACD_plus_verified_spy_put_call_VOLUME_ratio",
        "sessions":sessions,"frames":nframes,
        "pcr_mode":"FULL_CHAIN_POINT_IN_TIME_OR_FAIL_CLOSED",
        "pcr_supplied":pcr is not None,
        "pcr_is_SPY_only_not_CBOE_marketwide":True,
        "same_frozen_underlying_10m_close_outcome_clock":True,
        "proxy_hypothetical_spy_bps_cost":HYPOTHETICAL_SPY_SPOT_COST_BPS,
        "historical_data_previously_inspected":True,
        "real_option_pnl":None,
        "live_trades_placed":0,
        "data_purchases_usd":0,
        "adaptive_trailing_return_gate":False,
        "periods":result,
    }


def main(argv:Sequence[str]|None=None)->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir",type=Path,default=Path("/data/research"))
    parser.add_argument("--output-dir",type=Path,default=Path("/data/research/spy_pcr_momentum_reversal"))
    args=parser.parse_args(argv)
    sources=_research_files(args.data_dir)
    if not sources:
        print("SPY PCR STUDY BLOCKED: SPY archive missing",flush=True)
        return 2
    pcr_files=sorted([*args.data_dir.glob("spy_pcr_*.csv"),
                      *args.data_dir.glob("spy_pcr_*.csv.gz")])
    pcr=load_complete_spy_pcr(pcr_files) if pcr_files else None
    result=run_study(iter_research_directory(args.data_dir),pcr)
    args.output_dir.mkdir(parents=True,exist_ok=True)
    digest=hashlib.sha256()
    for p in sources:
        digest.update(p.name.encode())
        digest.update(str(p.stat().st_size).encode())
    result["archived_spy_files"]=len(sources)
    result["source_files_size_fingerprint_sha256"]=digest.hexdigest()
    result["pcr_files"]=len(pcr_files)
    (args.output_dir/"report.json").write_text(json.dumps(result,indent=2,sort_keys=True))
    print("SPY PCR STUDY COVERAGE: "+json.dumps({
        "sessions":result["sessions"],"frames":result["frames"],
        "pcr_files":len(pcr_files),"historical_option_pnl":None,
        "new_data_purchased":False,
        "report_path":str(args.output_dir),
    },sort_keys=True),flush=True)
    for period,contents in result["periods"].items():
        for name,model in contents["models"].items():
            print("SPY PCR STUDY RESULT: "+json.dumps({
                "period":period,"policy":name,"status":model["status"],
                "sessions":contents["trading_days"],"accepted":model["accepted"],
                "positive_days":model.get("positive_spy_proxy_days"),
                "negative_days":model.get("negative_spy_proxy_days"),
                "correct_direction":model["correct_direction"],
                "spot_bps_per_day_minus_2bp_proxy":model["signed_spy_bps_per_day_minus_2bp_proxy"],
                "bootstrap95":model.get("five_session_block_bootstrap95_daily_bps"),
                "missing_pcr_signal_days":len(contents["missing_pcr_dates"]),
                "actual_option_pnl":None,
            },sort_keys=True),flush=True)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
