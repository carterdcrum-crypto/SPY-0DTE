"""Exploratory SPY 0DTE REGIME RECLAIM and FAILURE REVERSAL strategy.

Research hypotheses inspired by published intraday ETF momentum and reversal
patterns (Gao et al 2018; Iwanaga & Sakemoto 2026), NOT an empirical claim
of 0DTE option profitability or a proven high win rate.

Signal features: observed COMPLETED SPY 1m closes and volume ratios ONLY.
Quotes: archived CBBO 1m minute buckets, NOT event-time executable NBBO.
Entry: first eligible option quote observed 1-2 minutes after signal.
Exits: unchanged 30% trailing + independent 35% hard loss + max 10m,
NO fixed take profits and NO partial sells.

One-contract settled-cash $1000 T+1 simulation enforces one open position
at a time, reserves premium at entry and cannot re-use same-day sale
proceeds. NO broker orders, live toggle, external purchases.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
from collections import defaultdict, Counter
from datetime import datetime,timedelta,time
from pathlib import Path

from .burst_research import _research_files
from .data import load_canonical_path
from .exit_policy_comparison import _select_entry,_sampled_path
from .free_signal_screen import ET,_bps
from .trailing_only_sweep import simulate,period

START_CASH=1000.0
TRAIL_PCT=30
HARD_STOP_PCT=35
MAX_HOLD_MIN=10
FEE_STRESS_PER_TRADE=2.0
AFTER_OPEN=time(9,52)
MORNING_END=time(11,0)
DAY_END=time(15,25)
ARMS=(
    "trend_reclaim",
    "trend_reclaim_volume_150",
    "trend_reclaim_cost_aware",
    "stretch_reclaim",
    "failed_range_reentry",
    "blend_trend_or_stretch",
    "blend_trend_or_stretch_cost_aware",
    "trend_reclaim_morning",
)
SETUPS=("trend_reclaim","stretch_reclaim","failed_range_reentry")
PERIODS=("development","validation","previously_studied_diagnostic","all_inspected")


def ema(values:list[float],period:int)->float:
    if not values or period<2:raise ValueError("missing EMA data")
    alpha=2./(period+1.)
    e=values[0]
    for x in values[1:]:
        e=alpha*x+(1-alpha)*e
    return e


def contiguous(frames,i:int,n:int)->bool:
    return i>=n and all(
        (frames[k].timestamp-frames[k-1].timestamp).total_seconds()==60
        for k in range(i-n+1,i+1)
    )


def identify_setups(frames)->list[dict]:
    """Use information as-of signal bar close. No option outcome/exit data."""
    out=[]
    spots=[x.market.spot for x in frames]
    for i in range(35,len(frames)):
        signal=frames[i].timestamp+timedelta(minutes=1)
        et=signal.astimezone(ET).time()
        if et<AFTER_OPEN or et>=DAY_END:
            continue
        if not contiguous(frames,i,35):
            continue
        recent=spots[i-35:i+1]
        if not all(math.isfinite(x) and x>0 for x in recent):
            continue
        rvol=frames[i].market.volume_ratio
        if not math.isfinite(rvol) or rvol<.75:continue
        current=spots[i];prev=spots[i-1]
        # EMA recomputed on 35 completed bars; previous EMA excludes latest.
        fast=ema(recent[:-1],8)
        slow=ema(recent[:-1],21)
        slow10=ema(recent[:-11],21)
        newfast=ema(recent,8)
        trend=_bps(slow,slow10)
        twenty=spots[i-20:i]
        rng=(max(twenty)-min(twenty))
        path=sum(abs(spots[j]-spots[j-1]) for j in range(i-14,i+1))
        efficiency=abs(spots[i]-spots[i-15])/path if path>0 else 0.
        # Direction-confirmed pullback: previous close under/over the
        # fast EMA, latest close reclaims it within a slower EMA trend.
        trend_side=None
        if (trend>=2. and prev<=fast and current>newfast and
            current>slow and current>prev and efficiency>=.18):
            trend_side="call"
        elif (trend<=-2. and prev>=fast and current<newfast and
              current<slow and current<prev and efficiency>=.18):
            trend_side="put"
        if trend_side is not None:
            out.append({"at":signal,"side":trend_side,"setup":"trend_reclaim",
                        "volume_ratio":rvol,"trend_bps":round(trend,3),
                        "efficiency":round(efficiency,4)})
        # Exhaustion/reversion: prior *completed* close moved outside
        # a PREVIOUS 20-bar +/- 1.2std band, current closes back inside.
        # The reference band is frozen prior to previous completed close.
        base=spots[i-22:i-2]
        avg=statistics.fmean(base)
        sigma=statistics.pstdev(base)
        if sigma>0:
            low=avg-1.2*sigma;high=avg+1.2*sigma
            stretch=None
            if prev<low and current>low and current>prev and efficiency<.60:
                stretch="call"
            elif prev>high and current<high and current<prev and efficiency<.60:
                stretch="put"
            if stretch is not None:
                out.append({"at":signal,"side":stretch,"setup":"stretch_reclaim",
                            "volume_ratio":rvol,"trend_bps":round(trend,3),
                            "efficiency":round(efficiency,4)})
        # Failed close-range breakout: previous candle was beyond its
        # pre-existing 20 minute extreme, latest closes BACK inside.
        reference=spots[i-22:i-2]
        high=max(reference);low=min(reference)
        failed=None
        if prev>high and current<high and current<prev:
            failed="put"
        elif prev<low and current>low and current>prev:
            failed="call"
        if failed:
            out.append({"at":signal,"side":failed,"setup":"failed_range_reentry",
                        "volume_ratio":rvol,"trend_bps":round(trend,3),
                        "efficiency":round(efficiency,4)})
    return out


def asof_cost_ok(quote,market)->bool:
    """Observable entry-time heuristic, not a guaranteed edge."""
    ivrv=quote.implied_volatility/market.realized_volatility if market.realized_volatility>0 else math.inf
    return quote.spread_fraction<=.035 and ivrv<=1.5


def accept(candidate:dict,arm:str)->bool:
    if arm not in ARMS:raise ValueError("unknown research arm")
    setup=candidate["event"]["setup"]
    vol=candidate["event"]["volume_ratio"]
    cost=asof_cost_ok(candidate["quote"],candidate["quote_market"])
    et=candidate["event"]["at"].astimezone(ET).time()
    return {
        "trend_reclaim":setup=="trend_reclaim",
        "trend_reclaim_volume_150":setup=="trend_reclaim" and vol>=1.5,
        "trend_reclaim_cost_aware":setup=="trend_reclaim" and cost,
        "stretch_reclaim":setup=="stretch_reclaim",
        "failed_range_reentry":setup=="failed_range_reentry",
        "blend_trend_or_stretch":setup in ("trend_reclaim","stretch_reclaim"),
        "blend_trend_or_stretch_cost_aware":setup in ("trend_reclaim","stretch_reclaim") and cost,
        "trend_reclaim_morning":setup=="trend_reclaim" and et<MORNING_END,
    }[arm]


def get_quote_candidates(frames,events,diag)->list[dict]:
    observations=[]
    for event in events:
        option=_select_entry(frames,event["at"],event["side"])
        if option is None:
            diag["no_quote_at_entry"]+=1
            continue
        at_index,q=option
        first_obs=frames[at_index].timestamp+timedelta(minutes=1)
        # Actual 0DTE quote stream lacks executable quote timestamps.
        observations.append({
            "event":event,"option_index":at_index,
            "quote":q,"quote_market":frames[at_index].market,
            "entry_at":first_obs,"entry_debit_usd":round((q.ask+.01)*100+.65,2),
            "symbol":q.symbol,
        })
    return sorted(observations,key=lambda c:(
        c["entry_at"],0 if c["event"]["setup"]=="trend_reclaim" else
        (1 if c["event"]["setup"]=="stretch_reclaim" else 2),c["symbol"]))


def run_one_session(frames,candidates,arm:str,
                    cash:float,diagnostic:Counter)->tuple[list[dict],float,float]:
    """Cash represents settled opening funds. Returns cash + unsettled proceeds.

    Same-day liquidation proceeds NEVER fund subsequent entries.
    Outcome labels are read ONLY after a candidate is admitted.
    """
    accepted=[]
    unsettled=0.
    flat_at=None
    for c in candidates:
        if not accept(c,arm):
            continue
        if flat_at is not None and c["entry_at"]<=flat_at:
            diagnostic[arm+"_position_busy"]+=1
            continue
        debit=c["entry_debit_usd"]
        if debit>cash+1e-8:
            diagnostic[arm+"_insufficient_settled_cash"]+=1
            continue
        # Trading debit and next-day proceeds are modeled only.
        cash-=debit
        diagnostic[arm+"_accepted_entry"]+=1
        trace=_sampled_path(frames,c["option_index"],c["symbol"])
        if trace is None:
            # Recredit the cost on the same day because NO trade was
            # evaluated; lock to prevent re-selection using future quote
            # completeness (conservative gap handling).
            cash+=debit
            flat_at=c["entry_at"]+timedelta(minutes=MAX_HOLD_MIN)
            diagnostic[arm+"_missing_exit_quote"]+=1
            continue
        outcome=simulate(c["quote"].ask,trace,TRAIL_PCT)
        pnl=float(outcome["pnl_usd"])
        proceeds=max(0.,debit+pnl)
        unsettled+=proceeds
        flat_at=datetime.fromisoformat(outcome["exit_time"])
        accepted.append({
            "day":frames[0].timestamp.astimezone(ET).date().isoformat(),
            "arm":arm,"setup":c["event"]["setup"],
            "side":c["event"]["side"],
            "signal_at":c["event"]["at"].isoformat(),
            "entry_at":c["entry_at"].isoformat(),
            "exit_at":outcome["exit_time"],
            "symbol":c["symbol"],
            "entry_debit_usd":debit,
            "pnl_usd":pnl,"exit_reason":outcome["exit_reason"],
            "premium_return_pct":outcome["return_on_premium_pct"],
            "relative_volume":c["event"]["volume_ratio"],
            "entry_ask":c["quote"].ask,
            "entry_spread_fraction":c["quote"].spread_fraction,
        })
    return accepted,cash,unsettled


def summarize(trades:list[dict],sessions:list[str],arm:str,part:str)->dict:
    active_dates=[d for d in sessions if part=="all_inspected" or period(d)==part]
    rows=[t for t in trades if t["arm"]==arm and (
        part=="all_inspected" or period(t["day"])==part)]
    pnl=[t["pnl_usd"] for t in rows]
    wins=[p for p in pnl if p>0]
    losses=[p for p in pnl if p<=0]
    counts=Counter(t["day"] for t in rows)
    daily=defaultdict(float)
    for t in rows:daily[t["day"]]+=t["pnl_usd"]
    return {
        "sessions":len(active_dates),
        "trades":len(rows),
        "trades_per_session":round(len(rows)/len(active_dates),3) if active_dates else None,
        "active_sessions":sum(counts[d]>0 for d in active_dates),
        "days_with_2plus_trades":sum(counts[d]>=2 for d in active_dates),
        "days_with_3plus_trades":sum(counts[d]>=3 for d in active_dates),
        "max_trades_in_one_day":max((counts[d] for d in active_dates),default=0),
        "win_rate_pct":round(100*len(wins)/len(pnl),2) if pnl else None,
        "mean_winner_usd":round(statistics.fmean(wins),2) if wins else None,
        "mean_loser_usd":round(statistics.fmean(losses),2) if losses else None,
        "sum_one_contract_pnl_usd":round(sum(pnl),2),
        "extra_two_dollars_per_trade_stressed_sum_usd":
            round(sum(pnl)-FEE_STRESS_PER_TRADE*len(pnl),2),
        "positive_pnl_days":sum(daily[d]>0 for d in active_dates),
        "negative_pnl_days":sum(daily[d]<0 for d in active_dates),
    }


def cash_summary(daily:list[dict],starting_cash:float)->dict:
    if not daily:return {"ending_equity_usd":starting_cash}
    equities=[starting_cash]+[x["equity_usd"] for x in daily]
    peak=starting_cash
    maxdd=0.
    for x in equities[1:]:
        peak=max(x,peak)
        maxdd=max(maxdd,peak-x)
    weeks={}
    prev=starting_cash
    for day in daily:
        week=str(datetime.fromisoformat(day["day"]).isocalendar()[:2])
        w=weeks.setdefault(week,{"starting_equity":prev,"ending_equity":None})
        w["ending_equity"]=day["equity_usd"]
        prev=day["equity_usd"]
    return {
        "starting_settled_cash_usd":starting_cash,
        "ending_equity_usd":round(equities[-1],2),
        "account_profit_loss_usd":round(equities[-1]-starting_cash,2),
        "total_return_pct":round(100*(equities[-1]/starting_cash-1),2),
        "max_end_of_day_drawdown_usd":round(maxdd,2),
        "max_end_of_day_drawdown_pct_initial_capital":round(
            maxdd/starting_cash*100,2),
        "weeks_doubled":sum(v["starting_equity"]>0 and
             v["ending_equity"]>=2*v["starting_equity"] for v in weeks.values()),
        "weeks":len(weeks),
        "post_hoc_path_uses_modeled_quote_fills":True,
    }


def run(data_dir:Path,out_dir:Path)->dict:
    paths=_research_files(data_dir)
    states={arm:{"settled":START_CASH,"unsettled":0.,"daily":[]} for arm in ARMS}
    trades=[]
    days=[]
    diag=Counter()
    for filename in paths:
        frames=list(load_canonical_path(filename))
        if not frames:continue
        day=frames[0].timestamp.astimezone(ET).date().isoformat()
        days.append(day)
        events=identify_setups(frames)
        for e in events:diag[e["setup"]+"_raw_signal"]+=1
        quotes=get_quote_candidates(frames,events,diag)
        diag["minute_frames"]+=len(frames)
        diag["quoted_candidates"]+=len(quotes)
        for arm in ARMS:
            state=states[arm]
            state["settled"]+=state["unsettled"]  # previous session settled
            state["unsettled"]=0.
            accepted,settled,unsettled=run_one_session(
                frames,quotes,arm,state["settled"],diag)
            state["settled"]=settled
            state["unsettled"]=unsettled
            equity=round(settled+unsettled,4)
            state["daily"].append({"day":day,"equity_usd":equity,
                                   "trades":len(accepted)})
            trades.extend(accepted)
    days=sorted(set(days))
    report={
        "study":"Research SPY options high-win-rate hypotheses: trend EMA reclaim, intraday stretch reversal, failed range",
        "status":"RESEARCH_EXPLORATION_NOT_LIVE_AND_NOT_NEW_HOLDOUT",
        "source_research":["Gao et al JFE 2018 intraday momentum SPY",
                           "Iwanaga and Sakemoto 2026 overnight reversal US ETF",
                           "Cboe 0DTE risks and bid-ask spreads"],
        "requested_goal":"high win rate but MUST measure actual net option profit after costs",
        "start_capital_usd":START_CASH,
        "settlement":"simplified T+1, no same-day proceeds recycling",
        "exits":{"trailing_stop_pct":TRAIL_PCT,"hard_loss_pct":HARD_STOP_PCT,
                 "max_hold_minutes":MAX_HOLD_MIN,"fixed_take_profit":False,
                 "partial_exits":False},
        "market_sessions":len(days),
        "historical_minute_bar_close_only":True,
        "underlying_true_OHLCV_unavailable":True,
        "executable_options_event_NBBO_unavailable":True,
        "full_chain_pcr_unavailable":True,
        "not_untouched_future_data":True,
        "archive_is_previously_inspected":True,
        "tests_exploratory_not_multiple_comparison_corrected":True,
        "arm_names":ARMS,
        "signal_and_liquidity_coverage":dict(diag),
        "results":{a:{
            "all_archived_sessions":summarize(trades,days,a,"all_inspected"),
            "historical_partition_summary":{
                p:summarize(trades,days,a,p) for p in PERIODS[:-1]},
            "illustrative_1000_usd_settled_cash":cash_summary(states[a]["daily"],START_CASH),
        } for a in ARMS},
        "warnings":[
            "128 previously studied sessions: no new holdout and high winning arm could be multiple-test selection bias.",
            "SPY close-only signals are not true OHLC/VWAP reversal or actual opening-range entries.",
            "1m CBBO option quotes cannot prove event-time executable fills, spreads, or accurate intraminute stops.",
            "Account simulation one at a time and holds same-day sales until next date; does not model actual broker fees beyond existing option fill assumptions or exercise/assignment.",
            "Increasing win rate can decrease profitability when loss size is large; demand after-cost expectancy and genuine new unseen results.",
        ],
        "orders":0,"data_purchased":0,
    }
    out_dir.mkdir(parents=True,exist_ok=True)
    (out_dir/"report.json").write_text(json.dumps(report,sort_keys=True,indent=2)+"\n")
    with (out_dir/"trades.csv").open("w",newline="") as f:
        cols=("day","arm","setup","side","signal_at","entry_at","exit_at",
              "symbol","entry_debit_usd","pnl_usd","exit_reason",
              "premium_return_pct","relative_volume","entry_ask","entry_spread_fraction")
        w=csv.DictWriter(f,fieldnames=cols);w.writeheader();w.writerows(trades)
    print("REGIME_RECLAIM_RESULT "+json.dumps(report,sort_keys=True),flush=True)
    return report


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--data-dir",type=Path,default=Path("/data/research"))
    p.add_argument("--output-dir",type=Path,default=Path("/data/research/regime_reclaim"))
    a=p.parse_args()
    run(a.data_dir,a.output_dir)


if __name__=="__main__":
    main()
