"""V4 optional *research-only* discrete dynamic contract sizing.

Exact V4 frozen candidate entry and exit; vary only quantity decided from
THEN-AVAILABLE settled cash, earlier-session modeled returns and drawdown.
The source remains pre-inspected SPY close + OPRA CBBO-1m minute buckets,
NOT timestamped executable NBBO and NOT a live-trading sizing policy.

Several deterministic sizing modes, including extra MICRO-ACCOUNT variants
    added after seeing that integer target-budget rounding prevented most entries,
    are compared with the
identical one-contract V4 baseline:
  * compound: 25% unreserved account premium budget, up to five contracts;
  * vault: as compound with 50% realized-profit reserve after +10% equity;
  * fractional_kelly: existing integer ES99/Kelly risk engine from previous
    session trade returns only, 2% full-premium-loss stress, no guessed
    positive edge in a new account.

All modes enforce T+1 no intraday sale proceeds recycling, cash reserve,
one option position at a time, premium-at-risk, and session-end flat.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from collections import Counter
from dataclasses import dataclass
from datetime import datetime,timedelta
from pathlib import Path

from .burst_research import _research_files
from .data import load_canonical_path
from .dynamic_risk import (
    DynamicRiskConfig, DynamicRiskRequest,
    choose_dynamic_long_option_size, smooth_drawdown_multiplier,
)
from .regime_hold_conditional import simulate_conditional
from .regime_hold_sensitivity import (
    START_CASH, MAX_FIXED_MIN, liquidation_cutoff,
    quote_path, selection,
)
from .weekly_options_data import NY

ARMS=("v4_fixed_one_contract","v4_dynamic_compound",
      "v4_dynamic_profit_vault","v4_dynamic_fractional_kelly",
      "v4_dynamic_compound_micro","v4_dynamic_vault_micro",
      "v4_dynamic_kelly_micro","v4_dynamic_streak_compound",
      "v4_dynamic_streak_vault")
MAX_CONTRACTS=5
CAPITAL_FRACTION=.25
DRAWDOWN_SOFT=.04
DRAWDOWN_HALT=.18
MIN_KELLY_PREVIOUS_TRADES=25
KELLY_HISTORY_WINDOW=50
TAIL_PREMIUM_LOSS_PROB=.02
FEE_STRESS_PER_CONTRACT=2.0


@dataclass(frozen=True)
class PositionDecision:
    contracts: int
    reason: str
    fraction: float
    reserve: float
    kelly_raw: float=0.0


def _fraction_of_drawdown(equity:float,peak:float)->float:
    return max(0.0,1-equity/peak) if peak>0 else 1.0


def choose_size(
    *,
    arm:str, equity:float, settled:float, peak:float,
    debit:float,spread:float,relative_volume:float,
    previous_returns:tuple[float,...], expiry_minutes:float,
)->PositionDecision:
    """No current/future trade outcome, future same-day proceeds, or lookahead."""
    if arm not in ARMS:
        raise ValueError("unknown sizing arm")
    if equity<=0 or settled<=0 or debit<=0 or peak<=0:
        return PositionDecision(0,"NO_CAPITAL",0,0)
    if debit>settled+1e-9:
        return PositionDecision(0,"INSUFFICIENT_T1_SETTLED_CASH",0,0)
    if arm==ARMS[0]:
        return PositionDecision(1,"FROZEN_V4_ONE_CONTRACT",debit/equity,0)

    dd=_fraction_of_drawdown(equity,peak)
    if arm in ("v4_dynamic_streak_compound","v4_dynamic_streak_vault"):
        # Preserve V4's one-contract trade participation. Only ADD integer
        # contracts when prior-session winning evidence AND healthy cash
        # permit it. Prior failures scale down to 1, rather than halt.
        reserve=0.
        if arm=="v4_dynamic_streak_vault" and equity>=START_CASH*1.10:
            reserve=max(0.,.50*(equity-START_CASH))
        available_cash=max(0.,settled-reserve)
        if available_cash<debit:
            return PositionDecision(0,"PROTECTED_CASH_BELOW_ONE_CONTRACT",0,reserve)
        recent_two=previous_returns[-2:]
        profitable_streak=len(recent_two)==2 and all(x>0 for x in recent_two)
        healthy=dd<=.04 and profitable_streak and spread<=.025 and relative_volume>=1.2
        if not healthy:
            return PositionDecision(1,"BASELINE_ONE_UNLESS_TWO_PRIOR_WINS",debit/equity,reserve)
        tier=max(1,math.floor(equity/START_CASH))
        discretionary_budget=min(available_cash,.35*equity)
        qty=min(MAX_CONTRACTS,tier,
                max(1,math.floor((discretionary_budget+1e-9)/debit)))
        return PositionDecision(qty,"HISTORICAL_PERFORMANCE_SCALED",
                                qty*debit/equity,reserve)
    multiplier=smooth_drawdown_multiplier(dd,
                                         soft_start=DRAWDOWN_SOFT,
                                         zero_level=DRAWDOWN_HALT)
    reserve=0.0
    if arm in ("v4_dynamic_profit_vault","v4_dynamic_vault_micro") and equity>=START_CASH*1.10:
        reserve=max(0.0,.50*(equity-START_CASH))
    usable_cash=max(0.0,settled-reserve)
    if usable_cash<debit:
        return PositionDecision(0,"RESERVE_OR_CASH_LESS_THAN_ONE_CONTRACT",0,reserve)
    if multiplier<=0:
        return PositionDecision(0,"DRAWDOWN_SIZE_HALT",0,reserve)
    # A predeclared quality scalar, not a fitted market forecast.
    volume_quality=max(.70,min(1.,relative_volume/1.5))
    liquidity_quality=max(.60,min(1.,(.035-spread)/.035+.60))
    quality=min(volume_quality,liquidity_quality)
    if arm in ("v4_dynamic_compound","v4_dynamic_profit_vault",
               "v4_dynamic_compound_micro","v4_dynamic_vault_micro"):
        risk_budget=min(usable_cash,equity*CAPITAL_FRACTION)
        risk_budget*=multiplier*quality
        qty=min(MAX_CONTRACTS,
                math.floor((risk_budget+1e-9)/debit),
                math.floor((usable_cash+1e-9)/debit))
        if (not qty and arm.endswith("_micro") and
                debit<=.35*equity and multiplier>=.60):
            # Integer micro-account granularity: 1 contract can exceed a
            # 25% *target* but never the explicit 35% premium hard rail.
            qty=1
            return PositionDecision(qty,"MINIMUM_VIABLE_ONE_CONTRACT",
                                    debit/equity,reserve)
        return PositionDecision(max(0,qty),
                 "APPROVED_COMPOUND" if qty else "RISK_BUDGET_BELOW_ONE_CONTRACT",
                 risk_budget/equity,reserve)

    if len(previous_returns)<MIN_KELLY_PREVIOUS_TRADES:
        if (arm=="v4_dynamic_kelly_micro" and debit<=.35*equity and multiplier>=.60):
            # Explicit paper-research bootstrap: 1-contract probation, no
            # unearned Kelly edge inference before 25 observed prior trades.
            return PositionDecision(1,"ONE_CONTRACT_KELLY_LEARNING",debit/equity,0)
        return PositionDecision(0,"INSUFFICIENT_PREVIOUS_DAYS_FOR_KELLY",0,0)
    values=tuple(previous_returns[-KELLY_HISTORY_WINDOW:])
    weights=(TAIL_PREMIUM_LOSS_PROB,)+( (1-TAIL_PREMIUM_LOSS_PROB)/len(values), )*len(values)
    scenarios=(-1.0,)+tuple(max(-1.,float(x)) for x in values)
    # Minimum observed success probability is a veto, not a promise of edge.
    positive=sum(v>0 for v in values)/len(values)
    decision=choose_dynamic_long_option_size(DynamicRiskRequest(
        account_equity=equity,high_watermark=peak,settled_cash=usable_cash,
        daily_start_equity=equity,daily_realized_pnl=0.,
        entry_cost_per_contract=debit,
        scenario_returns=scenarios,scenario_probabilities=weights,
        positive_edge_probability=positive,
        liquidity_score=quality,spread_fraction=spread,
        quote_age_seconds=60.,minutes_to_expiry=expiry_minutes,
        ask_size=0,model_health_multiplier=.85,
        event_multiplier=1.,
    ),DynamicRiskConfig(
        kelly_fraction=.25,minimum_positive_edge_probability=.50,
        maximum_contracts=MAX_CONTRACTS,trade_es99_fraction=.25,
        aggregate_es99_fraction=.25,maximum_premium_loss_fraction=.35,
        maximum_capital_fraction=.40,
        drawdown_soft_start=DRAWDOWN_SOFT,
        drawdown_zero_level=DRAWDOWN_HALT,
        maximum_daily_loss_fraction=.20,
        maximum_quote_age_seconds=61.,
        maximum_spread_fraction=.035,
        minimum_liquidity_score=.60,
        minimum_minutes_to_expiry=15.,
        allow_minimum_viable_contract=False,
    ))
    qty=min(decision.contracts,
            math.floor((usable_cash+1e-9)/debit)) if decision.allowed else 0
    if (not qty and arm=="v4_dynamic_kelly_micro" and
            decision.reason=="fractional_kelly_below_one_contract" and
            decision.hard_limited_contracts>=1 and
            decision.expected_net_return>0 and
            decision.adaptive_multiplier>=.60 and debit<=.35*equity):
        return PositionDecision(1,"MINIMUM_VIABLE_EMPIRICAL_KELLY",
                                debit/equity,reserve,decision.raw_kelly_fraction)
    return PositionDecision(qty,decision.reason,
                            qty*debit/equity,reserve,
                            decision.raw_kelly_fraction)


def summary(state:dict, *, starting_cash: float = START_CASH)->dict:
    days=state["daily"]
    equity=[starting_cash]+[d["equity_usd"] for d in days]
    peak=starting_cash
    worst_dd=0.0
    worst_peak=starting_cash
    peak_day="initial_capital"
    drawdown_peak_day=None
    trough_day=None
    for label,value in zip(["initial_capital"]+[x["day"] for x in days],equity):
        if value>peak:
            peak=value
            peak_day=label
        if peak-value>worst_dd:
            worst_dd=peak-value
            worst_peak=peak
            trough_day=label
            drawdown_peak_day=peak_day
    trades=[x for x in state["trades"] if x["pnl_usd"] is not None]
    pnl=[x["pnl_usd"] for x in trades]
    wins=[x for x in pnl if x>0]
    losses=[x for x in pnl if x<0]
    totalqty=sum(x["contracts"] for x in trades)
    ending=round(state["settled"]+state["pending"],2)
    return {
        "start_equity_usd":starting_cash,
        "ending_equity_usd":ending,
        "net_profit_usd":round(ending-starting_cash,2),
        "return_pct":round(100*(ending/starting_cash-1),2),
        "max_end_of_day_drawdown_usd":round(worst_dd,2),
        "max_end_of_day_drawdown_pct_of_preceding_peak":
            round(100*worst_dd/worst_peak,2) if worst_peak>0 else None,
        "drawdown_peak_day":drawdown_peak_day,
        "drawdown_trough_day":trough_day,
        "scored_positions":len(trades),
        "unscorable_positions":sum(x["pnl_usd"] is None for x in state["trades"]),
        "contracts_traded":totalqty,
        "mean_contracts_per_scored_trade":round(totalqty/len(trades),3) if trades else None,
        "max_contracts_in_one_trade":max((x["contracts"] for x in trades),default=0),
        "win_rate_pct":round(100*len(wins)/len(pnl),2) if pnl else None,
        "profit_factor":round(sum(wins)/abs(sum(losses)),3) if losses else None,
        "modeled_pnl_after_extra_2usd_each_contract_usd":
            round(sum(pnl)-FEE_STRESS_PER_CONTRACT*totalqty,2),
        "skipped_entries_by_reason":dict(state["skips"]),
        "exit_reasons":dict(state["exits"]),
    }


def run(data_dir:Path,output_dir:Path)->dict:
    paths=_research_files(data_dir)
    if not paths:
        raise ValueError("genuine legacy research archive required; no synthetic returns")
    states={arm:dict(settled=START_CASH,pending=0.0,
                     daily=[],trades=[],skips=Counter(),exits=Counter(),
                     realized_return_history=[])
            for arm in ARMS}
    dates=[]
    for file in paths:
        frames=list(load_canonical_path(file))
        if not frames:
            continue
        day=frames[0].timestamp.astimezone(NY).date()
        dates.append(str(day))
        entries=selection(frames)
        cutoff=liquidation_cutoff(day)
        for arm,state in states.items():
            # Settle only proceeds from the previous completed session.
            state["settled"]+=state["pending"]
            state["pending"]=0.0
            open_equity=state["settled"]
            prior_high=max([START_CASH]+[x["equity_usd"] for x in state["daily"]])
            flat=None
            todays_returns=[]
            for item in entries:
                at=item["entry_at"]
                if at>=cutoff-timedelta(minutes=MAX_FIXED_MIN+1):
                    state["skips"]["LATE_ENTRY_NEAR_EXPIRY"]+=1
                    continue
                if flat is not None and at<=flat:
                    state["skips"]["ONE_POSITION_BUSY"]+=1
                    continue
                q=item["quote"]
                debit=item["debit"]
                decision=choose_size(
                    arm=arm,equity=open_equity,settled=state["settled"],
                    peak=prior_high,debit=debit,
                    spread=q.spread_fraction,
                    relative_volume=item["signal"]["volume_ratio"],
                    previous_returns=tuple(state["realized_return_history"]),
                    expiry_minutes=(cutoff-at).total_seconds()/60+15,
                )
                if decision.contracts==0:
                    state["skips"][decision.reason]+=1
                    continue
                qty=decision.contracts
                if qty*debit>state["settled"]+1e-8:
                    raise AssertionError("T+1 settled cash constraint broken")
                # The exact same V4 event selection and first-contract exit.
                outcome=simulate_conditional(
                    q.ask,quote_path(frames,item["entry_idx"],q.symbol,cutoff),
                    frames,entry_at=at,side=item["signal"]["side"],
                    symbol=q.symbol,cutoff=cutoff)
                state["settled"]=round(state["settled"]-qty*debit,8)
                if outcome is None:
                    # No free recredit or conveniently marked winner on sparse data.
                    flat=cutoff
                    state["trades"].append({
                        "day":str(day),"arm":arm,
                        "entry_at":at.isoformat(),"symbol":q.symbol,
                        "contracts":qty,"entry_debit_usd_per_contract":debit,
                        "pnl_usd":None,"exit_at":None,
                        "exit_reason":"UNSCORABLE_MISSING_TERMINAL_QUOTE",
                        "reserved_profit_usd":decision.reserve,
                    })
                    continue
                flat=outcome["exit_at"]
                unit_pnl=outcome["pnl_usd"]
                account_pnl=round(unit_pnl*qty,6)
                proceeds=max(0.,qty*debit+account_pnl)
                state["pending"]+=proceeds
                todays_returns.append(max(-1.,unit_pnl/debit))
                state["exits"][outcome["exit_reason"]]+=1
                state["trades"].append({
                    "day":str(day),"arm":arm,
                    "entry_at":at.isoformat(),"symbol":q.symbol,
                    "setup":item["signal"]["setup"],
                    "contracts":qty,"entry_debit_usd_per_contract":debit,
                    "position_cost_usd":round(qty*debit,2),
                    "unit_pnl_usd":unit_pnl,"pnl_usd":account_pnl,
                    "exit_at":flat.isoformat(),
                    "exit_reason":outcome["exit_reason"],
                    "reserved_profit_usd":round(decision.reserve,2),
                    "kelly_raw_fraction":round(decision.kelly_raw,4),
                })
            state["daily"].append({"day":str(day),
                "equity_usd":round(state["settled"]+state["pending"],4)})
            # Only the previous completed day's closed trades are available to
            # the next day's Kelly posterior; never today or future sessions.
            state["realized_return_history"].extend(todays_returns)
    report={
        "title":"V4 frozen conditional hold, dynamic sizing only",
        "status":"HISTORICAL_PROXY_PREVIOUSLY_INSPECTED_NOT_FORWARD_VALIDATED",
        "sessions":len(dates),
        "first_day":min(dates) if dates else None,
        "last_day":max(dates) if dates else None,
        "initial_cash_usd":START_CASH,
        "data":"prior inspected minute CBBO buckets/close-only SPY, not event NBBO",
        "entry_and_exit":"unchanged V4 frozen signal, IV-spread gate and conditional extension",
        "settlement":"T+1; sales not recycled intraday, no borrowing or negative cash",
        "contract_premium_at_risk":"100% per long contract can be lost; 35% modeled stop is not guaranteed",
        "sizing_arms":{
            "v4_fixed_one_contract":"V4 original frozen research reference; 1 contract if affordable",
            "v4_dynamic_compound":"25% equity budget multiplied by causal volume/liquidity quality and drawdown throttle; max 5 contracts",
            "v4_dynamic_profit_vault":"compound plus 50% equity gains reserved after at least 10% profit",
            "v4_dynamic_fractional_kelly":"previous-session trailing 50 realized option net returns, require 25 known trades, 2% total-loss stress, empirical Kelly x25%, ES99/cash/premium bounds; 0 contracts if no measured positive edge",
            "v4_dynamic_compound_micro":"compound with an explicit one-contract learning-floor allowed only when contract debit<=35% equity and drawdown multiplier>=60%",
            "v4_dynamic_vault_micro":"vault with the same one-contract micro floor after preserving reserved gains",
            "v4_dynamic_kelly_micro":"1-contract research learning phase until 25 prior realized trades; thereafter empirical fractional Kelly+ES99, with one-contract floor only if risk/edge rails passed",
            "v4_dynamic_streak_compound":"1 contract baseline, upsize from earlier session winning streak of at least 2, RVOL>=1.2, spread<=2.5%, prior-day cash drawdown<=4%, equity/1000 tier and 35% discretionary equity cap; after losses revert to 1 contract",
            "v4_dynamic_streak_vault":"streak compound with 50% of positive profits reserved after 10% account growth",
        },
        "risk_rails":{"max_contracts":MAX_CONTRACTS,"capital_budget_fraction":CAPITAL_FRACTION,
                      "drawdown_soft_fraction":DRAWDOWN_SOFT,
                      "drawdown_halt_fraction":DRAWDOWN_HALT,
                      "scenario_stress_full_premium_loss_probability":TAIL_PREMIUM_LOSS_PROB,
                      "minimum_kelly_previous_trades":MIN_KELLY_PREVIOUS_TRADES},
        "max_drawdown_is_end_of_day_only":True,
        "previously_inspected_data":True,
        "real_orders":0,"paid_data_purchases":0,
        "results":{arm:summary(state) for arm,state in states.items()},
        "caveats":[
            "Strategy and dynamic arm selection are being compared on already inspected data; NOT out-of-sample proof.",
            "Minute-bucket option quotes cannot establish executable stops or intraminute tail losses.",
            "Return outcomes for same-day trades are only added after day close; no lookahead Kelly.",
            "Integer contracts can exceed a desired fractional risk; no minimum-size override for Kelly.",
            "Intraday path/margin/exercise risk not measured; EOD drawdown can understate losses.",
        ],
    }
    output_dir.mkdir(parents=True,exist_ok=True)
    (output_dir/"report.json").write_text(json.dumps(report,indent=2,sort_keys=True)+"\n")
    with (output_dir/"trades.csv").open("w",newline="") as f:
        rows=[x for arm in ARMS for x in states[arm]["trades"]]
        keys=sorted({"day","arm","entry_at","symbol","contracts","pnl_usd","exit_reason"}|
                    {k for row in rows for k in row})
        w=csv.DictWriter(f,fieldnames=keys);w.writeheader();w.writerows(rows)
    with (output_dir/"daily_equity.csv").open("w",newline="") as f:
        writer=csv.DictWriter(f,fieldnames=["arm","day","equity_usd"])
        writer.writeheader()
        for arm in ARMS:
            for row in states[arm]["daily"]:
                writer.writerow({"arm":arm,**row})
    print("V4_DYNAMIC_SIZING_RESULT "+json.dumps(report,sort_keys=True),flush=True)
    return report


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--data-dir",type=Path,default=Path("/data/research"))
    p.add_argument("--output-dir",type=Path,default=Path("/data/research/v4_dynamic_size"))
    args=p.parse_args()
    run(args.data_dir,args.output_dir)


if __name__=="__main__":
    main()
