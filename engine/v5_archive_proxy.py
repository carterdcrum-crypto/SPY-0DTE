"""Exploratory SPY V5 close-only proxy on already inspected Railway CBBO archive.

THIS IS NOT A TRUE V5 OHLCV BACKTEST: historical SPY high/low, full bar
volume, bid/ask depth and event NBBO are absent. Never impute missing highs
or lows from close. V5 true implementation remains BLOCKED_MISSING_OHLCV.
No market orders, broker access, new paid queries, production policy changes.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import json
import math
import statistics
from collections import Counter
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

from .burst_research import _research_files
from .data import load_canonical_path
from .regime_hold_sensitivity import START_CASH, MAX_FIXED_MIN, liquidation_cutoff, quote_path
from .regime_hold_conditional import simulate_conditional
from .v4_quality_sizing import score_at_entry, size as v4_size
from .v4_dynamic_size_backtest import summary
from .regime_reclaim_frozen_signal import ema
from .weekly_options_data import NY
from quantconnect.v5_core import Signal, quality_score

ARMS = ("v4_quality_3x", "v5_close_proxy_current",
        "v5_close_proxy_challenger", "v5_close_proxy_unrestricted_RESEARCH_ONLY")
FEE = .65
SLIP = .01
SETUP_SPLITS = {
    "momentum_ignition": "momentum_ignition_CLOSE_PROXY",
    "liquidity_reversal": "liquidity_reversal_CLOSE_PROXY",
    "volatility_expansion": "volatility_expansion_CLOSE_PROXY",
}


def close_proxy_signals(frames, i, *, challenger=False,
                        reversal_lookback=20, reversal_max_efficiency=.60):
    """Close-only alternatives, NOT the QC V5 high/low/volume setup.

    Close range breakout is NOT a replacement for true intraminute high/low.
    RVOL reads the as-of archive's volume_ratio, not made-up bar volume.
    Default reversal parameters remain frozen for historical replay; only
    production V5 passes the expressly selected 15-close / 0.75 variant.
    """
    if not (3 <= reversal_lookback <= 30 and
            0 < reversal_max_efficiency <= 1):
        raise ValueError("invalid reversal-variant parameters")
    if i < 35:
        return ()
    today=frames[i].timestamp.astimezone(NY).date()
    window=frames[i-35:i+1]
    if any(f.timestamp.astimezone(NY).date()!=today or
           not math.isfinite(f.market.spot) or f.market.spot<=0
           for f in window):
        return ()
    if any((b.timestamp-a.timestamp).total_seconds()!=60
           for a,b in zip(window,window[1:])):
        return ()
    rvol=frames[i].market.volume_ratio
    if rvol<(1.25 if challenger else 1.5):
        return ()
    spots=[f.market.spot for f in frames]
    recent=spots[i-35:i+1]
    now=spots[i];prev=spots[i-1]
    fast=ema(recent,8)
    slow=ema(recent[:-1],21)
    older_slow=ema(recent[:-11],21)
    trend=(slow/older_slow-1)*1e4 if older_slow>0 else 0.
    distance=sum(abs(spots[j]-spots[j-1]) for j in range(i-14,i+1))
    eff=abs(now-spots[i-15])/distance if distance>0 else 0.
    momentum=(now/spots[i-3]-1)*1e4
    events=[]
    def add(side,setup):
        events.append(Signal(side,setup,rvol,trend,eff))

    # Close-based prior 12-minute range instead of unavailable bar highs.
    if eff >= (.24 if challenger else .28):
        if (now>max(spots[i-12:i]) and fast>slow and trend>=2 and momentum>=3):
            add("call","momentum_ignition_CLOSE_PROXY")
        elif (now<min(spots[i-12:i]) and fast<slow and trend<=-2 and momentum<=-3):
            add("put","momentum_ignition_CLOSE_PROXY")
    old=spots[i-reversal_lookback-2:i-2]
    if (prev>max(old) and now<max(old) and now<prev
            and eff<reversal_max_efficiency):
        add("put","liquidity_reversal_CLOSE_PROXY")
    elif (prev<min(old) and now>min(old) and now>prev
            and eff<reversal_max_efficiency):
        add("call","liquidity_reversal_CLOSE_PROXY")
    wide=max(spots[i-32:i-8])-min(spots[i-32:i-8])
    narrow=max(spots[i-8:i])-min(spots[i-8:i])
    if wide>0 and narrow/wide<=(.75 if challenger else .60):
        if now>max(spots[i-8:i]) and now>prev and momentum>=2 and fast>slow:
            add("call","volatility_expansion_CLOSE_PROXY")
        elif now<min(spots[i-8:i]) and now<prev and momentum<=-2 and fast<slow:
            add("put","volatility_expansion_CLOSE_PROXY")
    return tuple(events)


def proxy_candidates(frames, *, challenger):
    times=[f.timestamp+timedelta(minutes=1) for f in frames]
    entries=[]
    for i in range(35,len(frames)):
        signals=close_proxy_signals(frames,i,challenger=challenger)
        if not signals:
            continue
        event_time=times[i]
        for sig in signals:
            # Same original archive's one-to-two-minute delayed entry
            # and cheapest selection process; no in-signal bar option fills.
            start=event_time+timedelta(minutes=1)
            end=event_time+timedelta(minutes=2)
            idx=bisect.bisect_left(times,start)
            chosen=None
            for j in range(idx,len(frames)):
                if times[j]>end:
                    break
                qlist=[q for q in frames[j].options
                       if q.right==sig.side and q.bid>0 and q.ask>q.bid
                       and .40<=abs(q.delta)<=.60
                       and q.spread_fraction<=.15]
                if qlist:
                    q=min(qlist,key=lambda q:(abs(abs(q.delta)-.50),
                                             q.spread_fraction,
                                             abs(q.strike-frames[j].market.spot),
                                             q.symbol))
                    chosen=(j,q)
                    break
            if chosen is None:
                continue
            j,q=chosen
            market=frames[j].market
            if (q.spread_fraction>.03 or market.realized_volatility<=0 or
                    q.implied_volatility<=0 or
                    q.implied_volatility/market.realized_volatility>1.5):
                continue
            score=quality_score(sig,q.spread_fraction,q.delta,
                                q.implied_volatility,market.realized_volatility)
            item={"signal": {"at":event_time,"side":sig.side,
                             "setup":sig.setup,"volume_ratio":sig.rvol,
                             "trend_bps":sig.trend_bps,
                             "efficiency":sig.efficiency},
                  "entry_idx":j,"entry_at":times[j],"quote":q,
                  "score":score,
                  "debit":round((q.ask+SLIP)*100+FEE,2)}
            entries.append(item)
    return sorted(entries,key=lambda x:(x["entry_at"], x["quote"].symbol))


def proxy_size(arm, *, equity, settled, peak, debit, score, starting_cash=START_CASH):
    """Cash-premium research budget only; NBBO sizes missing in legacy CSV.

    All returned quantities are NON-EXECUTABLE fill hypotheses.
    """
    if min(equity,settled,peak,debit)<=0:
        return 0
    cash=int((settled+1e-8)//debit)
    if arm.endswith("unrestricted_RESEARCH_ONLY"):
        return cash
    if score<(3 if arm.endswith("challenger") else 4):
        return 0
    drawdown=max(0.,1-equity/peak)
    if drawdown>=.08:
        return 0
    fraction=(.25 if arm.endswith("challenger") else .20)
    if score>=5:
        fraction=(.35 if arm.endswith("challenger") else .30)
    if drawdown>=.04:
        fraction*=.25
    elif drawdown>=.02:
        fraction*=.50
    riskable=max(0.,equity-.25*max(0.,equity-starting_cash))
    return max(0,min(cash,int((min(.35*equity,fraction*riskable)+1e-8)//debit)))


def proxy_exit(arm,entry,frames,cutoff):
    """Real sampled option bids, research-only simulated realized exits.

    Exit on hard stop or peak trailing stop, close proxy reversal and timer.
    Unscorable terminal records are NOT silently credited.
    """
    entry_at=entry["entry_at"]
    q=entry["quote"]
    debit=entry["debit"]
    peak=debit
    due=entry_at+timedelta(minutes=18 if arm.endswith("challenger")
                            or "unrestricted" in arm else 12)
    path=quote_path(frames,entry["entry_idx"],q.symbol,cutoff)
    times=[f.timestamp+timedelta(minutes=1) for f in frames]
    for at,bid in path:
        if at<=entry_at or at>cutoff:
            continue
        credit=max(0.,(bid-SLIP)*100-FEE)
        peak=max(peak,credit)
        if credit<=debit*.65+1e-9:
            reason="hard_stop_35pct"
        elif credit<=peak*(.75 if arm.endswith("challenger") or
                            "unrestricted" in arm else .70)+1e-9:
            reason="trailing_stop"
        else:
            ix=bisect.bisect_right(times,at)-1
            closes=[f.market.spot for f in frames]
            if ix>=4:
                direction=1 if entry["signal"]["side"]=="call" else -1
                momentum=direction*(closes[ix]/closes[ix-3]-1)
                if momentum<0 and at>=entry_at+timedelta(minutes=10):
                    reason="underlying_reversal_close_proxy"
                elif at>=due:
                    reason="time_exit"
                else:
                    continue
            elif at>=due:
                reason="time_exit"
            else:
                continue
        return {"pnl_usd":round(credit-debit,6),
                "exit_at":at,"exit_reason":reason}
    return None


def first_hit(s, target=5000.):
    return next((i for i,day in enumerate(s["daily"],1)
                 if day["equity_usd"]>=target),None)


def rolling_42(s, starting_cash=START_CASH):
    eq=[starting_cash]+[x["equity_usd"] for x in s["daily"]]
    windows=[]
    for start in range(0,len(eq)-42):
        opening=eq[start]
        if opening>0:
            windows.append(eq[start+42]/opening)
    return {"windows":len(windows),
            "5x_windows":sum(x>=5 for x in windows),
            "halving_windows":sum(x<=.5 for x in windows),
            "fivefold_pct":round(100*sum(x>=5 for x in windows)/len(windows),2)
                if windows else None}


def run(data_dir:Path, output_dir:Path, *, starting_cash=START_CASH, arms=ARMS, split_setups=False):
    if not math.isfinite(starting_cash) or starting_cash <= 0 or round(starting_cash, 2) != starting_cash:
        raise ValueError("starting_cash must be a positive dollar-and-cent amount")
    if not arms or any(arm not in ARMS for arm in arms):
        raise ValueError("unknown or missing research arm")
    if split_setups and tuple(arms)!=(ARMS[-1],):
        raise ValueError("split_setups requires only unrestricted V5 research arm")
    # Fully separate accounts, not profit attribution within the combined arm.
    policies={arm:(arm,None) for arm in arms}
    if split_setups:
        policies.update({f"{ARMS[-1]}__{label}":(ARMS[-1],setup)
                         for label,setup in SETUP_SPLITS.items()})
    paths=_research_files(data_dir)
    if not paths:
        raise ValueError("Railway research archive absent; no fabricated history")
    states={name:{"settled":starting_cash,"pending":0.,"daily":[],
                  "trades":[],"skips":Counter(),"exits":Counter()}
            for name in policies}
    names=[]
    diagnostic={"missing_spy_high":True,"missing_spy_low":True,
                "missing_completed_spy_volume":True,
                "missing_option_bid_ask_sizes":True,
                "event_nbbo_not_verified":True,
                "strict_v5_quote_fills_executable":False}
    for file in paths:
        frames=list(load_canonical_path(file))
        if not frames:
            continue
        day=frames[0].timestamp.astimezone(NY).date()
        names.append(str(day))
        cutoff=liquidation_cutoff(day)
        # Reuse original exact V4 3x selection (unchanged) for benchmark.
        from .regime_hold_sensitivity import selection
        original=selection(frames)
        proxy_regular=proxy_candidates(frames,challenger=False)
        proxy_challenger=proxy_candidates(frames,challenger=True)
        for label,s in states.items():
            arm,setup_filter=policies[label]
            s["settled"]+=s["pending"]
            s["pending"]=0.
            opening=s["settled"]
            peak=max([starting_cash]+[d["equity_usd"] for d in s["daily"]])
            busy=None
            candidates=(original if arm=="v4_quality_3x" else
                        proxy_regular if arm=="v5_close_proxy_current" else
                        proxy_challenger)
            for item in candidates:
                if setup_filter is not None and item["signal"]["setup"]!=setup_filter:
                    continue
                at=item["entry_at"]
                if at>=cutoff-timedelta(minutes=MAX_FIXED_MIN+1):
                    s["skips"]["LATE_ENTRY"]+=1
                    continue
                if busy is not None and at<=busy:
                    s["skips"]["BUSY"]+=1
                    continue
                q=item["quote"]
                debit=item["debit"]
                if arm=="v4_quality_3x":
                    score=score_at_entry(item,frames)
                    qty=v4_size(arm,score=score,equity=opening,
                                settled=s["settled"],peak=peak,debit=debit)
                else:
                    score=item["score"]
                    qty=proxy_size(arm,equity=opening,settled=s["settled"],
                                   peak=peak,debit=debit,score=score,starting_cash=starting_cash)
                if qty<=0:
                    s["skips"]["CASH_QUALITY_OR_SIZE"]+=1
                    continue
                if qty*debit>s["settled"]+1e-8:
                    raise AssertionError("T+1 cash requirement violated")
                if arm=="v4_quality_3x":
                    outcome=simulate_conditional(q.ask,
                        quote_path(frames,item["entry_idx"],q.symbol,cutoff),
                        frames,entry_at=at,side=item["signal"]["side"],
                        symbol=q.symbol,cutoff=cutoff)
                else:
                    outcome=proxy_exit(arm,item,frames,cutoff)
                s["settled"]=round(s["settled"]-qty*debit,8)
                row={"day":str(day),"arm":label,"setup":item["signal"]["setup"],
                     "contracts":qty,"quality_score":score,
                     "entry_time":at.isoformat(),"symbol":q.symbol,
                     "entry_debit_usd":debit}
                if outcome is None:
                    busy=cutoff
                    s["trades"].append({**row,"pnl_usd":None,
                        "exit_reason":"NO_VERIFIED_TERMINAL_BID_PREMIUM_RESERVED"})
                    continue
                pnl=round(qty*outcome["pnl_usd"],6)
                s["pending"]+=max(0.,qty*debit+pnl)
                busy=outcome["exit_at"]
                s["exits"][outcome["exit_reason"]]+=1
                s["trades"].append({**row,"pnl_usd":pnl,
                    "exit_time":outcome["exit_at"].isoformat(),
                    "exit_reason":outcome["exit_reason"]})
            s["daily"].append({"day":str(day),
                "equity_usd":round(s["settled"]+s["pending"],4)})
    reports={}
    for label,s in states.items():
        reports[label]={**summary(s,starting_cash=starting_cash),"first_5k_session":first_hit(s),
                      "first_5x_session":first_hit(s,target=round(starting_cash*5,2)),
                      "rolling_42_session_proxy":rolling_42(s,starting_cash=starting_cash)}
    report={
        "study":"Railway V5 close-only exploratory proxies versus original V4 3x",
        "status":"REUSED_IN_SAMPLE_CBBO_MINUTE_CLOSE_ONLY_PROXY_NOT_TRUE_V5",
        "truthful_strict_v5_status":"BLOCKED_MISSING_REAL_SPY_OHLCV_AND_OPTION_DEPTH",
        "sessions":len(names),"first_day":min(names),"last_day":max(names),
        "start_cash_usd":starting_cash,
        "tested_arms":list(policies),
        "split_setups":split_setups,
        "split_method":"Independent all-cash T+1 replays with only that entry signal; no changes to shared exits, spreads, slippage or contract selection." if split_setups else None,
        "source":"Previously inspected SPY close/volume-ratio and CBBO-1m archive",
        "entry_execution":"option ASK+0.01/share+$0.65/contract; simulated exit sampled BID-0.01/share-$0.65/contract",
        "settlement":"one-session proceeds delay; no intraday option sale reuse",
        "contracts":"option multiplier 100; no NBBO size, quantity is a hypothetical proxy only",
        "max_drawdown":"EOD equity only; intraday NBBO unavailable",
        "cost_stress":"summary modeled_pnl_after_extra_2usd_each_contract_usd",
        "archive_has_verified_ohlcv":False,
        "no_paid_data":True,"no_broker_orders":True,
        "data_gaps":diagnostic,
        "lookahead":"closed SPY observations only; subsequent minute-bucket option quotes",
        "independence":"No genuinely independent holdout; entire archive repeatedly inspected",
        "caveats":[
            "These are NOT the full QuantConnect V5 signals. Missing high/low bars are NOT reconstructed.",
            "V5 close-range trend/compression signals use observed closes ONLY and are separately identified.",
            "Legacy archive has no executable event-time NBBO or bid/ask depth. Gross fills and high quantities may be unachievable.",
            "No reliable intraday drawdown or forward risk-of-ruin probability.",
            "Compare historical variants as hypotheses; do not promote any V5 variant to LIVE."
        ],
        "results":reports,
    }
    output_dir.mkdir(parents=True,exist_ok=True)
    (output_dir/"report.json").write_text(json.dumps(report,sort_keys=True,indent=2)+"\n")
    with (output_dir/"trades.csv").open("w",newline="") as f:
        rows=[row for s in states.values() for row in s["trades"]]
        cols=sorted({"day","arm","pnl_usd"}|{k for row in rows for k in row})
        w=csv.DictWriter(f,fieldnames=cols);w.writeheader();w.writerows(rows)
    with (output_dir/"daily_equity.csv").open("w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=["arm","day","equity_usd"])
        w.writeheader()
        for arm,s in states.items():
            w.writerows({"arm":arm,**d} for d in s["daily"])
    print("RAILWAY_V5_CLOSE_PROXY_RESULT "+json.dumps(report,sort_keys=True),
          flush=True)
    return report


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--data-dir",type=Path,default=Path("/data/research"))
    p.add_argument("--starting-cash",type=float,default=START_CASH)
    p.add_argument("--arm",choices=ARMS,help="run only one profile; defaults to all four")
    p.add_argument("--split-setups",action="store_true",help="compare all three V5 entry signals independently against combined V5; requires unrestricted --arm")
    p.add_argument("--output-dir",type=Path,
                   default=Path("/data/research/v5_archive_2026_10_14"))
    args=p.parse_args()
    run(args.data_dir,args.output_dir,starting_cash=args.starting_cash,
        arms=(args.arm,) if args.arm else ARMS,split_setups=args.split_setups)


if __name__=="__main__":
    main()
