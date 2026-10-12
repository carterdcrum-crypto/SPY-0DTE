"""Previously inspected 128-session SPY 0DTE historical exit-horizon sensitivity.

Compares the exact frozen Regime Reclaim entry/cost hypothesis under its
ORIGINAL 10-minute trail and a NO-FIXED-MINUTES trailing policy. Does NOT
retune the entry rules, rely on lookahead, place orders, call a paid provider,
or pretend 1-minute CBBO bucket quotes are executable event-time NBBO.
V2 regime-routing/VWAP backtests are impossible on close-only legacy data.

Crucial: missing early/late terminal quotes are UNKNOWN, never a favorable
exit. For a worst-case CASH drawdown scenario, missing-outcome positions
reserve 100% of their debit, with unscored P&L reported separately.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import json
import math
from collections import Counter
from datetime import datetime, timedelta, time
from pathlib import Path

from .burst_research import _research_files
from .data import load_canonical_path
from .regime_reclaim_frozen_signal import identify_setups, simulate
from .session_calendar import session_close
from .regime_reclaim_v2 import deteriorating_structure
from .weekly_options_data import NY

START_CASH=1000.0
PREMIUM_FEE_PER_SIDE=0.65
SLIP_PER_SHARE=0.01
TRAIL_PCT=30
HARD_STOP=.35
MAX_FIXED_MIN=10
CUTOFF_BUFFER_MIN=15
BAR_TOLERANCE_SECONDS=60
ARMS=("frozen_10min","no_max_30pct_trail","no_max_adaptive20pct")


def liquidation_cutoff(day):
    closing=session_close(day)
    if closing is None:
        # Legacy dates 2025 may fall outside our strictly supported 2026+ calendar.
        # Valid US SPY trading data days have a normal 16:00 ET close; never assume
        # an early close outside our known calendar in a production gate.
        if day.year not in (2026,2027,2028) and day.weekday()<5:
            closing=time(16,0)
        else:
            raise ValueError("unknown/closed trading session cannot safely hold to expiry")
    return datetime.combine(day,closing,NY)-timedelta(minutes=CUTOFF_BUFFER_MIN)


def selection(frames):
    """Identical selection order/contract and IV gate as frozen historical arm."""
    events=identify_setups(frames)
    available=[f.timestamp+timedelta(minutes=1) for f in frames]
    choices=[]
    for event in events:
        start=event["at"]+timedelta(minutes=1)
        end=event["at"]+timedelta(minutes=2)
        idx=bisect.bisect_left(available,start)
        chosen=None
        for i in range(idx,len(frames)):
            at=available[i]
            if at>end:
                break
            qualified=[q for q in frames[i].options
                       if q.right==event["side"] and q.bid>0 and q.ask>q.bid
                       and .40<=abs(q.delta)<=.60
                       and q.spread_fraction<=.15]
            if qualified:
                q=min(qualified,key=lambda x:(abs(abs(x.delta)-.5),
                                            x.spread_fraction,
                                            abs(x.strike-frames[i].market.spot),
                                            x.symbol))
                chosen=(i,at,q)
                break
        if chosen is None:
            continue
        i,at,q=chosen
        market=frames[i].market
        if event["setup"] not in ("trend_reclaim","stretch_reclaim"):
            continue
        if q.spread_fraction>.035 or market.realized_volatility<=0:
            continue
        if q.implied_volatility/market.realized_volatility>1.5:
            continue
        choices.append({"signal":event,"entry_idx":i,
                        "entry_at":at,"quote":q,
                        "debit":round((q.ask+SLIP_PER_SHARE)*100+PREMIUM_FEE_PER_SIDE,2)})
    return sorted(choices,key=lambda x:(
        x["entry_at"],0 if x["signal"]["setup"]=="trend_reclaim" else 1,
        x["quote"].symbol))


def quote_path(frames, entry_idx, symbol, until):
    """The only exit prices considered are strictly later observed sampled bids."""
    path=[]
    for f in frames[entry_idx+1:]:
        at=f.timestamp+timedelta(minutes=1)
        if at>until+timedelta(minutes=1):
            break
        quote=f.option(symbol)
        if quote is not None and quote.bid>0 and quote.ask>=quote.bid:
            path.append((at,quote.bid))
    return path


def _stop_exit(entry_ask, path, *, bars=None, side=None, entry_at=None, adaptive=False):
    """Stop-only: no auto sell at end of path; caller must prove session-close mark."""
    debit=(entry_ask+SLIP_PER_SHARE)*100+PREMIUM_FEE_PER_SIDE
    peak=debit
    trailing=.30
    for at,bid in path:
        credit=max(0.0,(bid-SLIP_PER_SHARE)*100-PREMIUM_FEE_PER_SIDE)
        peak=max(peak,credit)
        if adaptive and entry_at and at>=entry_at+timedelta(minutes=2):
            # The close-only historical archive has no true OHLCV. This arm
            # uses two completed SPY CLOSES crossing EMA8 as a limited proxy.
            if bars is not None and side and deteriorating_structure(bars,at,side):
                trailing=min(trailing,.20)
        if credit<=debit*(1-HARD_STOP)+1e-9:
            reason="independent_hard_stop_35pct"
        elif credit<=peak*(1-trailing)+1e-9:
            reason="adaptive_trailing_20pct" if trailing<.30 else "trailing_stop"
        else:
            continue
        return {"pnl_usd":round(credit-debit,6),"exit_at":at,
                "exit_reason":reason}
    return None


def simulate_unlimited(entry_ask, path, cutoff, *, bars=None, side=None, entry_at=None, adaptive=False):
    """No max-duration cap; conservative compulsory same-day expiry buffer."""
    if not path:
        return None
    restricted=[(t,b) for t,b in path if t<=cutoff]
    if not restricted:
        return None
    stopped=_stop_exit(entry_ask,restricted,bars=bars,side=side,
                       entry_at=entry_at,adaptive=adaptive)
    if stopped:
        return stopped
    final_at,final_bid=restricted[-1]
    # No stale favorable endpoint or 16:00 expiry / possible exercise.
    if abs((cutoff-final_at).total_seconds())>BAR_TOLERANCE_SECONDS:
        return None
    debit=(entry_ask+SLIP_PER_SHARE)*100+PREMIUM_FEE_PER_SIDE
    credit=max(0.0,(final_bid-SLIP_PER_SHARE)*100-PREMIUM_FEE_PER_SIDE)
    return {"pnl_usd":round(credit-debit,6),"exit_at":final_at,
            "exit_reason":"mandatory_pre_expiry_liquidation"}


def backtest(data_dir:Path, output:Path):
    """Replays original fixed-hold and no-time-limit with distinct T+1 ledgers."""
    files=_research_files(data_dir)
    if not files:
        raise ValueError("historical SPY archive missing; no synthetic profitability")
    states={k:{"settled":START_CASH,"pending":0.0,"days":[],"trades":[],
               "unscored":0,"entries":0,"issues":Counter(),"reasons":Counter()}
            for k in ARMS}
    inspected=[]
    for file in files:
        frames=list(load_canonical_path(file))
        if not frames:
            continue
        day=frames[0].timestamp.astimezone(NY).date()
        inspected.append(str(day))
        entries=selection(frames)
        cutoff=liquidation_cutoff(day)
        # Close-only O(HLC) proxy for causal EMA8 in the adaptive arm.
        # Wrap timestamps/close as Bar-like, no fabricated candle features.
        from types import SimpleNamespace
        bars=[SimpleNamespace(start=f.timestamp,
              available_at=f.timestamp+timedelta(minutes=1),
              close=f.market.spot) for f in frames]
        for arm in ARMS:
            state=states[arm]
            state["settled"]+=state["pending"]
            state["pending"]=0.0
            busy_until=None
            for entry in entries:
                at=entry["entry_at"]
                q=entry["quote"]
                if at>=cutoff:
                    state["issues"]["entry_after_cutoff"]+=1
                    continue
                if busy_until is not None and at<=busy_until:
                    state["issues"]["position_busy"]+=1
                    continue
                debit=entry["debit"]
                if debit>state["settled"]+1e-8:
                    state["issues"]["insufficient_settled_cash"]+=1
                    continue
                idx=entry["entry_idx"]
                if arm=="frozen_10min":
                    due=at+timedelta(minutes=MAX_FIXED_MIN)
                    trace=quote_path(frames,idx,q.symbol,due)
                    # Mirror historical original _sampled_path: allow next
                    # minute sampled quote but require terminal within 60s.
                    if not trace or abs((trace[-1][0]-due).total_seconds())>60:
                        state["issues"]["no_fixed_terminal_quote_recredit"]+=1
                        busy_until=due
                        continue
                    res=simulate(q.ask,trace,TRAIL_PCT)
                    outcome={"pnl_usd":res["pnl_usd"],
                             "exit_at":datetime.fromisoformat(res["exit_time"]),
                             "exit_reason":res["exit_reason"]}
                else:
                    trace=quote_path(frames,idx,q.symbol,cutoff)
                    outcome=simulate_unlimited(
                        q.ask,trace,cutoff,
                        bars=bars,side=entry["signal"]["side"],entry_at=at,
                        adaptive=arm=="no_max_adaptive20pct")
                state["settled"]-=debit
                state["entries"]+=1
                if outcome is None:
                    # Premium remains reserved/unscored, NOT counted as
                    # a profitable or full-loss historical trade. Cash balance
                    # is a worst-case mark of zero recoverable value.
                    state["unscored"]+=1
                    state["issues"]["unscorable_quote_end"]+=1
                    busy_until=cutoff
                    state["trades"].append({"day":str(day),"arm":arm,"entry_at":at.isoformat(),
                         "symbol":q.symbol,"pnl_usd":None,
                         "premium_reserved_usd":debit,"exit_reason":"unscorable"})
                    continue
                pnl=outcome["pnl_usd"]
                state["pending"]+=max(0.0,debit+pnl)
                busy_until=outcome["exit_at"]
                state["reasons"][outcome["exit_reason"]]+=1
                state["trades"].append({"day":str(day),"arm":arm,"entry_at":at.isoformat(),
                    "symbol":q.symbol,"pnl_usd":pnl,"premium_reserved_usd":0,
                    "exit_at":outcome["exit_at"].isoformat(),
                    "exit_reason":outcome["exit_reason"]})
            state["days"].append({"date":str(day),
                 "equity":round(state["settled"]+state["pending"],4)})
    report={}
    for arm,s in states.items():
        eq=[START_CASH]+[d["equity"] for d in s["days"]]
        peak=START_CASH
        dd=0.0
        dd_peak=None
        dd_peak_equity=START_CASH
        dd_trough=None
        peak_day="initial_capital"
        for datevalue,equity in zip(["start"]+[d["date"] for d in s["days"]],eq):
            if equity>peak:
                peak=equity
                peak_day=datevalue
            if peak-equity>dd:
                dd=peak-equity
                dd_peak_equity=peak
                dd_peak=peak_day
                dd_trough=datevalue
        trades=[t for t in s["trades"] if t["pnl_usd"] is not None]
        pnls=[t["pnl_usd"] for t in trades]
        winners=[x for x in pnls if x>0]
        losing=[x for x in pnls if x<0]
        final=round(s["settled"]+s["pending"],2)
        report[arm]={
            "modeled_cash_equity_usd":final,
            "modeled_cash_profit_vs_1000_usd":round(final-START_CASH,2),
            "maximum_end_of_day_cash_drawdown_usd":round(dd,2),
            "maximum_end_of_day_cash_drawdown_pct_of_peak":round(100*dd/dd_peak_equity,2) if dd_peak_equity>0 else None,
            "drawdown_peak_day":dd_peak,
            "drawdown_trough_day":dd_trough,
            "total_entry_positions":s["entries"],"scored_trades":len(pnls),
            "unscorable_positions":s["unscored"],
            "scored_trade_net_pnl_usd":round(sum(pnls),2),
            "win_rate_scored_pct":round(100*len(winners)/len(pnls),2) if pnls else None,
            "profit_factor":round(sum(winners)/abs(sum(losing)),2) if losing else None,
            "stress_plus_2usd_per_scored_trade_pnl_usd":round(sum(pnls)-2*len(pnls),2),
            "exit_reasons":dict(s["reasons"]),"coverage_issues":dict(s["issues"]),
            "unknown_outcomes_assumed_zero_liquidation_value_in_cash_mark":True,
        }
    result={
        "study":"Frozen SPY Regime Reclaim: no-max-hold exit sensitivity",
        "status":"HISTORICAL_EXPLORATORY_PROXY_NOT_FORWARD_VALIDATED",
        "sessions":len(inspected),
        "first_day":min(inspected) if inspected else None,
        "last_day":max(inspected) if inspected else None,
        "source":"pre-inspected SPY minute close/CBBO-1m buckets, NOT event NBBO",
        "comparability":"Identical original frozen entry/cost rules; only exit horizon is varied",
        "v2_not_backtested":"Legacy data lacks true SPY OHLCV/VWAP needed for V2 regime router",
        "no_max_hold_until":"verified-session-close minus 15 minutes; at latest 15:45 ET normal day",
        "fees_per_side_usd":PREMIUM_FEE_PER_SIDE,
        "slippage_per_option_share":SLIP_PER_SHARE,
        "trailing_original_pct":TRAIL_PCT,
        "independent_hard_loss_pct":35,
        "t1_cash_no_same_day_recycling":True,
        "max_drawdown_basis":"peak-to-trough end-of-day modeled CASH + settled T+1 proceeds; unscored premium conservatively reserved/marked zero, not a verified realized loss",
        "intra_trade_drawdown_unavailable":True,
        "broker_orders":0,"new_data_purchases":0,
        "results":report,
    }
    output.mkdir(parents=True,exist_ok=True)
    (output/"report.json").write_text(json.dumps(result,sort_keys=True,indent=2)+"\n")
    with (output/"trades.csv").open("w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=("day","arm","entry_at","symbol","pnl_usd",
                           "premium_reserved_usd","exit_at","exit_reason"))
        w.writeheader()
        for arm in ARMS:
            for row in states[arm]["trades"]:
                w.writerow(row)
    print("NO_MAX_HOLD_RESEARCH_RESULT "+json.dumps(result,sort_keys=True),flush=True)
    return result


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--data-dir",type=Path,default=Path("/data/research"))
    p.add_argument("--output-dir",type=Path,default=Path("/data/research/regime_no_max_hold"))
    a=p.parse_args()
    backtest(a.data_dir,a.output_dir)


if __name__=="__main__":
    main()
