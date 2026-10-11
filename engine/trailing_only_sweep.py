"""Exploratory whole-position trailing-stop sensitivity; NO trading, NO data purchases.

All exit variants use identical archived SPY 0DTE signal candidates / options.
Only previously downloaded minute-bucket CBBO quotes are used, NOT true event
NBBO and not a full-chain point-in-time five-factor strategy.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

from .burst_research import _research_files
from .data import load_canonical_path
from .exit_policy_comparison import _sampled_path, _select_entry
from .free_signal_screen import ET, PERIODS
from .spy_pcr_momentum_reversal import session_classifications, select

TRAIL_PCTS = tuple(range(20, 91, 5))
ARMS = TRAIL_PCTS + (None,)  # None: independent hard stop only
FEE_PER_SIDE = 0.65
SLIP_PER_SHARE = 0.01
HARD_STOP = 0.35
EXIT_MINUTES = 10


def policy_name(pct: int | None) -> str:
    return f"trail_{pct}pct" if pct is not None else "no_trailing"


def simulate(ask: float, path, trail_pct: int | None) -> dict:
    """Return entire position at one sampled bid; no target, scaling or runner."""
    if not path or (trail_pct is not None and not 0 < trail_pct < 100):
        raise ValueError("invalid trace or percentage")
    entry = (ask + SLIP_PER_SHARE) * 100.0 + FEE_PER_SIDE
    peak = entry
    for i, (at, bid) in enumerate(path):
        exit_value = max(0.0, (bid - SLIP_PER_SHARE) * 100.0 - FEE_PER_SIDE)
        peak = max(peak, exit_value)
        if exit_value <= entry * (1.0 - HARD_STOP) + 1e-9:
            why = "independent_hard_stop_35pct"
        elif trail_pct is not None and exit_value <= peak * (1.0 - trail_pct / 100.0) + 1e-9:
            why = "trailing_stop"
        elif i == len(path) - 1:
            why = "hard_max_hold_10min"
        else:
            continue
        return {"pnl_usd": round(exit_value - entry, 6),
                "return_on_premium_pct": round(100.0*(exit_value/entry - 1),6),
                "exit_reason": why, "exit_time":at.isoformat()}
    raise RuntimeError("position remained unsettled")


def period(day: str) -> str:
    d = date.fromisoformat(day)
    return next((name for name, a, b in PERIODS if a <= d <= b), "unclassified")


def summarize(rows: list[dict], days: list[str], arm: str, group: str):
    selected = [r for r in rows if group == "all_inspected" or r["period"] == group]
    if not selected:
        return None
    values = [r["policies"][arm]["pnl_usd"] for r in selected]
    returns = [r["policies"][arm]["return_on_premium_pct"] for r in selected]
    equity = peak = drawdown = 0.
    daily = defaultdict(float)
    for row, pnl in zip(selected, values):
        equity += pnl
        peak = max(peak, equity)
        drawdown = max(drawdown, peak-equity)
        daily[row["day"]] += pnl
    relevant_days = [d for d in days if group == "all_inspected" or period(d) == group]
    return {
        "trades":len(values),
        "calendar_sessions_including_no_trade":len(relevant_days),
        "one_contract_aggregate_pnl_usd_NOT_ACCOUNT":round(sum(values),2),
        "mean_trade_pnl_usd":round(sum(values)/len(values),3),
        "mean_option_premium_return_pct":round(sum(returns)/len(returns),3),
        "win_rate_pct":round(100*sum(v>0 for v in values)/len(values),2),
        "positive_days":sum(daily[d]>0 for d in relevant_days),
        "negative_days":sum(daily[d]<0 for d in relevant_days),
        "no_trade_or_flat_days":sum(daily[d]==0 for d in relevant_days),
        "additive_closed_trade_pnl_drawdown_usd_NOT_ACCOUNT":round(drawdown,2),
        "exit_reasons":dict(sorted({why:sum(r["policies"][arm]["exit_reason"]==why for r in selected)
                        for why in ("trailing_stop","independent_hard_stop_35pct","hard_max_hold_10min")}.items())),
    }


def bootstrap_difference(rows: list[dict], days: list[str], alt: str, baseline: str):
    vals={day:0. for day in days}
    for r in rows:
        vals[r["day"]] += r["policies"][alt]["pnl_usd"] - r["policies"][baseline]["pnl_usd"]
    seq=[vals[day] for day in days]
    if not seq:
        return None
    randomizer=random.Random(20261011)
    means=[]
    for _ in range(1000):
        sample=[]
        while len(sample)<len(seq):
            start=randomizer.randrange(len(seq))
            sample.extend(seq[(start+i)%len(seq)] for i in range(min(5,len(seq))))
        means.append(sum(sample[:len(seq)])/len(seq))
    means.sort()
    return {
        "difference_total_usd":round(sum(seq),2),
        "paired_5session_block_bootstrap_95pct_ci_usd_per_calendar_day":[round(means[24],3),round(means[974],3)],
    }


def run(root: Path, output: Path) -> dict:
    matched=[]
    coverage=defaultdict(int)
    days=[]
    files=_research_files(root)
    for path in files:
        frames=list(load_canonical_path(path))
        if not frames:
            coverage["empty_sessions"]+=1
            continue
        day=frames[0].timestamp.astimezone(ET).date().isoformat()
        days.append(day)
        coverage["one_minute_frames"]+=len(frames)
        candidates=select(session_classifications(frames),"momentum_reversal_four_factors")
        coverage["accepted_signals"]+=len(candidates)
        for classified in candidates:
            signal=classified.opportunity
            result=_select_entry(frames,datetime.fromisoformat(signal.signal_at),signal.original_direction)
            if result is None:
                coverage["no_matching_option_entry"]+=1
                continue
            index,option=result
            trace=_sampled_path(frames,index,option.symbol)
            if trace is None:
                coverage["no_matched_10min_exit_path"]+=1
                continue
            policies={policy_name(p):simulate(option.ask,trace,p) for p in ARMS}
            matched.append({"day":day,"period":period(day),"signal_time":signal.signal_at,
                            "contract":option.symbol,"direction":signal.original_direction,
                            "entry_ask":option.ask,"policies":policies})
            coverage["matched_option_scenarios"]+=1
    days=sorted(set(days))
    groups=("development","validation","previously_studied_diagnostic","all_inspected")
    tables={policy_name(p):{g:summarize(matched,days,policy_name(p),g) for g in groups} for p in ARMS}
    development=[p for p in ARMS if tables[policy_name(p)]["development"] is not None]
    chosen=max(development,key=lambda p:(tables[policy_name(p)]["development"]
        ["one_contract_aggregate_pnl_usd_NOT_ACCOUNT"],-(p if p is not None else 100))) if development else None
    by_all=sorted(development,key=lambda p:tables[policy_name(p)]["all_inspected"]
        ["one_contract_aggregate_pnl_usd_NOT_ACCOUNT"],reverse=True)
    report={
        "study":"TRAILING_ONLY_MINUTE_CBBO_SENSITIVITY_NOT_AN_EXECUTION_BACKTEST",
        "sessions":len(days),"file_count":len(files),"coverage":dict(coverage),
        "arms":list(TRAIL_PCTS)+["no_trailing"],
        "full_position_exit":True,"take_profit_targets":False,"partial_sells":False,
        "independent_hard_loss_fraction":HARD_STOP,"hard_max_hold_minutes":EXIT_MINUTES,
        "fee_per_contract_per_side_usd":FEE_PER_SIDE,"adverse_slippage_per_share_per_side_usd":SLIP_PER_SHARE,
        "selected_by_development_pnl_only":policy_name(chosen) if development else None,
        "inspected_all_sessions_hindsight_ranking":[policy_name(p) for p in by_all],
        "tables":tables,
        "paired_difference_vs_trail20":{policy_name(p):bootstrap_difference(matched,days,policy_name(p),policy_name(20)) for p in ARMS if p != 20},
        "full_chain_point_in_time_pcr":False,"real_event_timestamped_nbbo":False,
        "actual_account_returns":False,"data_purchases_usd":0.0,
        "note":"Already-inspected historical periods, no untouched holdout; comparisons are sampled quote scenarios, not broker fills. No account cash or settlement constraints.",
    }
    output.mkdir(parents=True,exist_ok=True)
    (output/"report.json").write_text(json.dumps(report,indent=2,sort_keys=True)+"\n")
    with (output/"matched_cases.csv").open("w",newline="") as f:
        cols=["day","period","signal_time","contract","direction","entry_ask"]
        cols += [policy_name(p)+"_pnl_usd" for p in ARMS]
        writer=csv.DictWriter(f,fieldnames=cols)
        writer.writeheader()
        for item in matched:
            writer.writerow({**{key:item[key] for key in cols[:6]},
                **{policy_name(p)+"_pnl_usd":item["policies"][policy_name(p)]["pnl_usd"] for p in ARMS}})
    print("TRAIL_ONLY_GRID_REPORT "+json.dumps(report,sort_keys=True),flush=True)
    return report


def main() -> None:
    cli=argparse.ArgumentParser()
    cli.add_argument("--data-dir",type=Path,default=Path("/data/research"))
    cli.add_argument("--output-dir",type=Path,default=Path("/data/research/trailing_only_sweep"))
    args=cli.parse_args()
    run(args.data_dir,args.output_dir)

if __name__=="__main__":
    main()
