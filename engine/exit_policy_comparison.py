"""Offline side-by-side exit sensitivity on already-licensed legacy SPY minute snapshots.

NOT a real-fill / event-NBBO / full five-factor 0DTE backtest. Archived
Databento OPRA cbbo-1m bucket quotes and modeled Greeks lack independent
quote-observation events, sizes and complete SPY-specific PCR history.
No API calls, orders, purchases, live or paper-account state changes.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from .burst_research import _research_files
from .data import HistoricalFrame, load_canonical_path
from .spy_pcr_momentum_reversal import session_classifications, select
from .free_signal_screen import ET

STARTING_CONTRACTS = (1, 4)
FEE = 0.65
ADVERSE_OPTION_SLIPPAGE_PER_SHARE = 0.01
MAX_HOLD_MIN = 10
CLOSE_CUTOFF_MIN = 10
MIN_DELTA, MAX_DELTA = .40, .60
MAX_SPREAD_FRACTION = .15


def _liquid(row) -> bool:
    return bool(row.bid > 0 and row.ask > row.bid and
                MIN_DELTA <= abs(row.delta) <= MAX_DELTA and
                row.spread_fraction <= MAX_SPREAD_FRACTION)


def _select_entry(frames: list[HistoricalFrame], signal_time: datetime,
                  right: str):
    """First available complete minute sample >=60s after the known signal."""
    for i, frame in enumerate(frames):
        observed_after = frame.timestamp + timedelta(minutes=1)
        if observed_after < signal_time + timedelta(minutes=1):
            continue
        if observed_after > signal_time + timedelta(minutes=2):
            break
        eligible = [q for q in frame.options if q.right == right and _liquid(q)]
        if not eligible:
            continue
        selected = min(eligible, key=lambda q: (
            abs(abs(q.delta) - .5), q.spread_fraction,
            abs(q.strike - frame.market.spot), q.symbol,
        ))
        return i, selected
    return None


def _sampled_path(frames: list[HistoricalFrame], at_idx: int, symbol: str):
    entry = frames[at_idx]
    entry_time = entry.timestamp + timedelta(minutes=1)
    stop_time = entry_time + timedelta(minutes=MAX_HOLD_MIN)
    trace = []
    for frame in frames[at_idx + 1:]:
        observed_after = frame.timestamp + timedelta(minutes=1)
        if observed_after > stop_time + timedelta(minutes=1):
            break
        if observed_after <= entry_time:
            continue
        row = frame.option(symbol)
        if row is None or row.bid <= 0 or row.ask < row.bid:
            continue
        trace.append((observed_after, row.bid))
    # Need a real sampled bid at time limit (within one minute) or do not
    # quietly mark profitable survivors at a stale, convenient quote.
    if not trace or abs((trace[-1][0] - stop_time).total_seconds()) > 60:
        return None
    return trace


def simulate(entry_ask: float, trace, *, mode: str, contracts: int):
    entry_cost = (entry_ask + ADVERSE_OPTION_SLIPPAGE_PER_SHARE) * 100 + FEE
    peak = entry_cost
    remaining = contracts
    already_target_sold = 0
    proceeds = 0.0
    exit_reason_counts: dict[str, int] = {}
    stop_time = trace[-1][0]
    close_times = []
    for at, bid in trace:
        credit = max(0, (bid - ADVERSE_OPTION_SLIPPAGE_PER_SHARE) * 100 - FEE)
        peak = max(peak, credit)
        is_last = at >= stop_time
        if mode == "old_50":
            if credit <= entry_cost * .65:
                reason = "hard_stop_35"
            elif credit >= entry_cost * 1.5:
                reason = "take_profit_50"
            elif is_last:
                reason = "max_hold"
            else:
                continue
            count = remaining
        else:
            if credit <= peak * .80 + 1e-8:
                reason = "trailing_stop_20"
            elif credit <= entry_cost * .65:
                reason = "hard_stop_35"
            elif credit >= entry_cost * 2 and already_target_sold == 0:
                reason = "take_profit_100"
            elif is_last:
                reason = "max_hold"
            else:
                continue
            if reason == "take_profit_100" and contracts >= 3:
                count = min(remaining, contracts - 1, math.ceil(.75 * contracts))
                already_target_sold += count
            else:
                count = remaining
        remaining -= count
        proceeds += count * credit
        exit_reason_counts[reason] = exit_reason_counts.get(reason, 0) + count
        close_times.append(at.isoformat())
        if remaining <= 0:
            break
    if remaining:
        raise RuntimeError("unsettled positions must not be silently discarded")
    invested = contracts * entry_cost
    return {
        "pnl": round(proceeds - invested, 6),
        "premium_return_pct": round(100*(proceeds-invested)/invested, 6),
        "reasons": exit_reason_counts,
        "target_sold": already_target_sold,
        "exit_times": close_times,
    }


def _summary(records: list[dict], policy: str, contracts: int):
    matched = [x for x in records if x["policy"] == policy and
               x["contracts"] == contracts]
    out = {}
    for key in ("old_50", "new_100_trail20"):
        pnls = [x[key]["pnl"] for x in matched]
        if not pnls:
            out[key] = None
            continue
        running = peak = dd = 0.0
        for val in pnls:
            running += val
            peak = max(peak, running)
            dd = max(dd, peak - running)
        n=len(pnls)
        out[key] = {
            "paired_sampled_trades": n,
            "net_usd_on_fixed_contract_research_units": round(sum(pnls), 2),
            "mean_pnl_usd": round(sum(pnls)/n, 2),
            "win_rate_pct": round(100*sum(v>0 for v in pnls)/n, 2),
            "mean_premium_return_pct": round(sum(x[key]["premium_return_pct"] for x in matched)/n, 2),
            "max_closed_trade_cumulative_pnl_drawdown_usd_NOT_ACCOUNT": round(dd, 2),
        }
    return out


def run(root: Path, output: Path) -> dict:
    paths = _research_files(root)
    coverage = defaultdict(int)
    trades = []
    detail = []
    days = set()
    for path in paths:
        frames = list(load_canonical_path(path))
        if not frames:
            coverage["empty_session_files"] += 1
            continue
        day = frames[0].timestamp.astimezone(ET).date().isoformat()
        if any(f.timestamp.astimezone(ET).date().isoformat()!=day for f in frames):
            coverage["cross_session_files"] += 1
            continue
        days.add(day)
        coverage["minute_sample_frames"] += len(frames)
        classified = session_classifications(frames)
        coverage["candidate_signals"] += len(classified)
        for policy in ("prior_all_three_families", "momentum_reversal_four_factors"):
            signals = select(classified, policy)
            coverage[f"{policy}_accepted"] += len(signals)
            for pick in signals:
                op=pick.opportunity
                at=datetime.fromisoformat(op.signal_at)
                chosen=_select_entry(frames, at, op.original_direction)
                if chosen is None:
                    coverage[f"{policy}_missing_entry_quote"] += 1
                    continue
                idx, q=chosen
                history=_sampled_path(frames, idx, q.symbol)
                if history is None:
                    coverage[f"{policy}_missing_complete_exit_path"] += 1
                    continue
                coverage[f"{policy}_paired_comparable"] += 1
                for contracts in STARTING_CONTRACTS:
                    old=simulate(q.ask, history, mode="old_50", contracts=contracts)
                    new=simulate(q.ask, history, mode="new_100_trail20", contracts=contracts)
                    trades.append({
                        "date":day, "signal":op.signal_at, "setup":op.setup,
                        "side":op.original_direction, "symbol":q.symbol,
                        "entry_sample_bucket":frames[idx].timestamp.isoformat(),
                        "entry_ask":q.ask, "contracts":contracts, "policy":policy,
                        "old_50":old, "new_100_trail20":new,
                    })
    for row in trades:
        detail.append({
            "date":row["date"],"signal":row["signal"],"setup":row["setup"],
            "side":row["side"],"symbol":row["symbol"],"contracts":row["contracts"],
            "policy":row["policy"],"entry_sample_bucket":row["entry_sample_bucket"],
            "entry_ask":row["entry_ask"],
            "old_net_pnl":row["old_50"]["pnl"],
            "new_net_pnl":row["new_100_trail20"]["pnl"],
            "delta_net_pnl":round(row["new_100_trail20"]["pnl"]-row["old_50"]["pnl"],6),
            "old_reasons":json.dumps(row["old_50"]["reasons"],sort_keys=True),
            "new_reasons":json.dumps(row["new_100_trail20"]["reasons"],sort_keys=True),
        })
    report={
        "status":"SAMPLED_CBBO_EXIT_SENSITIVITY_ONLY" if trades else "INSUFFICIENT_MATCHED_SAMPLED_QUOTES",
        "source":"already_cached_historical_SPY_OPRA_cbbo-1m_minute_bucket_snapshots",
        "full_algo_status":"NOT_TESTED_MISSING_POINT_IN_TIME_COMPLETE_SPY_PCR_AND_EVENT_QUOTES",
        "true_event_observed_nbbo":False,
        "modeled_greeks_no_raw_open_interest_bid_size_proof":True,
        "real_option_account_return":None,
        "real_option_account_max_drawdown":None,
        "untouched_holdout":False,
        "new_data_purchases_usd":0,
        "archive_files":len(paths), "archive_days":len(days),
        "coverage":dict(coverage),
        "fees_per_contract_per_side_usd":FEE,
        "slippage_per_share_per_side_usd":ADVERSE_OPTION_SLIPPAGE_PER_SHARE,
        "fixed_hold_minutes":MAX_HOLD_MIN,
        "matched_results":{
            policy:{
                str(contracts):_summary(trades,policy,contracts)
                for contracts in STARTING_CONTRACTS
            } for policy in ("prior_all_three_families","momentum_reversal_four_factors")
        },
        "caution":"Entries use next-minute sampled CBBO buckets; those are NOT causal event NBBO or guaranteed fills. No full PCR input. Missing paths are excluded and explicitly counted; this is NOT a valid full strategy profitability backtest.",
    }
    output.mkdir(parents=True,exist_ok=True)
    (output/"report.json").write_text(json.dumps(report,indent=2,sort_keys=True)+"\n")
    if detail:
        with (output/"matched_trade_exit_comparison.csv").open("w",newline="") as f:
            writer=csv.DictWriter(f,fieldnames=tuple(detail[0].keys()))
            writer.writeheader()
            writer.writerows(detail)
    return report


def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument("--data-dir",type=Path,default=Path("/data/research"))
    parser.add_argument("--output-dir",type=Path,default=Path("/data/research/exit_policy_comparison"))
    args=parser.parse_args(argv)
    report=run(args.data_dir,args.output_dir)
    print("EXIT COMPARISON REPORT: "+json.dumps(report,sort_keys=True),flush=True)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
