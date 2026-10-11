"""Read-only SPY indicator entry ablations with a FROZEN 30% full-exit trail.

Existing archive only. Does not request prices, place orders or modify paper/live state.
These are previously inspected data; validation is descriptive, not untouched OOS.
PCR is unavailable; do not claim to test a full five-factor strategy.
"""
from __future__ import annotations
import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from .burst_research import _research_files
from .data import load_canonical_path
from .exit_policy_comparison import _sampled_path, _select_entry
from .free_signal_screen import ET, PERIODS
from .spy_pcr_momentum_reversal import session_classifications, select
from .trailing_only_sweep import simulate, period

TRAIL_PCT = 30
VARIANTS = (
    "base_four_factor",
    "momentum_only",
    "reversal_only",
    "breakout_only",
    "pullback_only",
    "macd_and_price_action",
    "momentum_macd_price",
    "higher_volume_ge_1p50",
    "weighted_score_ge_6",
)
PRIORITY = ("development","validation","previously_studied_diagnostic","all_inspected")


def eligible(row, variant: str) -> bool:
    """Read features known at decision time, NEVER future ten-minute P&L."""
    if variant not in VARIANTS:
        raise ValueError(f"unknown prespecified entry ablation {variant}")
    if "base_four_factors_PASS" not in row.reasons:
        return False
    op=row.opportunity
    return {
        "base_four_factor": True,
        "momentum_only": row.family_mode=="momentum",
        "reversal_only": row.family_mode=="reversal",
        "breakout_only": op.setup=="close_breakout_baseline",
        "pullback_only": op.setup=="trend_pullback_reclaim",
        "macd_and_price_action": (row.macd_histo_aligned_or_improving
                                   and row.price_action_confirmed),
        "momentum_macd_price": (row.family_mode=="momentum" and
                                row.macd_histo_aligned_or_improving and
                                row.price_action_confirmed),
        "higher_volume_ge_1p50": op.volume_ratio_at_signal>=1.50,
        "weighted_score_ge_6": op.weighted_total>=6,
    }[variant]


def _summarize(records: list[dict], days: list[str], variant: str, regime: str) -> dict:
    subset=[x for x in records if x["variant"]==variant and (
        regime=="all_inspected" or x["period"]==regime)]
    calendar=[d for d in days if regime=="all_inspected" or period(d)==regime]
    daily=defaultdict(float)
    for x in subset:
        daily[x["day"]]+=x["pnl_usd"]
    vals=[x["pnl_usd"] for x in subset]
    returns=[x["option_return_pct"] for x in subset]
    acc=peak=drawdown=0.
    for x in subset:
        acc+=x["pnl_usd"]
        peak=max(peak,acc)
        drawdown=max(drawdown,peak-acc)
    return {
        "sessions":len(calendar),"matched_trades":len(vals),
        "mean_pnl_per_trade_usd":round(sum(vals)/len(vals),4) if vals else None,
        "one_contract_additive_pnl_usd_NOT_ACCOUNT":round(sum(vals),2),
        "sum_pnl_per_calendar_session_usd_NOT_ACCOUNT":round(sum(vals)/len(calendar),3) if calendar else None,
        "mean_option_return_pct":round(sum(returns)/len(returns),3) if vals else None,
        "winning_trades_pct":round(100*sum(x>0 for x in vals)/len(vals),2) if vals else None,
        "positive_sessions":sum(daily[d]>0 for d in calendar),
        "negative_sessions":sum(daily[d]<0 for d in calendar),
        "flat_or_no_trade_sessions":sum(daily[d]==0 for d in calendar),
        "cumulative_closed_trade_pnl_drawdown_usd_NOT_ACCOUNT":round(drawdown,2),
    }


def run(root: Path, output_dir: Path) -> dict:
    files=_research_files(root)
    count=defaultdict(int)
    days=[]
    records=[]
    for path in files:
        frames=list(load_canonical_path(path))
        if not frames:
            continue
        day=frames[0].timestamp.astimezone(ET).date().isoformat()
        days.append(day)
        classified=session_classifications(frames)
        for variant in VARIANTS:
            # Apply the entry filter BEFORE the non-overlap lock. Do not
            # retain locks from signals that are now disqualified.
            chosen=select([x for x in classified if eligible(x,variant)],
                         "momentum_reversal_four_factors")
            count[variant+"_accepted"]+=len(chosen)
            for signal in chosen:
                op=signal.opportunity
                entry=_select_entry(frames,datetime.fromisoformat(op.signal_at),
                                    op.original_direction)
                if entry is None:
                    count[variant+"_missing_quote"]+=1
                    continue
                at_index,option=entry
                trace=_sampled_path(frames,at_index,option.symbol)
                if trace is None:
                    count[variant+"_missing_exit_trace"]+=1
                    continue
                pnl=simulate(option.ask,trace,TRAIL_PCT)
                records.append({
                    "day":day,"period":period(day),"variant":variant,
                    "signal_at":op.signal_at,"setup":op.setup,
                    "right":op.original_direction,"symbol":option.symbol,
                    "score":signal.factor_score,
                    "volume_ratio":round(op.volume_ratio_at_signal,5),
                    "pnl_usd":pnl["pnl_usd"],
                    "option_return_pct":pnl["return_on_premium_pct"],
                    "exit_reason":pnl["exit_reason"],
                })
    days=sorted(set(days))
    results={v:{p:_summarize(records,days,v,p) for p in PRIORITY} for v in VARIANTS}
    # Select purely on DEVELOPMENT, ignoring validation and diagnostic.
    # All results still require an untouched holdout before production.
    minimum_development_trades=30
    eligible_variants=[v for v in VARIANTS
        if results[v]["development"]["matched_trades"]>=minimum_development_trades]
    leader=max(eligible_variants,key=lambda v:(
        results[v]["development"]["sum_pnl_per_calendar_session_usd_NOT_ACCOUNT"],
        v=="base_four_factor")) if eligible_variants else None
    report={
        "study":"FROZEN_30PCT_TRAIL_ASOF_ENTRY_FILTER_ABLATIONS_LEGACY_MINUTE_CBBO",
        "file_count":len(files),"calendar_sessions":len(days),
        "stop_pct":TRAIL_PCT,"full_position_exit":True,"no_profit_target":True,
        "no_partial_exit":True,"trading_orders_or_data_purchases":0,
        "five_factor_full_SPY_PCR_NOT_TESTED":True,
        "event_timestamped_true_executable_nbbo":False,
        "untouched_holdout":False,"model_selection":"development_only",
        "minimum_development_matched_trades_for_ranking":minimum_development_trades,
        "development_eligible_variants":eligible_variants,
        "development_leader_NOT_VALIDATED":leader,
        "counts":dict(count),"results":results,
        "limitations":"Prior-inspected snapshots, model Greeks, no quote size, no full-chain PCR, no realistic account portfolio and no true option fill prices.",
    }
    output_dir.mkdir(parents=True,exist_ok=True)
    (output_dir/"report.json").write_text(json.dumps(report,indent=2,sort_keys=True)+"\n")
    with (output_dir/"matched_entries.csv").open("w",newline="") as f:
        fields=list(records[0]) if records else ["day","variant","pnl_usd"]
        writer=csv.DictWriter(f,fieldnames=fields)
        writer.writeheader()
        writer.writerows(records)
    print("ENTRY30_STUDY_RESULT "+json.dumps(report,sort_keys=True),flush=True)
    return report


def main() -> None:
    parser=argparse.ArgumentParser()
    parser.add_argument("--data-dir",type=Path,default=Path("/data/research"))
    parser.add_argument("--output-dir",type=Path,default=Path("/data/research/entry30_study"))
    args=parser.parse_args()
    run(args.data_dir,args.output_dir)

if __name__=="__main__":
    main()
