"""Opening CLOSE-range breakout/retest frequency vs expectancy experiment.

Zero broker orders/purchases. No fixed take-profit: frozen 30% whole-position
trailing stop, independent 35% initial loss stop, 10-minute maximum hold.
Existing canonical SPY data contain CLOSE+volume_ratio, NOT OHLCV high/low,
real VWAP, real order book, tick prints, point-in-time full-chain PCR, or
execution-grade option NBBO. Never call these true opening range high/lows.
Research is exploratory on 128 PREVIOUSLY INSPECTED sessions.
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
from .trailing_only_sweep import simulate, period

MORNING_END=time(11,0)
ALLDAY_END=time(15,25)
FROZEN_TRAIL=30
FROZEN_HARD_STOP=35
FROZEN_MAX_MIN=10
SPREAD_STRESS=2.0
RETEST_NEAR_BPS=3.0
RETEST_FAILURE_BPS=7.0
CONFIRM_BPS=1.0
BREAKOUT_MIN_VOLUME=1.25
RETEST_MIN_VOLUME=1.25
POLICIES=(
    "direct_5m_morning",
    "direct_15m_morning",
    "retest_5m_morning",
    "retest_15m_morning",
    "retest_5m_morning_rvol150",
    "retest_5m_morning_cost_quality",
    "retest_5m_morning_volatility_quality",
    "hybrid_5m_direct_or_retest_morning",
    "retest_5m_all_day",
    "retest_15m_all_day",
    "retest_5m_all_day_cost_quality",
)
REGIMES=("development","validation","previously_studied_diagnostic","all_inspected")


def active(ts:datetime, end:time)->bool:
    return time(9,35)<=ts.astimezone(ET).time()<end


def valid_close_prefix(frames,i,n:int)->bool:
    if i<n-1:return False
    if not all(math.isfinite(frames[k].market.spot) and frames[k].market.spot>0
               and math.isfinite(frames[k].market.volume_ratio)
               for k in range(max(0,i-n-2),i+1)):
        return False
    return all((frames[k+1].timestamp-frames[k].timestamp).total_seconds()==60
               for k in range(max(0,i-n-2),i))


def near_retest(price:float,level:float,right:str)->bool:
    diff=_bps(price,level)
    return (-RETEST_FAILURE_BPS<=diff<=RETEST_NEAR_BPS if right=="call"
            else -RETEST_NEAR_BPS<=diff<=RETEST_FAILURE_BPS)


def confirmed(price:float,prior:float,level:float,right:str)->bool:
    diff=_bps(price,level)
    return (price>prior and diff>=CONFIRM_BPS if right=="call"
            else price<prior and diff<=-CONFIRM_BPS)


def range_events(frames,n:int)->list[dict]:
    """Form first n completed CLOSES, then breakout→separate-bar retest→confirm.

    At i=n, the earliest signal is 9:36 for 5m and 9:46 for 15m.
    The scanner can fire direct and retest candidate signals multiple times
    only after price has been inside the original range for two closes.
    This is state from completed bars ONLY.
    """
    if len(frames)<n+3 or not valid_close_prefix(frames,n,n):
        return []
    upper=max(x.market.spot for x in frames[:n])
    lower=min(x.market.spot for x in frames[:n])
    if upper<=lower:return []
    inside_count=0
    armed=True
    pending=None
    events=[]
    for i in range(n,len(frames)):
        observed=frames[i].timestamp+timedelta(minutes=1)
        if not active(observed,ALLDAY_END):
            if observed.astimezone(ET).time()>=ALLDAY_END:break
            continue
        if not valid_close_prefix(frames,i,n):
            pending=None
            armed=False
            inside_count=0
            continue
        now=frames[i].market.spot
        prev=frames[i-1].market.spot
        volume=frames[i].market.volume_ratio
        within=lower<=now<=upper
        if within:
            inside_count+=1
        else:
            inside_count=0
        if inside_count>=2:
            armed=True
        # Candidate entry emits ONLY after an additional confirm bar.
        if pending is not None:
            side=pending["side"]
            level=upper if side=="call" else lower
            if i-pending["breakout_i"]>12:
                pending=None
            elif pending["retest_i"] is None:
                if i>pending["breakout_i"] and near_retest(now,level,side):
                    pending["retest_i"]=i
                elif (side=="call" and _bps(now,level)<-RETEST_FAILURE_BPS
                      or side=="put" and _bps(now,level)>RETEST_FAILURE_BPS):
                    pending=None
            elif i>pending["retest_i"]:
                if (confirmed(now,prev,level,side)
                        and volume>=RETEST_MIN_VOLUME):
                    events.append({
                        "signal_at":observed.isoformat(),"right":side,
                        "type":"retest","range_minutes":n,
                        "volume_ratio":volume,
                        "breakout_observed_at":pending["breakout_at"],
                        "retest_observed_at":(
                            frames[pending["retest_i"]].timestamp
                            +timedelta(minutes=1)).isoformat(),
                        "upper_close_range":upper,"lower_close_range":lower,
                    })
                    pending=None
                elif not near_retest(now,level,side):
                    pending=None
        if not armed:continue
        side=None
        if now>upper and prev<=upper and volume>=BREAKOUT_MIN_VOLUME:
            side="call"
        elif now<lower and prev>=lower and volume>=BREAKOUT_MIN_VOLUME:
            side="put"
        if side is None:continue
        events.append({
            "signal_at":observed.isoformat(),"right":side,
            "type":"direct","range_minutes":n,
            "volume_ratio":volume,"breakout_observed_at":observed.isoformat(),
            "retest_observed_at":None,
            "upper_close_range":upper,"lower_close_range":lower,
        })
        pending={"side":side,"breakout_i":i,"retest_i":None,
                 "breakout_at":observed.isoformat()}
        armed=False
    return events


def candidate_quality(frames,at_idx,quote,right)->dict:
    """As-of observed completed SPY bars and modeled first option quote."""
    spot=frames[at_idx].market
    signalspot=spot.spot
    earlier=frames[max(0,at_idx-5)].market.spot
    signed=(1 if right=="call" else -1)*_bps(signalspot,earlier)
    hypothetical_delta_movement_usd=abs(quote.delta)*signalspot*max(0,signed)/10000*100
    quoted_roundtrip_friction_usd=(quote.ask-quote.bid)*100+2*.65+2.0
    iv_rv_ratio=(quote.implied_volatility/spot.realized_volatility
                 if spot.realized_volatility>0 else math.inf)
    return {
        "option_spread_fraction":quote.spread_fraction,
        "option_iv_to_spy_realized_vol_proxy_ratio":iv_rv_ratio,
        "proxy_delta_5min_directional_move_usd":hypothetical_delta_movement_usd,
        "entry_quote_roundtrip_friction_proxy_usd":quoted_roundtrip_friction_usd,
        "estimated_entry_debit_per_contract_usd":(
            quote.ask+.01)*100+.65,
        "signed_spy_5min_change_bps":signed,
    }


def accept(event:dict,quality:dict,arm:str)->bool:
    if arm not in POLICIES:raise ValueError("unrecognized study policy")
    n=event["range_minutes"]
    kind=event["type"]
    end=MORNING_END if "_morning" in arm else ALLDAY_END
    if not active(datetime.fromisoformat(event["signal_at"]),end):return False
    direct=kind=="direct"
    retest=kind=="retest"
    q=quality
    cost_good=(
        q["option_spread_fraction"]<=.08 and
        q["proxy_delta_5min_directional_move_usd"]
        >=1.5*q["entry_quote_roundtrip_friction_proxy_usd"]
    )
    iv_good=(
        math.isfinite(q["option_iv_to_spy_realized_vol_proxy_ratio"]) and
        q["option_iv_to_spy_realized_vol_proxy_ratio"]<=1.5
    )
    return {
        "direct_5m_morning":n==5 and direct,
        "direct_15m_morning":n==15 and direct,
        "retest_5m_morning":n==5 and retest,
        "retest_15m_morning":n==15 and retest,
        "retest_5m_morning_rvol150":n==5 and retest
             and event["volume_ratio"]>=1.5,
        "retest_5m_morning_cost_quality":n==5 and retest and cost_good,
        "retest_5m_morning_volatility_quality":n==5 and retest and iv_good,
        "hybrid_5m_direct_or_retest_morning":n==5,
        "retest_5m_all_day":n==5 and retest,
        "retest_15m_all_day":n==15 and retest,
        "retest_5m_all_day_cost_quality":n==5 and retest and cost_good,
    }[arm]


def schedule_trades(candidates:list[dict],arm:str,diag:dict)->list[dict]:
    """One simultaneous option. Re-enter as soon as prior position has exited.

    The exit timestamp of an earlier completed trade is information known at
    the NEXT candidate's entry; no outcomes from later entries influence
    earlier selection. Missing bid paths count as unavailable and lock for
    worst-case 10min, not a prescient skip.
    """
    chosen=[]
    flat_at=None
    seen_keys=set()
    for item in sorted(candidates,key=lambda x:(
        x["entry_time"],x["event"]["signal_at"],
        0 if x["event"]["type"]=="retest" else 1,x["symbol"])):
        if not accept(item["event"],item["quality"],arm):
            continue
        if item["entry_time"] in seen_keys:
            diag[arm+"_simultaneous_candidate_skipped"]+=1
            continue
        if flat_at is not None and item["entry_time"]<=flat_at:
            diag[arm+"_overlap_skipped"]+=1
            continue
        seen_keys.add(item["entry_time"])
        diag[arm+"_entry_attempts"]+=1
        # Compute realized hold only AFTER hypothetical entry is accepted.
        # No foresight used to select the candidate.
        path=_sampled_path(item["frames"],item["quote_index"],item["symbol"])
        if path is None:
            diag[arm+"_missing_complete_future_option_quotes"]+=1
            flat_at=item["entry_time"]+timedelta(minutes=FROZEN_MAX_MIN)
            continue
        actual=simulate(item["quote"].ask,path,FROZEN_TRAIL)
        exit_dt=datetime.fromisoformat(actual["exit_time"])
        if exit_dt<item["entry_time"]:
            raise AssertionError("future exit timestamp precedes entry")
        flat_at=exit_dt
        chosen.append({
            "day":item["day"],"arm":arm,
            "signal_at":item["event"]["signal_at"],
            "entry_observation_at":item["entry_time"].isoformat(),
            "exit_at":actual["exit_time"],"exit_reason":actual["exit_reason"],
            "symbol":item["symbol"],"right":item["event"]["right"],
            "range_minutes":item["event"]["range_minutes"],
            "signal_type":item["event"]["type"],
            "volume_ratio":item["event"]["volume_ratio"],
            "pnl_usd":actual["pnl_usd"],
            "premium_return_pct":actual["return_on_premium_pct"],
            "entry_debit_usd":item["quality"]["estimated_entry_debit_per_contract_usd"],
            "quality_iv_rv_ratio":round(item["quality"]["option_iv_to_spy_realized_vol_proxy_ratio"],4),
            "quote_spread_frac":item["quality"]["option_spread_fraction"],
        })
    return chosen


def metrics(ledger:list[dict],days:list[str],arm:str,group:str)->dict:
    groupdays=[d for d in days if group=="all_inspected" or period(d)==group]
    rows=[x for x in ledger if x["arm"]==arm and
          (group=="all_inspected" or period(x["day"])==group)]
    p=[x["pnl_usd"] for x in rows]
    day_pnl=defaultdict(float)
    day_count=defaultdict(int)
    for x in rows:
        day_pnl[x["day"]]+=x["pnl_usd"]
        day_count[x["day"]]+=1
    sorted_counts=sorted((day_count[d] for d in groupdays))
    pos_win=[x for x in p if x>0]
    neg_loss=[x for x in p if x<=0]
    runmax=dd=total=0.
    for x in p:
        total+=x
        runmax=max(runmax,total)
        dd=max(dd,runmax-total)
    gross=sum(p)
    return {
        "calendar_sessions":len(groupdays),"trades":len(rows),
        "trades_per_session_including_no_trade":round(
            len(rows)/len(groupdays),3) if groupdays else None,
        "active_days":sum(day_count[d]>0 for d in groupdays),
        "trades_per_active_day":round(len(rows)/sum(day_count[d]>0 for d in groupdays),3)
            if any(day_count[d]>0 for d in groupdays) else None,
        "days_at_least_two_trades":sum(day_count[d]>=2 for d in groupdays),
        "days_at_least_three_trades":sum(day_count[d]>=3 for d in groupdays),
        "days_at_least_five_trades":sum(day_count[d]>=5 for d in groupdays),
        "maximum_trades_in_one_day":max(sorted_counts,default=0),
        "wins":len(pos_win),
        "win_rate_pct":round(100*len(pos_win)/len(p),2) if p else None,
        "average_win_usd":round(statistics.fmean(pos_win),2) if pos_win else None,
        "average_nonwin_usd":round(statistics.fmean(neg_loss),2) if neg_loss else None,
        "mean_premium_return_pct":round(statistics.fmean(
            x["premium_return_pct"] for x in rows),3) if rows else None,
        "hypothetical_one_contract_sum_usd_NOT_ACCOUNT":round(gross,2),
        "extra_two_dollars_friction_sum_usd":round(
            gross-SPREAD_STRESS*len(rows),2),
        "hypothetical_closed_trade_drawdown_usd_NOT_ACCOUNT":round(dd,2),
        "positive_pnl_days":sum(day_pnl[d]>0 for d in groupdays),
        "negative_pnl_days":sum(day_pnl[d]<0 for d in groupdays),
        "no_trade_or_flat_days":sum(day_pnl[d]==0 for d in groupdays),
        "net_profitable_week_count":sum(
            sum(day_pnl[d] for d in groupdays
                if datetime.fromisoformat(d).isocalendar()[:2]==week)>0
            for week in {datetime.fromisoformat(d).isocalendar()[:2] for d in groupdays}
        ),
        "week_count":len({datetime.fromisoformat(d).isocalendar()[:2]
            for d in groupdays}),
        "note":"NOT percentage account returns; no portfolio compounding modeled",
    }


def block_test(ledger:list[dict],dates:list[str],arm:str,reps:int=1200)->dict:
    byday=defaultdict(float)
    counts=defaultdict(int)
    for x in ledger:
        if x["arm"]==arm:
            byday[x["day"]]+=x["pnl_usd"]
            counts[x["day"]]+=1
    raw=[byday[d] for d in dates]
    stressed=[byday[d]-SPREAD_STRESS*counts[d] for d in dates]
    seed=random.Random(50115)
    def ci(arr):
        if not arr:return None
        trials=[]
        for _ in range(reps):
            sample=[]
            while len(sample)<len(arr):
                i=seed.randrange(len(arr))
                sample.extend(arr[(i+j)%len(arr)] for j in range(5))
            trials.append(statistics.fmean(sample[:len(arr)]))
        trials.sort()
        return [round(trials[int(.025*reps)],3),round(trials[int(.975*reps)-1],3)]
    largest=sorted((byday[d] for d in dates),reverse=True)
    return {
        "block_ci_pnl_usd_per_calendar_session":ci(raw),
        "block_ci_extra_execution_stress_pnl_usd_per_calendar_session":ci(stressed),
        "exclude_best_day_total_pnl_usd":round(sum(raw)-(largest[0] if largest else 0),2),
        "exclude_best_3_days_total_pnl_usd":round(sum(raw)-sum(largest[:3]),2),
        "already_inspected_archive":True,
        "not_multiple_test_corrected":True,
    }


def run(source:Path,destination:Path)->dict:
    dates=[]
    ledger=[]
    diagnostic=defaultdict(int)
    for file in _research_files(source):
        frames=list(load_canonical_path(file))
        if not frames:continue
        day=frames[0].timestamp.astimezone(ET).date().isoformat()
        dates.append(day)
        events=range_events(frames,5)+range_events(frames,15)
        counts=defaultdict(int)
        for e in events:counts[f"n{e['range_minutes']}_{e['type']}"]+=1
        for k,v in counts.items():diagnostic[k]+=v
        at_index={frame.timestamp+timedelta(minutes=1):i
                  for i,frame in enumerate(frames)}
        candidates=[]
        for e in events:
            signal=datetime.fromisoformat(e["signal_at"])
            idx=at_index.get(signal)
            if idx is None or idx<5:continue
            choice=_select_entry(frames,signal,e["right"])
            if choice is None:
                diagnostic["entry_quote_unavailable"]+=1
                continue
            q_idx,quote=choice
            observed=frames[q_idx].timestamp+timedelta(minutes=1)
            quality=candidate_quality(frames,idx,quote,e["right"])
            candidates.append({
                "event":e,"frames":frames,"day":day,
                "quote":quote,"quote_index":q_idx,
                "quality":quality,"symbol":quote.symbol,
                "entry_time":observed,
            })
        diagnostic["total_candidate_events"]+=len(events)
        diagnostic["quoted_candidate_events"]+=len(candidates)
        for arm in POLICIES:
            ledger.extend(schedule_trades(candidates,arm,diagnostic))
        diagnostic["completed_frames"]+=len(frames)
    dates=sorted(set(dates))
    outcomes={
        arm:{
            "all":metrics(ledger,dates,arm,"all_inspected"),
            "regime_breakdown":{p:metrics(ledger,dates,arm,p) for p in REGIMES[:-1]},
            "robustness":block_test(ledger,dates,arm),
        } for arm in POLICIES
    }
    report={
        "title":"5m/15m opening CLOSE-range retest, volatility-aware filters, frequency frontier",
        "mode":"RESEARCH_ONLY_NO_LIVE_PAPER_OR_FUNDED_ACCOUNTS",
        "trading_days":len(dates),
        "entry_time_buckets":"09:35-11:00 ET, optional 09:35-15:25 ET extension",
        "actual_high_low_OHLCV_available":False,
        "actual_point_in_time_full_chain_PCR_available":False,
        "event_time_executable_option_NBBO_available":False,
        "true_account_weekly_returns_available":False,
        "fresh_untouched_holdout":False,
        "already_inspected_sessions":True,
        "cost_purchases":0,"orders_submitted":0,
        "exit":{"frozen_30pct_trail":True,
                "independent_35pct_initial_loss_stop":True,
                "max_hold_minutes":FROZEN_MAX_MIN,
                "fixed_profit_target":False,"partial_exits":False},
        "entry_policy":list(POLICIES),
        "coverage":dict(diagnostic),
        "results":outcomes,
        "cautions":[
            "5m/15m opening boundaries are first completed CLOSE extremes, not true OHLC highs/lows.",
            "Historical option CBBO-1m buckets do not establish causal executable NBBO fill timestamps.",
            "Observed max hold exit buckets may miss true intraminute stop hits.",
            "No funded equity/contract affordability, settlement cash recycling, or true weekly percentage P&L.",
            "High signal count does NOT mean profitable signal count; multiple comparisons and snooping exist.",
            "Data and existing development/validation/diagnostic partitions were repeatedly inspected.",
            "IV/RV ratio and past-five-minute delta movement are heuristics, NOT an implied option mispricing forecast.",
            "Live broker path unchanged; all new setups remain disabled outside research.",
        ],
    }
    destination.mkdir(parents=True,exist_ok=True)
    (destination/"report.json").write_text(json.dumps(report,sort_keys=True,indent=2)+"\n")
    columns=["day","arm","signal_at","entry_observation_at","exit_at","exit_reason",
             "symbol","right","range_minutes","signal_type","volume_ratio",
             "pnl_usd","premium_return_pct","entry_debit_usd",
             "quality_iv_rv_ratio","quote_spread_frac"]
    with (destination/"simulated_trades.csv").open("w",newline="") as f:
        writer=csv.DictWriter(f,fieldnames=columns)
        writer.writeheader();writer.writerows(ledger)
    print("RETEST_FREQUENCY_RESULT "+json.dumps(report,sort_keys=True),flush=True)
    return report


def main()->None:
    p=argparse.ArgumentParser()
    p.add_argument("--data-dir",type=Path,default=Path("/data/research"))
    p.add_argument("--output-dir",type=Path,default=Path("/data/research/or_retest_frequency"))
    a=p.parse_args()
    run(a.data_dir,a.output_dir)


if __name__=="__main__":
    main()
