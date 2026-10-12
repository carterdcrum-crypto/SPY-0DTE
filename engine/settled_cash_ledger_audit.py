"""Hypothetical settled-cash overlay for archived SPY 0DTE entry ledgers.

A POST-HOC screen of ALREADY SELECTED candidate trades. Does not reconstruct
additional signals that a cash-aware scheduler might choose if an earlier
hypothetical position were skipped for insufficient settled cash.
No buying-power leverage, no same-day recycling of sales proceeds.
Starting $115 is an ILLUSTRATIVE research input, NOT verified account balance.
Not a broker execution simulator or advice to trade more frequently.
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

HYPOTHETICAL_STARTING_SETTLED_CASH_USD=115.0
STRESS_MORE_PER_TRADE_USD=2.0


def process_one(rows:list[dict],dates:list[str],starting_cash:float)->dict:
    if starting_cash<=0:
        raise ValueError("starting settled cash must be positive")
    perday=defaultdict(list)
    for case in rows:
        perday[case["day"]].append(case)
    settled=starting_cash
    prior_sale_proceeds=0.
    trades=0
    week={}
    day_records=[]
    max_cash_peak=starting_cash
    max_dd=0.
    n_affordable=0
    n_unaffordable=0
    n_other=0
    extra_friction=0.
    for day in sorted(dates):
        # SPY-option buys & sells both settle T+1. Prior sales before
        # 4pm became settled available at this day's open.
        settled+=prior_sale_proceeds
        prior_sale_proceeds=0.
        n_today=0
        for case in sorted(perday.get(day,[]),key=lambda x:x["entry_observation_at"]):
            price=float(case["entry_debit_usd"])
            pnl=float(case["pnl_usd"])
            if price<=settled+1e-9:
                n_affordable+=1
                trades+=1
                n_today+=1
                settled-=price
                proceeds=max(0.,price+pnl)
                prior_sale_proceeds+=proceeds
                extra_friction+=STRESS_MORE_PER_TRADE_USD
            else:
                n_unaffordable+=1
        equity=settled+prior_sale_proceeds
        max_cash_peak=max(max_cash_peak,equity)
        max_dd=max(max_dd,max_cash_peak-equity)
        wk=datetime.fromisoformat(day).isocalendar()[:2]
        week.setdefault(str(wk),{"start_equity_usd":None,
                                  "end_equity_usd":None,"trades":0})
        # Start of week: last completed trading-day equity.
        if week[str(wk)]["start_equity_usd"] is None:
            week[str(wk)]["start_equity_usd"]=(
                day_records[-1]["equity_usd"] if day_records else starting_cash)
        week[str(wk)]["end_equity_usd"]=round(equity,4)
        week[str(wk)]["trades"]+=n_today
        day_records.append({"day":day,"trades":n_today,
                            "settled_end_usd":round(settled,4),
                            "unsettled_sale_proceeds_usd":round(prior_sale_proceeds,4),
                            "equity_usd":round(equity,4)})
    return {
        "starting_settled_cash_usd":starting_cash,
        "realized_equity_end_usd":round(settled+prior_sale_proceeds,2),
        "modeled_equity_pnl_usd":round(settled+prior_sale_proceeds-starting_cash,2),
        "extra_2usd_per_trade_stressed_equity_pnl_USD_DIAGNOSTIC":
            round(settled+prior_sale_proceeds-starting_cash-extra_friction,2),
        "actual_modeled_entries_affordable":n_affordable,
        "entries_skipped_insufficient_same_day_settled_cash":n_unaffordable,
        "max_cash_account_closed_day_drawdown_usd":round(max_dd,2),
        "days_with_at_least_two_affordable_contract_entries":sum(
            r["trades"]>=2 for r in day_records),
        "days_with_at_least_three_affordable_contract_entries":sum(
            r["trades"]>=3 for r in day_records),
        "weeks_returning_100pct_or_more":sum(
            e["end_equity_usd"]>=2*e["start_equity_usd"] and
            e["start_equity_usd"]>0 for e in week.values()),
        "weeks_observed":len(week),
        "week_records":week,
        "post_hoc_selection_diagnostic_only":True,
    }


def audit(ledger:Path,output:Path,starting_cash:float)->dict:
    with ledger.open(newline="") as f:
        raw=list(csv.DictReader(f))
    days=sorted({row["day"] for row in raw})
    arms=sorted({row["arm"] for row in raw})
    res={arm:process_one(
        [r for r in raw if r["arm"]==arm],days,starting_cash)
        for arm in arms}
    report={
        "status":"RESEARCH_ONLY_HYPOTHETICAL_CASH_SETTLEMENT_POST_FILTER",
        "source":str(ledger),"settlement":"T+1 model; no same-day proceeds recycling",
        "starting_cash_is_illustrative_not_user_actual_balance":True,
        "missing_no_trade_sessions_from_csv":True,
        "results":res,
        "warnings":[
            "Applied AFTER trade candidate path selection, so a cash-aware alternative-entry schedule is NOT reconstructed.",
            "Prices & exits come from archived CBBO-1m sampled bid/ask, not actual orders, NBBO or proven fills.",
            "Input ledger omits days with no matched trades; weekly statistics not a full continuous market-calendar backtest.",
            "No automatic option exercises/assignment, margin, leverage, external deposits, tax, or account fees modeled.",
            "Different strategy arms have independent artificial initial cash; do not sum balances across arms.",
            "All source calendar sessions were previously inspected and are not prospective out of sample.",
        ],
    }
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,sort_keys=True,indent=2)+"\n")
    print("SETTLED_CASH_AUDIT_RESULT "+json.dumps(report,sort_keys=True),flush=True)
    return report


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--ledger",type=Path,
      default=Path("/data/research/or_retest_frequency/simulated_trades.csv"))
    p.add_argument("--output",type=Path,
      default=Path("/data/research/or_retest_frequency/cash_account_audit.json"))
    p.add_argument("--starting-cash",type=float,
      default=HYPOTHETICAL_STARTING_SETTLED_CASH_USD)
    a=p.parse_args()
    audit(a.ledger,a.output,a.starting_cash)


if __name__=="__main__":
    main()
