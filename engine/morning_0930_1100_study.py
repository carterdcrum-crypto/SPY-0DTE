"""Test the requested SPY morning window: 09:30–11:00 Eastern.

Side-by-side with original 10:00–12:00 window, a causal early-opening
close-breakout proxy, and a combined non-overlap candidate stream.
Research only: no broker access, purchases, production entry changes,
or live/paper-account state mutations.

IMPORTANT: existing MACD/RSI four-factor screen requires >=75 completed
1-minute bars; moving only its clock DOES NOT create 09:30 entries.
Opening strategy separately uses first 15 preceding completed CLOSES,
not true OHLC opening-range highs/lows or VWAP.
All research exits freeze 30% full-position trailing, independent 35%
hard stop, 10-minute max hold, no take-profit and no partial sells.
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
from .spy_pcr_momentum_reversal import session_classifications, select
from .trailing_only_sweep import simulate, period

START_TIME = time(9, 30)
END_TIME = time(11, 0)  # exclusive
OLD_START = time(10, 0)
OLD_END = time(12, 0)  # exclusive
OPENING_HISTORY = 15
MIN_VOLUME_RATIO = 1.50
MIN_FIVE_MINUTE_BPS = 3.0
TRAIL_PCT = 30
LOCK_MINUTES = 10
EXTRA_FRICTION_PER_ROUNDTRIP_USD = 2.0
VARIANTS = (
    "legacy_volume_1000_1200",
    "legacy_volume_0930_1100",
    "opening_close_breakout_0930_1100",
    "combined_opening_and_legacy_0930_1100",
)
PERIODS = ("development", "validation", "previously_studied_diagnostic", "all_inspected")


def in_window(ts:datetime, start:time=START_TIME, end:time=END_TIME)->bool:
    clock=ts.astimezone(ET).time()
    return start<=clock<end


def legacy_events(frames, start:time, end:time)->list[dict]:
    """Filter before selecting nonoverlap — never lock on failed signals."""
    classified=session_classifications(frames)
    selected=select([
        row for row in classified
        if "base_four_factors_PASS" in row.reasons
        and row.opportunity.volume_ratio_at_signal >= MIN_VOLUME_RATIO
        and in_window(datetime.fromisoformat(row.opportunity.signal_at),start,end)
    ],"momentum_reversal_four_factors")
    return [{
        "signal_at":row.opportunity.signal_at,
        "right":row.opportunity.original_direction,
        "origin":"legacy_four_factor",
        "volume_ratio":row.opportunity.volume_ratio_at_signal,
    } for row in selected]


def opening_events(frames)->list[dict]:
    """First close breakout beyond preceding 15 completed closes.

    Required price and volume information is observed as of the signal's
    completed minute. Data gaps are rejected; no overnight warmup.
    Opening bar (09:30–09:31) cannot be treated as a 09:30 signal.
    """
    result=[]
    if len(frames)<OPENING_HISTORY+2:
        return result
    times=[x.timestamp for x in frames]
    closes=[x.market.spot for x in frames]
    volumes=[x.market.volume_ratio for x in frames]
    for i in range(OPENING_HISTORY,len(frames)):
        seen_at=times[i]+timedelta(minutes=1)
        if not in_window(seen_at):
            continue
        # Reject missing/unequal 1-minute bars in the required history.
        if any((times[j+1]-times[j]).total_seconds()!=60
               for j in range(i-OPENING_HISTORY,i)):
            continue
        if any(not math.isfinite(x) or x<=0 for x in closes[i-OPENING_HISTORY:i+1]):
            continue
        vol=volumes[i]
        if not math.isfinite(vol) or vol<MIN_VOLUME_RATIO:
            continue
        last=closes[i]
        prev=closes[i-1]
        lookback=closes[i-OPENING_HISTORY:i]
        previous_lookback=closes[i-OPENING_HISTORY-1:i-1] if i>OPENING_HISTORY else lookback
        trend=_bps(last,closes[i-5])
        side=None
        if (last>max(lookback) and prev<=max(previous_lookback)
                and trend>=MIN_FIVE_MINUTE_BPS):
            side="call"
        elif (last<min(lookback) and prev>=min(previous_lookback)
              and trend<=-MIN_FIVE_MINUTE_BPS):
            side="put"
        if side is not None:
            result.append({
                "signal_at":seen_at.isoformat(),
                "right":side,
                "origin":"opening_close_breakout_proxy",
                "volume_ratio":vol,
            })
    return result


def nonoverlap(events:list[dict])->list[dict]:
    """One hypothetical position, never overlapping by ten-minute window."""
    chosen=[]
    lock_until=None
    # Earliest signal wins. Tie priority is fixed and independent of returns.
    for row in sorted(events,key=lambda x:(
        x["signal_at"],0 if x["origin"]=="opening_close_breakout_proxy" else 1,
        x["right"])):
        start=datetime.fromisoformat(row["signal_at"])+timedelta(minutes=1)
        if lock_until is not None and start<=lock_until:
            continue
        chosen.append(row)
        lock_until=start+timedelta(minutes=LOCK_MINUTES)
    return chosen


def summarize(ledger:list[dict], dates:list[str], variant:str, group:str)->dict:
    subset=[x for x in ledger if x["variant"]==variant and
            (group=="all_inspected" or period(x["day"])==group)]
    days=[d for d in dates if group=="all_inspected" or period(d)==group]
    pnls=[x["pnl_usd"] for x in subset]
    returns=[x["return_pct"] for x in subset]
    daily=defaultdict(float)
    for row in subset:
        daily[row["day"]]+=row["pnl_usd"]
    acc=high=dd=0.
    for p in pnls:
        acc+=p
        high=max(high,acc)
        dd=max(dd,high-acc)
    return {
        "calendar_sessions":len(days),
        "hypothetical_one_contract_trades":len(pnls),
        "wins":sum(x>0 for x in pnls),
        "win_rate_pct":round(100*sum(x>0 for x in pnls)/len(pnls),2) if pnls else None,
        "average_option_premium_return_pct":round(statistics.fmean(returns),3) if pnls else None,
        "modeled_pnl_usd_NOT_ACCOUNT":round(sum(pnls),2),
        "extra_2usd_per_contract_stressed_pnl_usd":round(
            sum(pnls)-len(pnls)*EXTRA_FRICTION_PER_ROUNDTRIP_USD,2),
        "positive_sessions":sum(daily[d]>0 for d in days),
        "negative_sessions":sum(daily[d]<0 for d in days),
        "no_trade_or_flat_sessions":sum(daily[d]==0 for d in days),
        "closed_trade_additive_drawdown_usd_NOT_ACCOUNT":round(dd,2),
        "earliest_matched_signal_at":min((x["signal_at"] for x in subset),default=None),
        "latest_matched_signal_at":max((x["signal_at"] for x in subset),default=None),
        "pre_1000_signals":sum(
            datetime.fromisoformat(x["signal_at"]).astimezone(ET).time()<time(10,0)
            for x in subset),
        "signal_1000_to_1044":sum(
            time(10,0)<=datetime.fromisoformat(x["signal_at"]).astimezone(ET).time()
                <time(10,45) for x in subset),
        "signal_1045_to_1059":sum(
            time(10,45)<=datetime.fromisoformat(x["signal_at"]).astimezone(ET).time()
                <END_TIME for x in subset),
    }


def bootstrap(ledger:list[dict],dates:list[str],variant:str,reps:int=1500)->dict:
    daily=defaultdict(float)
    count=defaultdict(int)
    for row in ledger:
        if row["variant"]==variant:
            daily[row["day"]]+=row["pnl_usd"]
            count[row["day"]]+=1
    raw=[daily[x] for x in dates]
    stressed=[daily[x]-count[x]*EXTRA_FRICTION_PER_ROUNDTRIP_USD for x in dates]
    rng=random.Random(1200930)
    def ci(data):
        vals=[]
        for _ in range(reps):
            sample=[]
            while len(sample)<len(data):
                offset=rng.randrange(len(data))
                sample.extend(data[(offset+k)%len(data)] for k in range(5))
            vals.append(sum(sample[:len(data)])/len(data))
        vals.sort()
        return [round(vals[int(.025*reps)],3),round(vals[int(.975*reps)-1],3)]
    if not dates:
        return {"unavailable":"no calendar"}
    positive=sorted((daily[d],d) for d in dates)[::-1]
    return {
        "raw_5session_block_95pct_usd_per_calendar_day":ci(raw),
        "extra_friction_5session_block_95pct_usd_per_calendar_day":ci(stressed),
        "sum_without_best_day_usd":round(sum(raw)-positive[0][0],2),
        "sum_without_best_three_days_usd":round(
            sum(raw)-sum(value for value,_ in positive[:3]),2),
        "best_day":positive[0][1],"best_day_pnl_usd":round(positive[0][0],2),
        "not_new_unseen_oos":True,
    }


def run(root:Path,out:Path)->dict:
    ledger=[]
    dates=[]
    counts=defaultdict(int)
    for file in _research_files(root):
        frames=list(load_canonical_path(file))
        if not frames:
            continue
        day=frames[0].timestamp.astimezone(ET).date().isoformat()
        dates.append(day)
        original=legacy_events(frames,OLD_START,OLD_END)
        shifted=legacy_events(frames,START_TIME,END_TIME)
        open_raw=opening_events(frames)
        opening=nonoverlap(open_raw)
        variants={
            "legacy_volume_1000_1200":original,
            "legacy_volume_0930_1100":shifted,
            "opening_close_breakout_0930_1100":opening,
            "combined_opening_and_legacy_0930_1100":nonoverlap(open_raw+shifted),
        }
        counts["archived_minute_frames"]+=len(frames)
        counts["opening_raw_candidates"]+=len(open_raw)
        for label,chosen in variants.items():
            counts[label+"_accepted"]+=len(chosen)
            for row in chosen:
                chosen_option=_select_entry(
                    frames,datetime.fromisoformat(row["signal_at"]),row["right"])
                if chosen_option is None:
                    counts[label+"_missing_entry_quote"]+=1
                    continue
                idx,option=chosen_option
                trace=_sampled_path(frames,idx,option.symbol)
                if trace is None:
                    counts[label+"_missing_exit_path"]+=1
                    continue
                result=simulate(option.ask,trace,TRAIL_PCT)
                ledger.append({
                    "day":day,"variant":label,
                    "signal_at":row["signal_at"],"side":row["right"],
                    "origin":row["origin"],"contract":option.symbol,
                    "volume_ratio":round(row["volume_ratio"],4),
                    "pnl_usd":result["pnl_usd"],
                    "return_pct":result["return_on_premium_pct"],
                    "exit_reason":result["exit_reason"],
                    "modeled_entry_ask":option.ask,
                    "modeled_bid_ask_spread":round(option.spread,4),
                })
    dates=sorted(set(dates))
    report={
        "study":"SPY_MORNING_0930_TO_1100_VS_OLD_1000_TO_1200_FROZEN_30PCT_TRAIL",
        "requested_window_local_exchange":"09:30 inclusive to 11:00 exclusive America/New_York",
        "old_window_local_exchange":"10:00 inclusive to 12:00 exclusive America/New_York",
        "normal_four_factor_first_theoretical_signal_local":"10:46 due to >=75 completed minute history",
        "early_opening_range_first_theoretical_signal_local":"09:46, fifteen previous closes required",
        "opening_breakout_is_close_proxy_not_true_ohlc_or_vwap":True,
        "trailing_stop_pct":TRAIL_PCT,
        "hard_loss_stop_pct":35,"max_hold_minutes":10,
        "profit_targets":False,"partial_exits":False,
        "archive_sessions":len(dates),
        "coverage":dict(counts),
        "results":{variant:{p:summarize(ledger,dates,variant,p) for p in PERIODS}
                   for variant in VARIANTS},
        "robustness":{variant:bootstrap(ledger,dates,variant) for variant in VARIANTS},
        "no_purchase_no_orders":True,
        "full_chain_asof_pcr_unavailable":True,
        "true_timestamped_execution_nbbo_unavailable":True,
        "new_unseen_holdout_unavailable":True,
        "real_cash_account_return_not_tested":True,
        "warning":"Previously inspected data and bucket CBBO quotes, one hypothetical contract per trade. Validation period is not untouched.",
    }
    out.mkdir(parents=True,exist_ok=True)
    (out/"report.json").write_text(json.dumps(report,indent=2,sort_keys=True)+"\n")
    with (out/"trade_ledger.csv").open("w",newline="") as f:
        writer=csv.DictWriter(f,fieldnames=[
            "day","variant","signal_at","side","origin","contract","volume_ratio",
            "pnl_usd","return_pct","exit_reason","modeled_entry_ask",
            "modeled_bid_ask_spread"])
        writer.writeheader()
        writer.writerows(ledger)
    print("MORNING_0930_1100_REPORT "+json.dumps(report,sort_keys=True),flush=True)
    return report


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--data-dir",type=Path,default=Path("/data/research"))
    parser.add_argument("--output-dir",type=Path,default=Path("/data/research/morning_0930_1100"))
    args=parser.parse_args()
    run(args.data_dir,args.output_dir)


if __name__=="__main__":
    main()
