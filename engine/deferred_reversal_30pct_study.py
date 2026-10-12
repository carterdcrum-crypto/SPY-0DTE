"""Causal deferred reversal detection, frozen 30% trailing exit diagnostic.

The previous reversal rule required the trigger minute to move AWAY from the
rolling mean and TOWARD the mean simultaneously. That produced zero accepted
reversal signals across 128 archived sessions. This experimental repair waits
for a subsequent completed-minute turn (max 3 bars). Historical labels,
future price changes, and contemporaneous options PCR are not read by entry.
Not production/live code, and NOT a true event-time NBBO backtest.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from .burst_research import _research_files
from .data import HistoricalFrame, load_canonical_path
from .entry_filter_30pct_study import _summarize
from .exit_policy_comparison import _sampled_path, _select_entry
from .free_signal_screen import ET, _bps, screen_session
from .macd_rsi_sma_tape_experiment import _macd, _rsi_wilder
from .spy_pcr_momentum_reversal import select, session_classifications
from .trailing_only_sweep import simulate, period

TRAIL_PCT = 30
WAIT_COMPLETED_MINUTES = 3
VARIANTS = ("base", "deferred_reversal_price_rsi",
            "deferred_reversal_price_rsi_macd_volume",
            "base_plus_confirmed_reversal")


def delayed_reversals(frames: list[HistoricalFrame], *, strict: bool):
    """Return causal confirmation events (time, right, origin) in local session."""
    if not frames:
        return [],0
    spots=[x.market.spot for x in frames]
    volumes=[x.market.volume_ratio for x in frames]
    times=[x.timestamp for x in frames]
    macds,signals=_macd(spots)
    rsi=_rsi_wilder(spots)
    by_complete={t+timedelta(minutes=1):i for i,t in enumerate(times)}
    triggers=[r for r in screen_session(frames)
              if r.family=="rolling_mean_reversal"]
    found=[]
    for trigger in triggers:
        i=by_complete.get(datetime.fromisoformat(trigger.signal_timestamp))
        if i is None or i<75:
            continue
        side=1 if trigger.direction=="call" else -1
        # RSI must show an extreme at the ORIGINAL trigger, not after exit.
        if rsi[i] is None or not (rsi[i]<45 if side>0 else rsi[i]>55):
            continue
        for j in range(i+1,min(i+WAIT_COMPLETED_MINUTES+1,len(frames)-12)):
            if (times[j]-times[i]).total_seconds()!=60*(j-i):
                continue
            if any((times[z+1]-times[z]).total_seconds()!=60 for z in range(i,j)):
                continue
            # Signed price turn is observed only AFTER the original excursion.
            action=side*_bps(spots[j],spots[j-1])>=.5
            if not action:
                continue
            if strict:
                if any(v is None for v in (macds[j],signals[j],macds[j-1],signals[j-1])):
                    continue
                hist=macds[j]-signals[j]
                prev=macds[j-1]-signals[j-1]
                macd_turn=side*(hist-prev)>0
                volume_ok=math.isfinite(volumes[j]) and volumes[j]>=1.10
                if not (macd_turn and volume_ok):
                    continue
            found.append({
                "signal_at":(times[j]+timedelta(minutes=1)).isoformat(),
                "right":trigger.direction,"trigger_at":trigger.signal_timestamp,
                "confirmation_wait":j-i,
            })
            break
    # No future result/quote data is consulted during the confirmation filter.
    unique={}
    for x in found:
        key=(x["signal_at"],x["right"])
        unique.setdefault(key,x)
    return sorted(unique.values(),key=lambda x:x["signal_at"]),len(triggers)


def _locked(events):
    accepted=[]
    locked_until=None
    for e in sorted(events,key=lambda x:(x["signal_at"], x["priority"])):
        begin=datetime.fromisoformat(e["signal_at"])+timedelta(minutes=1)
        if locked_until is not None and begin<=locked_until:
            continue
        accepted.append(e)
        locked_until=begin+timedelta(minutes=10)
    return accepted


def run(root: Path, output: Path):
    coverage=defaultdict(int)
    records=[]
    days=[]
    for file in _research_files(root):
        frames=list(load_canonical_path(file))
        if not frames: continue
        day=frames[0].timestamp.astimezone(ET).date().isoformat()
        days.append(day)
        rows=session_classifications(frames)
        base=select(rows,"momentum_reversal_four_factors")
        baseline=[{"signal_at":x.opportunity.signal_at,
                   "right":x.opportunity.original_direction,
                   "priority":0,"kind":"base"} for x in base]
        loose,n_triggers=delayed_reversals(frames,strict=False)
        strict,_=delayed_reversals(frames,strict=True)
        coverage["original_reversal_trigger_candidates"]+=n_triggers
        coverage["delayed_price_rsi_confirmations"]+=len(loose)
        coverage["delayed_full_confirmations"]+=len(strict)
        branches={
            "base":baseline,
            "deferred_reversal_price_rsi":[{**x,"priority":1,"kind":"reversal_price_rsi"} for x in loose],
            "deferred_reversal_price_rsi_macd_volume":[{**x,"priority":1,"kind":"reversal_all_factors"} for x in strict],
            "base_plus_confirmed_reversal":[*baseline,*[{**x,"priority":1,"kind":"reversal_all_factors"} for x in strict]],
        }
        for variant,candidates in branches.items():
            chosen=_locked(candidates)
            coverage[variant+"_accepted"]+=len(chosen)
            for row in chosen:
                selected=_select_entry(frames,datetime.fromisoformat(row["signal_at"]),row["right"])
                if selected is None:
                    coverage[variant+"_unpriced_entry"]+=1
                    continue
                ix,contract=selected
                trace=_sampled_path(frames,ix,contract.symbol)
                if trace is None:
                    coverage[variant+"_unpriced_exit"]+=1
                    continue
                pnl=simulate(contract.ask,trace,TRAIL_PCT)
                records.append({
                    "day":day,"period":period(day),"variant":variant,
                    "pnl_usd":pnl["pnl_usd"],"option_return_pct":pnl["return_on_premium_pct"],
                    "direction":row["right"],"signal_at":row["signal_at"],
                    "kind":row["kind"],"contract":contract.symbol,
                    "confirmation_wait":row.get("confirmation_wait"),
                })
    days=sorted(set(days))
    groups=("development","validation","previously_studied_diagnostic","all_inspected")
    result={
        "study":"FIX_ZERO_REVERSAL_CONTRADICTION_DEFERRED_CAUSAL_CONFIRMATION",
        "30pct_trailing_stop":True,"stop_pct":TRAIL_PCT,
        "zero_profit_targets_or_partial_exits":True,
        "n_sessions":len(days),"counts":dict(coverage),
        "results":{v:{g:_summarize(records,days,v,g) for g in groups} for v in VARIANTS},
        "no_external_data_or_broker_calls":True,
        "not_execution_accuracy":True,"full_chain_PCR_missing":True,
        "previously_inspected_data":True,
        "assessment":"Exploratory repair only; no production promotion based on these results.",
    }
    output.mkdir(parents=True,exist_ok=True)
    (output/"report.json").write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
    with (output/"matched_cases.csv").open("w",newline="") as f:
        fields=list(records[0]) if records else ["day","variant","pnl_usd"]
        writer=csv.DictWriter(f,fieldnames=fields)
        writer.writeheader()
        writer.writerows(records)
    print("DEFERRED_REVERSAL30_RESULT "+json.dumps(result,sort_keys=True),flush=True)
    return result


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--data-dir",type=Path,default=Path("/data/research"))
    parser.add_argument("--output-dir",type=Path,default=Path("/data/research/reversal30"))
    args=parser.parse_args()
    run(args.data_dir,args.output_dir)

if __name__=="__main__":
    main()
