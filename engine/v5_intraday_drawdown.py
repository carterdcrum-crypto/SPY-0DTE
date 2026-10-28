"""Minute-sampled account liquidation-equity drawdown, NOT true intraday risk.

Compare the original V5 three-setup 5% spread profile with the research-only
looser liquidity reversal. Replay previously recorded 128 sessions and record
ACCOUNT equity at each observed option bid/ask snapshot, including *unrealized*
positions marked at bid minus slippage and exit fees. Settlement ledger and
entry/exit logic reproduce the executed 13-arm historical signal sweep.

No L1 event-time executable depth, no sub-minute quotes, no invented marks.
A missing position quote suppresses that minute's sample. Excludes unobserved
intraminute troughs and gap risk; cannot establish true account max drawdown.
NO live/paper changes, Webull orders or paid data requests.
"""
from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path

from .burst_research import _research_files
from .data import load_canonical_path
from .regime_hold_sensitivity import START_CASH, MAX_FIXED_MIN, liquidation_cutoff
from .v4_dynamic_size_backtest import summary
from .v5_archive_proxy import SLIP, FEE, ARMS, proxy_exit
from .v5_conditional_capital_study import (
    choose_qty, market_evidence, peak_to_trough_eod,
)
from .v5_signal_variations import param_candidates
from .weekly_options_data import NY

FULL = ARMS[-1]
ARMS_TO_COMPARE = {
    "baseline_all_three": 1808494.60,
    "reversal_looser": 2055376.50,
}


def fresh_metrics():
    return {
        "peak_usd": float(START_CASH), "peak_at": "initial_capital",
        "max_drawdown_pct": 0.0, "peak_at_worst": None,
        "trough_at_worst": None, "peak_usd_at_worst": None,
        "trough_usd_at_worst": None,
        "worst_intra_session_pct": 0.0,
        "worst_intra_session_at": None,
        "covered_mark_samples": 0,
        "missing_open_position_quote_minutes": 0,
        "open_position_quote_minutes": 0,
        "days_with_missing_open_position_quotes": set(),
        "worst_missing_streak_minutes": 0,
        "largest_unrealized_dip_in_single_trade_pct": 0.,
        "worst_single_trade_mark": None,
    }


def append_mark(metric, ts, equity):
    """Peak and running max drawdown across *chronological* marked equity."""
    peak = metric["peak_usd"]
    if equity > peak:
        metric["peak_usd"] = round(equity, 8)
        metric["peak_at"] = ts
    elif peak > 0:
        dd = (peak-equity)/peak*100
        if dd > metric["max_drawdown_pct"]:
            metric["max_drawdown_pct"] = dd
            metric["peak_at_worst"] = metric["peak_at"]
            metric["trough_at_worst"] = ts
            metric["peak_usd_at_worst"] = round(peak, 4)
            metric["trough_usd_at_worst"] = round(equity, 4)
    metric["covered_mark_samples"] += 1


def marked_day(metric, frames, day_trades, day, opening):
    """Account equity = settled cash + unsettled sales + bid-liquidation asset.

    No mark is emitted when an open option has no quote at the sampled minute.
    Stops and time exits are the same *realized* historical modeled exits.
    """
    events=defaultdict(list)
    for trade in day_trades:
        events[trade["entry_at"]].append(("buy",trade))
        if trade["exit_at"] is not None:
            events[trade["exit_at"]].append(("sell",trade))
    # Trades are chosen with no overlap, and exit and entry at identical
    # timestamps would not be allowed by frozen busy_until <= protection.
    cash=float(opening)
    sales_receivable=0.
    position=None
    prior_mark=None
    missing_streak=0
    day_peak=float(opening)
    day_peak_at=str(day)+" session_open"
    day_worst=0.
    day_open_has_gap=False
    append_mark(metric,day_peak_at,opening)

    for frame in frames:
        ts=frame.timestamp+timedelta(minutes=1)
        for event,tr in sorted(events.get(ts,[]),
                               key=lambda x:0 if x[0]=="sell" else 1):
            if event=="sell":
                assert position is tr, "Unmatched option position sale event"
                sales_receivable+=tr["qty"]*tr["credit_per_contract"]
                position=None
                prior_mark=None
                missing_streak=0
            elif event=="buy":
                assert position is None, "Overlapping positions"
                cash-=tr["qty"]*tr["debit"]
                position=tr
                prior_mark=None
                missing_streak=0

        liquidation=0.
        if position is not None:
            metric["open_position_quote_minutes"]+=1
            q=frame.option(position["symbol"])
            if q is None or q.bid<=0 or q.ask<q.bid:
                metric["missing_open_position_quote_minutes"]+=1
                day_open_has_gap=True
                missing_streak+=1
                metric["worst_missing_streak_minutes"]=max(
                    metric["worst_missing_streak_minutes"],missing_streak)
                continue
            missing_streak=0
            credit=max(0.,(q.bid-SLIP)*100-FEE)
            liquidation=position["qty"]*credit
            if prior_mark is None:
                prior_mark=position["qty"]*position["debit"]
            if prior_mark>0:
                decline=max(0.,1-liquidation/prior_mark)*100
                if decline>metric["largest_unrealized_dip_in_single_trade_pct"]:
                    metric["largest_unrealized_dip_in_single_trade_pct"]=decline
                    metric["worst_single_trade_mark"]={
                        "at":ts.isoformat(),"symbol":position["symbol"],
                        "contracts":position["qty"],"entry_at":position["entry_at"].isoformat(),
                        "debit_usd":round(position["qty"]*position["debit"],2),
                        "observed_liquidation_mark_usd":round(liquidation,2),
                    }
        equity=cash+sales_receivable+liquidation
        ts_iso=ts.isoformat()
        append_mark(metric,ts_iso,equity)
        if equity>day_peak:
            day_peak,day_peak_at=equity,ts_iso
        if day_peak>0:
            drawdown=(day_peak-equity)/day_peak*100
            if drawdown>day_worst:
                day_worst=drawdown
                if drawdown>metric["worst_intra_session_pct"]:
                    metric["worst_intra_session_pct"]=drawdown
                    metric["worst_intra_session_at"]={
                        "day":str(day),"peak_at":day_peak_at,
                        "trough_at":ts_iso,"peak_usd":round(day_peak,2),
                        "trough_usd":round(equity,2),
                    }

    if day_open_has_gap:
        metric["days_with_missing_open_position_quotes"].add(str(day))
    # The original close-proxy research treats missing exit mark as premium
    # reserved, not as verified sale. Do not append fake closing equity point.
    if position is None:
        modeled_final=cash+sales_receivable
        return modeled_final
    return None


def replay(paths):
    assert paths, "No archived Railway sessions; cannot fabricate intraday drawdown"
    states={
        key:{"settled":float(START_CASH),"pending":0.,"daily":[],
             "trades":[],"skips":Counter(),"exits":Counter()}
        for key in ARMS_TO_COMPARE
    }
    metrics={key:fresh_metrics() for key in ARMS_TO_COMPARE}
    dates=[]
    for file in paths:
        frames=list(load_canonical_path(file))
        if not frames:
            continue
        day=frames[0].timestamp.astimezone(NY).date()
        dates.append(str(day))
        cutoff=liquidation_cutoff(day)
        candidates={key:param_candidates(frames,variant=key)
                    for key in ARMS_TO_COMPARE}

        for key,entries in candidates.items():
            state=states[key]
            state["settled"]=round(state["settled"]+state["pending"],8)
            state["pending"]=0.
            opening=state["settled"]
            prior_peak=max([START_CASH]+[x["equity_usd"] for x in state["daily"]])
            busy_until=None
            events=defaultdict(set)
            for item in entries:
                events[(item["entry_at"],item["signal"]["side"])].add(
                    item["signal"]["setup"])
            day_trades=[]
            for item in entries:
                at=item["entry_at"]
                if at>=cutoff-timedelta(minutes=MAX_FIXED_MIN+1):
                    state["skips"]["LATE_ENTRY"]+=1
                    continue
                if busy_until is not None and at<=busy_until:
                    state["skips"]["BUSY"]+=1
                    continue
                evidence=market_evidence(
                    item,events[(at,item["signal"]["side"])])
                qty,fraction=choose_qty(
                    ("full_throttle",),item=item,opening=opening,
                    settled=state["settled"],peak=prior_peak,
                    evidence=evidence,starting_cash=START_CASH)
                if qty<1:
                    state["skips"]["CASH_OR_RISK_BUDGET"]+=1
                    continue
                debit=item["debit"]
                assert qty*debit<=state["settled"]+1e-8, "T+1 violation"
                outcome=proxy_exit(FULL,item,frames,cutoff)
                state["settled"]=round(state["settled"]-qty*debit,8)
                record={
                    "entry_at":at,"qty":qty,"debit":debit,
                    "symbol":item["quote"].symbol,"setup":item["signal"]["setup"],
                    "exit_at":None,
                    "credit_per_contract":None,
                }
                if outcome is None:
                    busy_until=cutoff
                else:
                    record["exit_at"]=outcome["exit_at"]
                    record["credit_per_contract"]=max(
                        0.,debit+outcome["pnl_usd"])
                    state["pending"]+=max(0.,qty*debit+round(qty*outcome["pnl_usd"],6))
                    busy_until=outcome["exit_at"]
                    state["exits"][outcome["exit_reason"]]+=1
                day_trades.append(record)
                state["trades"].append({
                    "contracts":qty,"entry_at":at.isoformat(),
                    "pnl_usd":None if outcome is None else
                    round(qty*outcome["pnl_usd"],6),
                    "exit_reason":"NO_VERIFIED_TERMINAL_BID_PREMIUM_RESERVED"
                                  if outcome is None else outcome["exit_reason"],
                })
            expected=round(state["settled"]+state["pending"],4)
            state["daily"].append({"day":str(day),"equity_usd":expected})
            observed=marked_day(metrics[key],frames,day_trades,day,opening)
            if observed is not None:
                assert abs(observed-expected)<.02, (
                    f"Account mark ending balance diverged: {day} {key} "
                    f"{observed} vs {expected}")
        if len(dates)%20==0:
            print("V5_INTRADAY_PROGRESS "+json.dumps(
                {"sessions":len(dates),"day":str(day)}),flush=True)
    assert len(dates)==128, f"Archive incomplete: {len(dates)} sessions"
    results={}
    for key in ARMS_TO_COMPARE:
        state=states[key];metric=metrics[key]
        historical=summary(state,starting_cash=START_CASH)
        expected=ARMS_TO_COMPARE[key]
        assert abs(historical["ending_equity_usd"]-expected)<.02, (
            f"Frozen 13-arm ending equity drift: {key}: {historical}")
        eod=peak_to_trough_eod(state["daily"],START_CASH)
        assert metric["max_drawdown_pct"]+1e-6>=eod["true_max_eod_drawdown_pct"],(
            "Sampled intraday risk cannot be below observed EOD mark risk",
            key,metric["max_drawdown_pct"],eod)
        results[key]={
            "historical_ending_equity_usd":historical["ending_equity_usd"],
            "scored_positions":historical["scored_positions"],
            "sampled_account_peak_to_trough_drawdown_pct":round(
                metric["max_drawdown_pct"],4),
            "sampled_intraday_session_peak_to_trough_pct":round(
                metric["worst_intra_session_pct"],4),
            "peak_at_worst":metric["peak_at_worst"],
            "trough_at_worst":metric["trough_at_worst"],
            "peak_usd_at_worst":metric["peak_usd_at_worst"],
            "trough_usd_at_worst":metric["trough_usd_at_worst"],
            "worst_single_session_details":metric["worst_intra_session_at"],
            "max_end_of_day_drawdown_pct":eod["true_max_eod_drawdown_pct"],
            "observed_mark_samples":metric["covered_mark_samples"],
            "open_option_minutes":metric["open_position_quote_minutes"],
            "missing_open_option_quote_minutes":metric[
                "missing_open_position_quote_minutes"],
            "days_with_open_quote_gaps":len(metric[
                "days_with_missing_open_position_quotes"]),
            "largest_consecutive_missing_quote_minutes":metric[
                "worst_missing_streak_minutes"],
            "largest_single_position_observed_mark_decline_pct":round(
                metric["largest_unrealized_dip_in_single_trade_pct"],4),
            "largest_single_position_observed_mark":metric[
                "worst_single_trade_mark"],
        }
    return {
        "status":"OBSERVED_MINUTE_BID_LIQUIDATION_ACCOUNT_EQUITY_ONLY",
        "start_cash":START_CASH,"first_session":min(dates),
        "last_session":max(dates),"sessions":len(dates),
        "iv_rv_cap":1.5,"delta_range":[.35,.65],
        "max_spread_fraction":.05,
        "results":results,
        "live_and_paper_unchanged":True,"broker_orders":0,
        "real_max_intraday_drawdown_known":False,
        "cautions":[
            "Only minute-spaced sampled option CBBO; true intraminute highs/lows absent",
            "Missing position bid samples excluded; high-risk intervals can be skipped",
            "Simulated bid-minus-slippage account liquidation mark, not executable prices",
            "No order size or market-impact information; huge modeled contract counts",
            "Repeatedly studied in-sample 128 sessions; not independent forward test",
            "Realized close-only proxy exits and full-cash size are hypothetical",
        ],
    }


def main():
    root=Path(os.getenv("RESEARCH_DATA_DIR","/data/research"))
    result=replay(_research_files(root))
    report=root/"v5_intraday_drawdown_2026_10_28"/"report.json"
    report.parent.mkdir(parents=True,exist_ok=True)
    report.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
    print("V5_INTRADAY_META "+json.dumps({
        "status":result["status"],"sessions":result["sessions"],
        "real_max_intraday_drawdown_known":False}),flush=True)
    for key,val in result["results"].items():
        print("V5_INTRADAY_RESULT "+json.dumps({
            "arm":key,**val},sort_keys=True),flush=True)
    print("V5_INTRADAY_REPORT_SAVED "+str(report),flush=True)
    os.execvp("python",["python","-m","engine.forward_watch"])


if __name__=="__main__":
    main()
