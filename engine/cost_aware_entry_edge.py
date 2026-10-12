"""Causal entry gate under frozen 30% full-position trailing exits.

Research-only, using already licensed sampled 1m CBBO. No broker, downloads,
real NBBO, full-chain PCR, or production-policy writes. Crucially, no claimed
edge until a predeclared gate clears a prior-only statistical hurdle.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
from collections import defaultdict
from datetime import date, datetime, time
from pathlib import Path

from .burst_research import _research_files
from .data import load_canonical_path
from .exit_policy_comparison import _select_entry, _sampled_path
from .free_signal_screen import ET, _bps
from .spy_pcr_momentum_reversal import session_classifications, select
from .trailing_only_sweep import simulate, period

TRAIL_PERCENT = 30
WINDOW = 30  # strictly completed PRIOR sessions
MIN_PRIOR_TRADES = 30
MIN_ACTIVE_DAYS = 8
# One-sided 95% family-wise approximate correction for 9 tested candidate rules.
# A Gaussian heuristic, not proof of statistical edge or a valid multiple-test p.
CONSERVATIVE_Z = 2.58
EXTRA_SLIPPAGE_USD_ROUNDTRIP = 2.00  # +1 option tick EACH side
VARIANTS = (
    "baseline",
    "volume_150",
    "volume_200",
    "tight_spread_080",
    "volume_150_spread_080",
    "volume_150_macd_price",
    "pullback_only",
    "pullback_volume_150",
    "volume_150_early",
)
PERIOD_NAMES = ("development","validation","previously_studied_diagnostic","all_inspected")


def signal_eligible(row, variant: str, *, five_minute_trend_bps: float | None = None) -> bool:
    """Pre-entry factors only; future target and return fields are never read."""
    if variant not in VARIANTS:
        raise ValueError("unexpected entry hypothesis")
    if "base_four_factors_PASS" not in row.reasons:
        return False
    v=row.opportunity.volume_ratio_at_signal
    momentum=row.family_mode=="momentum"
    pullback=row.opportunity.setup=="trend_pullback_reclaim"
    macd_price=row.macd_histo_aligned_or_improving and row.price_action_confirmed
    # Shift the MORNING RESEARCH strategy from 10:00-12:00 to 09:30-11:00 ET.
    # The legacy four-factor scanner still needs 75 completed bars, so
    # this alone cannot issue signals during the first 75 market minutes.
    at=datetime.fromisoformat(row.opportunity.signal_at).astimezone(ET)
    return {
        "baseline": True,
        "volume_150": v>=1.50,
        "volume_200": v>=2.00,
        "tight_spread_080": True,  # spread tested with first known option quote
        "volume_150_spread_080": v>=1.50,
        "volume_150_macd_price": v>=1.50 and momentum and macd_price,
        "pullback_only": pullback,
        "pullback_volume_150": pullback and v>=1.50,
        "volume_150_early": v>=1.50 and time(9,30)<=at.time()<time(11,0),
    }[variant]


def quote_eligible(quote,variant:str)->bool:
    if variant not in VARIANTS:
        raise ValueError("unexpected entry hypothesis")
    return quote.spread_fraction <= .08 if "spread_080" in variant else True


def conservative_prior_gate(prior_days: list[dict], candidate: str) -> dict:
    """Lower bound of stress-adjusted daily profits from PRIOR sessions only.

    No-trade days are explicitly included. An empty/weak history abstains.
    An unusually favorable small subset must clear minimum trades and
    sessions as well as >0 LCB on STRESS-adjusted returns.
    """
    if candidate not in VARIANTS:
        raise ValueError("unknown variant")
    if len(prior_days) < WINDOW:
        return {"allowed":False,"reason":"insufficient_prior_sessions"}
    prior=prior_days[-WINDOW:]
    n_trades=sum(len(d["cases"][candidate]) for d in prior)
    n_active=sum(bool(d["cases"][candidate]) for d in prior)
    if n_trades < MIN_PRIOR_TRADES or n_active < MIN_ACTIVE_DAYS:
        return {"allowed":False,"reason":"insufficient_prior_trade_count",
                "prior_trades":n_trades,"prior_active_days":n_active}
    daily_stress=[sum(c["net_pnl_usd"]-EXTRA_SLIPPAGE_USD_ROUNDTRIP
                        for c in d["cases"][candidate]) for d in prior]
    mu=statistics.fmean(daily_stress)
    sd=statistics.stdev(daily_stress)
    lower=mu-CONSERVATIVE_Z*sd/math.sqrt(len(daily_stress))
    return {
        "allowed":lower>0,
        "reason":"prior_positive_stressed_lower_bound" if lower>0 else "no_proven_positive_edge",
        "prior_trades":n_trades,"prior_active_days":n_active,
        "prior_stressed_pnl_per_day_usd":round(mu,4),
        "prior_stressed_lower_bound_usd_per_day":round(lower,4),
    }


def report_group(daily:list[dict],variant:str,group:str)->dict:
    scope=[d for d in daily if group=="all_inspected" or d["period"]==group]
    pnl=[x["net_pnl_usd"] for d in scope for x in d["cases"][variant]]
    entry=[x["premium_return_pct"] for d in scope for x in d["cases"][variant]]
    stressed=[v-EXTRA_SLIPPAGE_USD_ROUNDTRIP for v in pnl]
    realized=peak=dd=0.
    for x in (x["net_pnl_usd"] for d in scope for x in d["cases"][variant]):
        realized+=x
        peak=max(peak,realized)
        dd=max(dd,peak-realized)
    return {"sessions":len(scope),"trades":len(pnl),
            "gross_stressed_pnl_usd_NOT_ACCOUNT":round(sum(stressed),2),
            "net_hypothetical_pnl_usd_NOT_ACCOUNT":round(sum(pnl),2),
            "mean_premium_return_pct":round(statistics.fmean(entry),3) if entry else None,
            "win_rate_pct":round(100*sum(x>0 for x in pnl)/len(pnl),2) if pnl else None,
            "positive_days":sum(sum(c["net_pnl_usd"] for c in d["cases"][variant])>0 for d in scope),
            "negative_days":sum(sum(c["net_pnl_usd"] for c in d["cases"][variant])<0 for d in scope),
            "closed_trade_additive_drawdown_NOT_ACCOUNT_USD":round(dd,2)}



def robustness(daily:list[dict],candidate:str,*,window_length:int=5,reps:int=2000)->dict:
    """Descriptive serial-dependence and single-lucky-day sensitivity.

    Already-inspected days and nine screened hypotheses make these intervals
    optimistic for the final selected winner. They are NOT new OOS evidence.
    """
    if candidate not in VARIANTS:
        raise ValueError("unknown candidate")
    per_day=[
        {"day":d["day"],
         "trades":len(d["cases"][candidate]),
         "net":sum(c["net_pnl_usd"] for c in d["cases"][candidate])}
        for d in daily
    ]
    if not per_day:
        return {"status":"missing_days"}
    gross=[x["net"] for x in per_day]
    stressed=[x["net"]-x["trades"]*EXTRA_SLIPPAGE_USD_ROUNDTRIP for x in per_day]
    ordered=sorted(per_day,key=lambda x:x["net"],reverse=True)
    rand=random.Random(20261011)
    def interval(seq):
        trials=[]
        for _ in range(reps):
            sample=[]
            while len(sample)<len(seq):
                i=rand.randrange(len(seq))
                sample.extend(seq[(i+j)%len(seq)]
                              for j in range(window_length))
            trials.append(sum(sample[:len(seq)])/len(seq))
        trials.sort()
        return [round(trials[int(.025*reps)],4),
                round(trials[int(.975*reps)-1],4)]
    profits=[c["net_pnl_usd"] for d in daily for c in d["cases"][candidate]]
    wins=[p for p in profits if p>0]
    losses=[p for p in profits if p<0]
    avg_win=statistics.fmean(wins) if wins else None
    avg_loss=statistics.fmean(losses) if losses else None
    breakeven=(100*abs(avg_loss)/(avg_win+abs(avg_loss))
               if avg_win is not None and avg_loss is not None
               else None)
    return {
        "candidate":candidate,"days":len(per_day),"trades":len(profits),
        "raw_pnl_total_usd":round(sum(gross),2),
        "stress_pnl_total_usd":round(sum(stressed),2),
        "days_positive":sum(x>0 for x in gross),
        "days_negative":sum(x<0 for x in gross),
        "biggest_winning_day_usd":round(ordered[0]["net"],2),
        "biggest_winning_day":ordered[0]["day"],
        "minus_best_1_day_sum_usd":round(sum(d["net"] for d in ordered[1:]),2),
        "minus_best_3_days_sum_usd":round(sum(d["net"] for d in ordered[3:]),2),
        "minus_best_5_days_sum_usd":round(sum(d["net"] for d in ordered[5:]),2),
        "wins":len(wins),"losses":len(losses),
        "mean_winning_trade_usd":round(avg_win,3) if avg_win is not None else None,
        "mean_losing_trade_usd":round(avg_loss,3) if avg_loss is not None else None,
        "breakeven_win_rate_at_observed_payoff_ratio_pct":
            round(breakeven,3) if breakeven is not None else None,
        "bootstrap_5session_day_blocks_95pct_usd_per_calendar_day":
            interval(gross),
        "stressed_bootstrap_5session_95pct_usd_per_calendar_day":
            interval(stressed),
        "uncertainty_note":
            "Descriptive only; parameters were compared on previously inspected data. "
            "No multiplicity correction or untouched holdout. Percentile block bootstrap "
            "is not a strategy-discovery adjusted p-value.",
    }


def run(root:Path,out:Path)->dict:
    days=[]
    diagnostic=defaultdict(int)
    for path in _research_files(root):
        frames=list(load_canonical_path(path))
        if not frames:continue
        day=frames[0].timestamp.astimezone(ET).date().isoformat()
        classifications=session_classifications(frames)
        cases={v:[] for v in VARIANTS}
        diagnostic["candidate_frames"]+=len(frames)
        diagnostic["classified_opportunities"]+=len(classifications)
        for variant in VARIANTS:
            accepted=select([r for r in classifications if signal_eligible(r,variant)],
                            "momentum_reversal_four_factors")
            diagnostic[variant+"_signals"]+=len(accepted)
            for selected in accepted:
                op=selected.opportunity
                q=_select_entry(frames,datetime.fromisoformat(op.signal_at),op.original_direction)
                if q is None:
                    diagnostic[variant+"_missing_entry"]+=1
                    continue
                idx,quote=q
                # Quote constraint is checked after seeing the next minute's
                # quote, never on future prices; lock remains conservative.
                if not quote_eligible(quote,variant):
                    diagnostic[variant+"_rejected_spread"]+=1
                    continue
                trace=_sampled_path(frames,idx,quote.symbol)
                if trace is None:
                    diagnostic[variant+"_missing_exit"]+=1
                    continue
                calc=simulate(quote.ask,trace,TRAIL_PERCENT)
                cases[variant].append({
                    "signal_at":op.signal_at,"setup":op.setup,
                    "symbol":quote.symbol,"direction":op.original_direction,
                    "volume_ratio":round(op.volume_ratio_at_signal,4),
                    "spread_fraction":round(quote.spread_fraction,4),
                    "premium_return_pct":calc["return_on_premium_pct"],
                    "net_pnl_usd":calc["pnl_usd"],
                    "reason":calc["exit_reason"]})
        days.append({"day":day,"period":period(day),"cases":cases})
    days.sort(key=lambda d:d["day"])
    table={v:{p:report_group(days,v,p) for p in PERIOD_NAMES} for v in VARIANTS}
    walkforward=[]
    for i,d in enumerate(days):
        scores={v:conservative_prior_gate(days[max(0,i-WINDOW):i],v) for v in VARIANTS}
        feasible=[v for v in VARIANTS if scores[v]["allowed"]]
        choice=max(feasible,key=lambda v:(scores[v]["prior_stressed_lower_bound_usd_per_day"],
                                           -VARIANTS.index(v))) if feasible else None
        pnl=sum(x["net_pnl_usd"] for x in d["cases"][choice]) if choice else 0.0
        walkforward.append({
            "day":d["day"],"period":d["period"],
            "rule":choice or "NO_TRADE",
            "trades":len(d["cases"][choice]) if choice else 0,
            "net_pnl_usd_NOT_ACCOUNT":round(pnl,4),
            "selected_gate":scores[choice] if choice else None,
            "rules_with_positive_prior_stressed_lower_bound":feasible,
        })
    wf={}
    for p in PERIOD_NAMES:
        subset=[x for x in walkforward if p=="all_inspected" or x["period"]==p]
        pnls=[x["net_pnl_usd_NOT_ACCOUNT"] for x in subset]
        traded=[x for x in subset if x["trades"]>0]
        wf[p]={"sessions":len(subset),"traded_sessions":len(traded),
               "trade_count":sum(x["trades"] for x in subset),
               "sum_net_hypothetical_pnl_usd":round(sum(pnls),2),
               "positive_sessions":sum(x>0 for x in pnls),
               "negative_sessions":sum(x<0 for x in pnls),
               "no_trade_or_zero_pnl_sessions":sum(x==0 for x in pnls)}
    result={
        "study":"SPY_ENTRY_EDGE_ABLATIONS_PLUS_ROLLING_PAST_ONLY_COST_AWARE_ABSTENTION",
        "exit_policy":"fixed_30pct_trailing_full_exit_no_target_no_runner",
        "independent_hard_loss_pct":35,"maximum_hold_minutes":10,
        "estimated_extra_execution_stress_usd_per_one_contract_roundtrip":EXTRA_SLIPPAGE_USD_ROUNDTRIP,
        "data_sessions":len(days),"rule_count":len(VARIANTS),
        "prior_day_window":WINDOW,"min_prior_matched_trades":MIN_PRIOR_TRADES,
        "prior_lcb_z_multiple_rule_heuristic":CONSERVATIVE_Z,
        "coverage":dict(diagnostic),"rule_metrics":table,"walkforward":wf,
        "robustness_morning_volume":robustness(days,"volume_150_early"),
        "robustness_baseline":robustness(days,"baseline"),
        "walkforward_decisions":walkforward,
        "zero_new_market_data_spend":True,"not_true_event_NBBO":True,
        "complete_pcr_missing":True,"account_return_not_tested":True,
        "archive_previously_inspected_no_untouched_holdout":True,
        "conclusion_rule":"If no strategy has strictly positive PRIOR stressed confidence lower-bound then NO_TRADE.",
    }
    out.mkdir(parents=True,exist_ok=True)
    (out/"report.json").write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
    with (out/"daily_walkforward_decisions.csv").open("w",newline="") as f:
        writer=csv.DictWriter(f,fieldnames=["day","period","rule","trades",
                   "net_pnl_usd_NOT_ACCOUNT"])
        writer.writeheader()
        writer.writerows({k:x[k] for k in writer.fieldnames} for x in walkforward)
    summarized={k:v for k,v in result.items() if k!="walkforward_decisions"}
    print("ENTRY_EDGE_STUDY_RESULT "+json.dumps(summarized,sort_keys=True),flush=True)
    return result


def main()->None:
    parser=argparse.ArgumentParser()
    parser.add_argument("--data-dir",type=Path,default=Path("/data/research"))
    parser.add_argument("--output-dir",type=Path,default=Path("/data/research/cost_aware_entry_edge"))
    args=parser.parse_args()
    run(args.data_dir,args.output_dir)


if __name__=="__main__":
    main()
