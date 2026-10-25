"""SPY 0DTE V5 ex-ante position-sizing backtest: liquidity/volatility/quality,
historical signal reliability and prior EOD drawdown.

RESEARCH-ONLY. Historical archive is missing real SPY OHLCV, event-time
executable NBBO and displayed option depth. 'Predictive' means causal
pre-entry features, NOT a trained/validated probability forecast. No order
submission or changes to current production/paper/live execution.

Signal reliability uses only PRIOR DAY virtual outcomes for up to 30 earlier
days, counting the earliest observable proxy opportunity per setup/day.
Simulated exit outcomes from the current day are incorporated after its close.
All risk policies retain identical existing three V5 signals, proxy selection,
35% hard stop, 25% premium trail, and 18-minute maximum hold.
"""
from __future__ import annotations

import json
import math
import os
from collections import Counter, defaultdict, deque
from datetime import timedelta
from pathlib import Path

from .burst_research import _research_files
from .data import load_canonical_path
from .regime_hold_sensitivity import START_CASH, MAX_FIXED_MIN, liquidation_cutoff
from .v4_dynamic_size_backtest import summary
from .v5_archive_proxy import ARMS, proxy_candidates, proxy_exit
from .v5_conditional_capital_study import (
    market_evidence, dd_multiplier, choose_qty, peak_to_trough_eod, half_metrics,
)
from .weekly_options_data import NY

BASELINE_ARM=ARMS[-1]
POLICIES={
    "current_full_cash_eod_throttle": ("baseline",),
    "original_v5_adaptive": ("adaptive",),
    "predictive_combined": ("combined",1.00),
    "predictive_balanced": ("combined",.82),
    "predictive_defensive": ("combined",.62),
    "predictive_opportunistic": ("combined",1.25),
    "predictive_market_no_reliability": ("combined_no_rel",1.00),
    "predictive_liquidity_only": ("liq",),
    "predictive_volatility_only": ("vol",),
    "predictive_quality_regime_only": ("quality",),
    "predictive_prior_winrate_only": ("rel",),
    "predictive_combined_skip_wide": ("strict",1.00),
}
LABELS=("momentum_ignition_CLOSE_PROXY",
        "liquidity_reversal_CLOSE_PROXY",
        "volatility_expansion_CLOSE_PROXY")


def reliability_multiplier(known):
    """Bayesian shrinkage to 50%; cold start has no benefit or penalty.

    known contains *prior-day* independent shadow results ONLY, so no same-
    session future outcome or optimized full-archive signal weights.
    """
    n=len(known)
    if n < 8:
        return 1.0
    posterior=(sum(1 for win in known if win)+6.0)/(n+12.0)
    return max(.68,min(1.18,posterior/.55))


def ex_ante_factors(item, evidence, reliability):
    """Only pre-entry fields: RVOL, trend/eff, quality, IV/RV, spread."""
    q=item["quote"]
    spread=float(q.spread_fraction)
    ratio=float(q.implied_volatility)/float(item["realized_vol_at_entry"])
    score=int(item["score"])
    rvol=float(item["signal"]["volume_ratio"])
    quality=(1.10 if score>=5 else .98 if score>=4 else
             .83 if score>=3 else .67)
    regime=1.08 if evidence["aligned"] else .82
    if evidence["confluence"]:
        regime *= 1.04
    liquidity=(1.06 if spread<=.01 else
               1.0 if spread<=.015 else
               .82 if spread<=.02 else .63)
    volatility=(1.03 if ratio<=1.10 else
                .86 if ratio<=1.25 else
                .70 if ratio<=1.40 else .50)
    if rvol>3.0:
        volatility *= .82
    if rvol<1.5:
        quality *= .9
    return {"quality_regime":quality*regime,
            "liquidity":liquidity,
            "volatility":volatility,
            "reliability":reliability,
            "iv_rv":round(ratio,3),
            "spread":round(spread,4),
            "rvol":round(rvol,2),
            "score":score}


def budget_fraction(policy, opening, peak, factors):
    kind=policy[0]
    base=dd_multiplier(opening,peak)
    if kind=="baseline":
        return base
    if kind=="liq":
        adjust=factors["liquidity"]
    elif kind=="vol":
        adjust=factors["volatility"]
    elif kind=="quality":
        adjust=factors["quality_regime"]
    elif kind=="rel":
        adjust=factors["reliability"]
    else:
        adjust=(factors["liquidity"]*factors["volatility"]*
                factors["quality_regime"])
        if kind!="combined_no_rel":
            adjust *= factors["reliability"]
        adjust *= policy[1]
        if kind=="strict" and factors["spread"] > .02:
            return 0.0
    # Do not artificially increase the baseline full-cash risk fraction
    # during drawdown, whatever extra factors may suggest.
    return min(base,max(0.,min(1.,base*adjust)))


def risk_size(policy, *, item, settled, opening, peak, factors):
    debit=float(item["debit"])
    if min(debit,settled,opening,peak) <= 0:
        return 0,0.
    if policy[0]=="adaptive":
        return choose_qty(("adaptive",),item=item,opening=opening,
                          settled=settled,peak=peak,
                          evidence={"tier":0})
    fraction=budget_fraction(policy,opening,peak,factors)
    return (max(0,min(int((settled+1e-8)//debit),
                      int((opening*fraction+1e-8)//debit))),fraction)


def replay(paths, *, starting_cash=START_CASH):
    if not paths:
        raise ValueError("Actual archived Railway sessions required")
    states={name:{
        "settled":starting_cash,"pending":0.,
        "daily":[],"trades":[],"skips":Counter(),"exits":Counter(),
        "budget_fractions":[],"score_tiers":Counter(),
    } for name in POLICIES}
    # One separate, position-independent, *shadow only* setup history.
    # Each setup has at most one independent virtual label per session to
    # mitigate same-day cluster-weighting and avoid future-day leakage.
    prior={setup:deque(maxlen=30) for setup in LABELS}
    sessions=[]
    reliability_daily=[]
    for path in paths:
        frames=list(load_canonical_path(path))
        if not frames:
            continue
        day=frames[0].timestamp.astimezone(NY).date()
        day_text=day.isoformat()
        sessions.append(day_text)
        cutoff=liquidation_cutoff(day)
        entries=proxy_candidates(frames,challenger=True)
        observed=defaultdict(set)
        for item in entries:
            observed[(item["entry_at"],item["signal"]["side"])].add(
                item["signal"]["setup"])
            item["realized_vol_at_entry"]=frames[item["entry_idx"]].market.realized_volatility
        # Snapshot of PRIOR sessions. Histories are updated only when the
        # entire day's simulations are finished.
        reliability={setup:reliability_multiplier(prior[setup])
                     for setup in LABELS}
        if len(sessions)%20==1:
            reliability_daily.append({"day":day_text,
                "past_observations":{s:len(prior[s]) for s in LABELS},
                "multipliers":reliability})
        features=[]
        for item in entries:
            evidence=market_evidence(
                item,observed[(item["entry_at"],item["signal"]["side"])])
            features.append(ex_ante_factors(
                item,evidence,reliability[item["signal"]["setup"]]))
        # Cache identical entry outcomes across policies, including virtual
        # prior-session reliability, without reaching ahead for sizing.
        exit_cache={}
        def outcome(i,item):
            if i not in exit_cache:
                exit_cache[i]=proxy_exit(BASELINE_ARM,item,frames,cutoff)
            return exit_cache[i]
        for name,policy in POLICIES.items():
            s=states[name]
            s["settled"]=round(s["settled"]+s["pending"],8)
            s["pending"]=0.
            opening=s["settled"]
            peak=max([starting_cash]+[d["equity_usd"] for d in s["daily"]])
            busy_until=None
            for i,item in enumerate(entries):
                at=item["entry_at"]
                if at>=cutoff-timedelta(minutes=MAX_FIXED_MIN+1):
                    s["skips"]["LATE_ENTRY"]+=1
                    continue
                if busy_until is not None and at<=busy_until:
                    s["skips"]["BUSY"]+=1
                    continue
                qty,fraction=risk_size(
                    policy,item=item,settled=s["settled"],opening=opening,
                    peak=peak,factors=features[i])
                if qty<1:
                    s["skips"]["NO_PREMIUM_BUDGET"]+=1
                    continue
                debit=item["debit"]
                if qty*debit>s["settled"]+1e-8:
                    raise AssertionError("Cash account T+1 debit violation")
                s["budget_fractions"].append(round(fraction,4))
                s["score_tiers"][str(market_evidence(
                    item,observed[(item["entry_at"],item["signal"]["side"])])["tier"])]+=1
                result=outcome(i,item)
                s["settled"]=round(s["settled"]-qty*debit,8)
                trade={"day":day_text,"setup":item["signal"]["setup"],
                       "contracts":qty,"entry_at":at.isoformat(),
                       "entry_debit_usd":debit,
                       "risk_fraction":round(fraction,4),
                       "pnl_usd":None if result is None else
                                round(qty*result["pnl_usd"],6)}
                if result is None:
                    busy_until=cutoff
                    trade["exit_reason"]="NO_VERIFIED_TERMINAL_BID_PREMIUM_RESERVED"
                else:
                    s["pending"]+=max(0.,qty*debit+trade["pnl_usd"])
                    busy_until=result["exit_at"]
                    trade["exit_reason"]=result["exit_reason"]
                    s["exits"][result["exit_reason"]]+=1
                s["trades"].append(trade)
            s["daily"].append({"day":day_text,
                "equity_usd":round(s["settled"]+s["pending"],4)})
        # Shadow history updates ONLY AFTER today's trade decisions.
        first_setup={}
        for i,item in enumerate(entries):
            setup=item["signal"]["setup"]
            if (setup in first_setup or
                item["entry_at"]>=cutoff-timedelta(minutes=MAX_FIXED_MIN+1)):
                continue
            r=outcome(i,item)
            if r is not None:
                first_setup[setup]=r["pnl_usd"]>0
        for setup,win in first_setup.items():
            prior[setup].append(win)
        if len(sessions)%20==0:
            print("V5_PREDICTIVE_PROGRESS "+json.dumps({
                "sessions":len(sessions),"day":day_text}),flush=True)
    if not sessions:
        raise ValueError("No usable archived sessions")
    results={}
    for name,s in states.items():
        scored=[x for x in s["trades"] if x["pnl_usd"] is not None]
        mid=len(s["daily"])//2
        results[name]={
            **summary(s,starting_cash=starting_cash),
            **peak_to_trough_eod(s["daily"],starting_cash),
            "first_half":half_metrics(s["daily"],0,mid,starting_cash),
            "second_half":half_metrics(s["daily"],mid,len(s["daily"]),
                                    s["daily"][mid-1]["equity_usd"]),
            "tier_counts":dict(s["score_tiers"]),
            "average_risk_fraction_of_opening_equity_pct":round(
                100*sum(s["budget_fractions"])/len(s["budget_fractions"]),2)
                if s["budget_fractions"] else None,
            "max_single_contracts":max([0]+[t["contracts"] for t in scored]),
            "by_setup":{setup:{
                "trades":sum(x["setup"]==setup for x in scored),
                "net_pnl_usd":round(sum(x["pnl_usd"] for x in scored
                                   if x["setup"]==setup),2),
            } for setup in LABELS}
        }
    base=results["current_full_cash_eod_throttle"]
    for v in results.values():
        v["delta_ending_cash_vs_current_usd"]=round(
            v["ending_equity_usd"]-base["ending_equity_usd"],2)
        v["delta_true_eod_dd_vs_current_pp"]=round(
            v["true_max_eod_drawdown_pct"]-base["true_max_eod_drawdown_pct"],2)
    return {
        "status":"IN_SAMPLE_REUSED_CLOSE_ONLY_PROXY_NOT_EXECUTABLE_NOT_TRUE_V5",
        "start_cash_usd":starting_cash,"sessions":len(sessions),
        "first_day":min(sessions),"last_day":max(sessions),
        "baseline":"current_full_cash_eod_throttle",
        "policies":POLICIES,
        "features":"Entry as-of realized vol, IV/RV, SPY close-only signal score/regime/RVOL, sampled option spread, prior-session virtual setup win rate",
        "lookahead_prevention":"Reliability histories updated AFTER the full day's decisions; per setup earliest scored virtual outcome per session, trailing max 30",
        "sizing_budget":"Prior session EOD equity drawdown x prior settled starting cash; never same-day recycled option premium",
        "historical_quote_depth_available":False,
        "historical_executable_nbbo_available":False,
        "independent_holdout_available":False,
        "no_broker_orders":True,
        "production_configuration_modified":False,
        "no_market_data_purchases":True,
        "real_market_impact_and_intraday_drawdown_unverified":True,
        "assumed_costs":"Entry ask+0.01/share+$0.65; exit sampled bid-0.01/share-$0.65; additional $2/contract stress in reported summary",
        "model_caveat":"Not predictive ML or proven forecasting; heuristic causal multipliers. Archived repeated data, high risk overfitting.",
        "reliability_audit":reliability_daily,
        "results":results
    }


def main():
    root=Path(os.getenv("RESEARCH_DATA_DIR","/data/research"))
    report=replay(_research_files(root))
    outfile=root/"v5_predictive_sizing_2026_10_25"/"report.json"
    outfile.parent.mkdir(parents=True,exist_ok=True)
    outfile.write_text(json.dumps(report,indent=2,sort_keys=True)+"\n")
    print("V5_PREDICTIVE_RESULT "+json.dumps(report,sort_keys=True),flush=True)
    os.execvp("python",["python","-m","engine.forward_watch"])


if __name__=="__main__":
    main()
