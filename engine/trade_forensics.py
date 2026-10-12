"""Why do sampled SPY 0DTE options actually lose? Descriptive post-trade audit.

Takes the LOCKED trade ledger from Net Edge (not the unconstrained
unselected full candidate universe) and rejoins each simulated entry and
exit to its archived option/underlying minute snapshots.

Forensic labels rely on FUTURE price movement and can NEVER be used
at entry. NO model fitting, tuned entry thresholds or profit strategy
selection. No orders, no external API/data purchases, no app modification.

Important: historical CBBO-1m buckets are not verified event-time NBBO.
Bid/ask and simulated exit attribution are MODELED, not real fills.
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
from collections import defaultdict,Counter
from datetime import datetime,timedelta,time
from pathlib import Path

from .burst_research import _research_files
from .data import load_canonical_path
from .free_signal_screen import ET
from .trailing_only_sweep import simulate,period

FEE_PER_SIDE_USD=.65
ADVERSE_SLIPPAGE_PER_SHARE=.01
FROZEN_TRAIL_PCT=30
ADDITIONAL_FRICTION_STRESS_USD=2.
PRIMARY_ARMS=("all_setups_one_at_a_time",
              "option_friction_only",
              "prior_analog_positive_net",
              "prior_analog_net_plus_friction")
WINDOWS=("open_0930_1029","late_morning_1030_1059",
         "midday_1100_1259","afternoon_1300_1524")


def spot_attribution(side:str,spot_enter:float,spot_exit:float,
                     gross_mid_pnl_usd:float,net_pnl_usd:float)->str:
    """Mutually exclusive *post-hoc* diagnostic categories, not forecasts."""
    if side not in ("call","put"):
        raise ValueError("invalid option side")
    signed_bp=(spot_exit/spot_enter-1)*10000 * (1 if side=="call" else -1)
    if signed_bp < -1.:
        return "wrong_underlying_direction"
    if net_pnl_usd <=0 and gross_mid_pnl_usd>0:
        return "gross_mid_gain_erased_by_modeled_execution_costs"
    if signed_bp>1. and net_pnl_usd<=0:
        return "correct_spot_direction_but_option_lost"
    if abs(signed_bp)<=1. and net_pnl_usd<=0:
        return "flat_underlying_option_lost"
    return "other_including_winning_trade"


def session_window(entry_time:datetime)->str:
    clock=entry_time.astimezone(ET).time()
    if clock<time(10,30):return WINDOWS[0]
    if clock<time(11,0):return WINDOWS[1]
    if clock<time(13,0):return WINDOWS[2]
    return WINDOWS[3]


def find_quote(frames,by_bar_time:dict[datetime,int],
               observed_at:datetime,symbol:str):
    index=by_bar_time.get(observed_at)
    if index is None:return None
    row=frames[index].option(symbol)
    return (frames[index],row) if row is not None else None


def inspect_trade(row:dict,frames,by_bar:dict)->dict|None:
    """Use exit-time snapshots ONLY for labeling an already-placed trade."""
    entrance=datetime.fromisoformat(row["entry_at"])
    liquidation=datetime.fromisoformat(row["exit_at"])
    entry=find_quote(frames,by_bar,entrance,row["symbol"])
    exitq=find_quote(frames,by_bar,liquidation,row["symbol"])
    if entry is None or exitq is None:
        return None
    entry_frame,start=entry
    exit_frame,end=exitq
    if entrance>liquidation:
        raise AssertionError("exit occurred before entry")
    entry_debit=(start.ask+ADVERSE_SLIPPAGE_PER_SHARE)*100+FEE_PER_SIDE_USD
    exit_credit=max(0,(end.bid-ADVERSE_SLIPPAGE_PER_SHARE)*100-FEE_PER_SIDE_USD)
    net_profit=exit_credit-entry_debit
    historical_pnl=float(row["pnl_usd"])
    if abs(historical_pnl-net_profit)>.011:
        raise ValueError("reconstructed option proceeds differ from canonical prior result")
    gross_mid_gain=100*((end.bid+end.ask)/2-(start.bid+start.ask)/2)
    spread_and_fees_drag=gross_mid_gain-net_profit
    # Net option change is (exit option mid - entry option mid)
    # minus half the sampled spreads at ENTRY and EXIT, fees and slippage.
    half_spreads=50*((start.ask-start.bid)+(end.ask-end.bid))
    explicit_fee_slip=2*FEE_PER_SIDE_USD+2*ADVERSE_SLIPPAGE_PER_SHARE*100
    # In a zero-credit nonnegative bid case the floor can change drag.
    mid_entry_cost=start.ask*100
    recorded_exit_reason=None
    # Infer stop reason from precisely the archived no-target path;
    # NEVER use the future trace to decide whether to take this trade.
    from .exit_policy_comparison import _sampled_path
    at_idx=by_bar[entrance]
    trace=_sampled_path(frames,at_idx,row["symbol"])
    sample_return=None
    peak_option_bid_return_before_exit=None
    if trace:
        modeled=simulate(start.ask,trace,FROZEN_TRAIL_PCT)
        if modeled["exit_time"]!=row["exit_at"]:
            raise ValueError("modeled liquidate timestamp inconsistent with prior trade ledger")
        recorded_exit_reason=modeled["exit_reason"]
        upto=[credit for when,bid in trace if when<=liquidation
             for credit in (max(0,(bid-ADVERSE_SLIPPAGE_PER_SHARE)*100-FEE_PER_SIDE_USD),)]
        if upto:
            peak_option_bid_return_before_exit=round(max(upto)-entry_debit,2)
        sample_return=round(modeled["return_on_premium_pct"],3)
    right=row["side"]
    sign=1 if right=="call" else -1
    signed_spy_bp=sign*(exit_frame.market.spot/entry_frame.market.spot-1)*1e4
    why=spot_attribution(right,entry_frame.market.spot,exit_frame.market.spot,
                         gross_mid_gain,net_profit)
    return {
        "day":row["day"],"arm":row["arm"],"expert":row["expert"],
        "entry_at":row["entry_at"],"exit_at":row["exit_at"],
        "symbol":row["symbol"],"side":right,
        "pnl_usd":round(net_profit,2),
        "option_mid_to_mid_usd":round(gross_mid_gain,2),
        "modeled_execution_drag_usd":round(spread_and_fees_drag,2),
        "quoted_half_spreads_sum_usd":round(half_spreads,2),
        "fees_and_adverse_slippage_usd":round(explicit_fee_slip,2),
        "option_entry_ask_usd":round(start.ask*100,2),
        "option_entry_debit_usd":round(entry_debit,2),
        "entry_spread_pct":round(100*start.spread_fraction,3),
        "entry_volume":start.volume,
        "entry_open_interest":start.open_interest,
        "underlying_signed_close_change_bp":round(signed_spy_bp,3),
        "underlying_was_correct_over_1bp":signed_spy_bp>1.,
        "entry_RVOL_proxy":entry_frame.market.volume_ratio,
        "entry_IV_to_realized_proxy":round(
            start.implied_volatility/entry_frame.market.realized_volatility,3)
            if entry_frame.market.realized_volatility>0 else None,
        "historical_exit_reason":recorded_exit_reason,
        "mfe_in_bid_usd_before_exit":peak_option_bid_return_before_exit,
        "premium_return_pct":sample_return,
        "time_bucket":session_window(entrance),
        "post_trade_loss_diagnosis":why,
        "period":period(row["day"]),
    }


def group_statistics(entries:list[dict])->dict:
    if not entries:return {"trades":0,"win_rate_pct":None,"pnl_usd":0.}
    pnls=[e["pnl_usd"] for e in entries]
    losses=[e for e in entries if e["pnl_usd"]<=0]
    winners=[e for e in entries if e["pnl_usd"]>0]
    groups=defaultdict(list)
    for e in losses:groups[e["post_trade_loss_diagnosis"]].append(e)
    return {
        "trades":len(entries),
        "wins":len(winners),"losses_or_breakevens":len(losses),
        "win_rate_pct":round(100*len(winners)/len(entries),2),
        "pnl_usd":round(sum(pnls),2),
        "extra_2usd_roundtrip_cost_stress_sum_usd":
            round(sum(pnls)-ADDITIONAL_FRICTION_STRESS_USD*len(entries),2),
        "mean_trade_pnl_usd":round(statistics.fmean(pnls),2),
        "mean_winner_usd":round(statistics.fmean(
            e["pnl_usd"] for e in winners),2) if winners else None,
        "mean_loser_or_flat_usd":round(statistics.fmean(
            e["pnl_usd"] for e in losses),2) if losses else None,
        "mean_modeled_execution_drag_usd":round(
            statistics.fmean(e["modeled_execution_drag_usd"] for e in entries),2),
        "modeled_execution_drag_total_usd":round(
            sum(e["modeled_execution_drag_usd"] for e in entries),2),
        "summed_mid_to_mid_usd":round(
            sum(e["option_mid_to_mid_usd"] for e in entries),2),
        "entry_spread_pct_average":round(
            statistics.fmean(e["entry_spread_pct"] for e in entries),3),
        "mid_up_but_realized_net_nonpositive_count":sum(
            e["option_mid_to_mid_usd"]>0 and e["pnl_usd"]<=0 for e in entries),
        "correct_spy_direction_but_option_net_nonpositive_count":sum(
            e["underlying_was_correct_over_1bp"] and e["pnl_usd"]<=0 for e in entries),
        "loss_types":{
            label:{"trades":len(cases),"net_pnl_usd":round(sum(x["pnl_usd"] for x in cases),2)}
            for label,cases in sorted(groups.items())},
        "exit_reason_counts":dict(Counter(e["historical_exit_reason"] for e in entries)),
        "winning_bid_runup_before_exit_but_exit_loss_count":sum(
            e["pnl_usd"]<=0 and (e["mfe_in_bid_usd_before_exit"] or -1)<=0 for e in []), # computed below
    }


def correct_runup(entries:list[dict])->int:
    """Losing trades that briefly showed net-liquidation gain before exit."""
    return sum(e["pnl_usd"]<=0 and
               e["mfe_in_bid_usd_before_exit"] is not None and
               e["mfe_in_bid_usd_before_exit"]>0 for e in entries)


def summarize_arm(rows:list[dict],arm:str)->dict:
    data=[x for x in rows if x["arm"]==arm]
    result=group_statistics(data)
    result["losers_previously_showed_positive_bid_MFE"]=correct_runup(data)
    result["by_expert"]={
        k:group_statistics([x for x in data if x["expert"]==k])
        for k in sorted({x["expert"] for x in data})
    }
    result["by_time_bucket"]={
        k:group_statistics([x for x in data if x["time_bucket"]==k])
        for k in WINDOWS
    }
    result["by_period"]={
        k:group_statistics([x for x in data if x["period"]==k])
        for k in ("development","validation","previously_studied_diagnostic")
    }
    return result


def run(source:Path,ledger_path:Path,destination:Path)->dict:
    with ledger_path.open(newline="") as f:
        ledger=list(csv.DictReader(f))
    by_day=defaultdict(list)
    for row in ledger:
        by_day[row["day"]].append(row)
    recorded=[]
    coverage=Counter()
    market_days=0
    for source_file in _research_files(source):
        frames=list(load_canonical_path(source_file))
        if not frames:continue
        day=frames[0].timestamp.astimezone(ET).date().isoformat()
        market_days+=1
        by_bar={f.timestamp+timedelta(minutes=1):i for i,f in enumerate(frames)}
        for row in by_day[day]:
            got=inspect_trade(row,frames,by_bar)
            if got is None:coverage["unmatched_entry_or_exit_quote"]+=1
            else:
                recorded.append(got)
                coverage["matched_sampled_trade"]+=1
    report={
        "research_mode":"POST_TRADE_FORENSIC_DIAGNOSTIC_NOT_ENTRY_MODEL",
        "archived_market_sessions":market_days,
        "portfolio_starting_capital_scenario_usd":1000.0,
        "actual_account_simulation":False,
        "total_historical_ledger_trades":len(ledger),
        "matched_historical_cases":len(recorded),
        "coverage":dict(coverage),
        "arms":{arm:summarize_arm(recorded,arm) for arm in PRIMARY_ARMS},
        "method":"Rejoin archived option ask-at-entry and bid-at-exit; compare modeled ask-to-bid PnL with simultaneous synthetic mid-to-mid change and actual SPY sampled exit-direction; group by setup and time.",
        "frozen_exit":"30% whole-position trail + 35% initial loss + 10-minute cap; NO take profits",
        "warning":[
            "FUTURE outcomes are used for diagnostic LABELS only: NEVER use them as entry features.",
            "SPY close movement at option entry-to-exit is descriptive; cannot isolate an execution-causal trade-loss reason.",
            "Mid-to-mid option mark is hypothetical. Fees+spread decomposition is mechanical, not a verified achievable mid fill.",
            "Minute-bucket CBBO option quotes are not executable event-timestamped NBBO; intraminute stops unknown.",
            "Previously inspected 128 sessions; neither stable edge nor independent forward test proven.",
            "$1k figure is a comparison label from earlier study; per-contract diagnostics do NOT replay cash affordability.",
        ],
        "orders_placed":0,
        "data_purchased":0,
    }
    destination.mkdir(parents=True,exist_ok=True)
    (destination/"report.json").write_text(json.dumps(report,sort_keys=True,indent=2)+"\n")
    fields=list(recorded[0]) if recorded else ["day","arm","pnl_usd"]
    with (destination/"trade_forensics.csv").open("w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(recorded)
    print("TRADE_FORENSICS_RESULT "+json.dumps(report,sort_keys=True),flush=True)
    return report


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--data-dir",type=Path,default=Path("/data/research"))
    p.add_argument("--ledger",type=Path,default=Path("/data/research/option_net_edge/trades.csv"))
    p.add_argument("--output-dir",type=Path,default=Path("/data/research/trade_forensics"))
    a=p.parse_args()
    run(a.data_dir,a.ledger,a.output_dir)


if __name__=="__main__":
    main()
