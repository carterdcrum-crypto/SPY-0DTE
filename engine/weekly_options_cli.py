"""Reproducible chronological SPY 0DTE experiment CLI; never routes orders live.

Run with: python -m engine.weekly_options_cli --mode audit --data-dir /data
Development freezes exactly nine predeclared configurations BEFORE any holdout
file is read. One-time holdout report refuses reruns or changed strategy code.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import statistics
from dataclasses import asdict, replace
from datetime import date, timedelta
from pathlib import Path
from typing import Sequence

from .session_calendar import session_close
from .weekly_options_data import audit, available_sessions, load_session, SessionData, NY
from .weekly_options_experiment import (
    ExperimentConfig, BacktestReport, run_simulation,
)

CANDIDATES = tuple(
    ExperimentConfig(strategy=strategy,risk_fraction=fraction)
    for strategy in ("orb_simple","orb_filtered","vwap_reclaim")
    for fraction in (.005,.01,.02)
)
STRESS = ("base","two_ticks","double_fees","entry_delay_62s")
RESEARCH_VERSION = "weekly-options-2026-10-08-v1"


def code_hash() -> str:
    root = Path(__file__).resolve().parent
    h=hashlib.sha256()
    for name in ("weekly_options_data.py","weekly_options_experiment.py","weekly_options_cli.py"):
        h.update((root/name).read_bytes())
    return h.hexdigest()


def full_session_calendar(days:Sequence[date]) -> None:
    if not days or len(set(days))!=len(days):
        raise ValueError("empty or duplicate session list")
    included=set(days)
    current=days[0]
    while current <= days[-1]:
        if session_close(current) is not None and current not in included:
            raise ValueError(f"MISSING_SESSION {current}: missing real input files; cannot skip non-trading or losing days")
        current+=timedelta(days=1)


def _volume_seed(sessions:Sequence[SessionData]) -> dict[int,list[int]]:
    out:dict[int,list[int]]={}
    for s in sessions[-5:]:
        for bar in s.bars:
            now=bar.start.astimezone(NY)
            key=now.hour*60+now.minute
            out.setdefault(key,[]).append(bar.volume)
    return out


def _load(root:Path,days:Sequence[date])->list[SessionData]:
    return [load_session(root,d) for d in days]


def weekly_table(result:BacktestReport) -> list[dict]:
    """ISO-week account returns; includes every no-trade eligible session."""
    groups:dict[tuple[int,int],list]= {}
    for day in result.daily_equity:
        d=date.fromisoformat(day.day)
        cal=d.isocalendar()
        groups.setdefault((cal.year,cal.week),[]).append(day)
    previous=result.starting_cash
    out=[]
    for key in sorted(groups):
        rows=groups[key]
        close=rows[-1].equity
        value=close/previous-1 if previous>0 else -1.0
        out.append({"year":key[0],"iso_week":key[1],"week_start":rows[0].day,
                    "week_end":rows[-1].day,"sessions":len(rows),
                    "account_start":previous,"account_end":close,
                    "account_return":value,
                    "closed_trades_in_week":rows[-1].completed_trades-
                        (result.daily_equity[result.daily_equity.index(rows[0])-1].completed_trades
                         if result.daily_equity.index(rows[0])>0 else 0)})
        previous=close
    return out


def block_bootstrap(weekly:Sequence[float],*,paths:int=500,block_size:int=2,seed:int=7421)->dict:
    """Moving block bootstrap of full weekly account returns, with replacement.

    Preserves up to `block_size` adjacent return dependencies, but assumes
    historical blocks are representative and cannot model unprecedented tails.
    """
    if not weekly:
        return {"available":False,"reason":"no full weekly sample"}
    rng=random.Random(seed)
    n=len(weekly)
    geometric=[]
    drawdowns=[]
    for _ in range(paths):
        sequence=[]
        while len(sequence)<n:
            start=rng.randrange(n)
            sequence.extend(weekly[(start+j)%n] for j in range(min(block_size,n)))
        sequence=sequence[:n]
        equity=peak=1.0
        dd=0.0
        for ret in sequence:
            equity*=max(0.0,1+ret)
            peak=max(peak,equity)
            dd=max(dd,1-equity/peak)
        geometric.append(equity**(1/n)-1 if equity>0 else -1)
        drawdowns.append(dd)
    def pct(data,q):
        data=sorted(data)
        return data[round((len(data)-1)*q)]
    return {"available":True,"seed":seed,"replicates":paths,
            "block_weeks":min(block_size,n),"n_weeks":n,
            "geometric_weekly_95pct_interval":[pct(geometric,.025),pct(geometric,.975)],
            "max_weekly_path_drawdown_95pct_interval":[pct(drawdowns,.025),pct(drawdowns,.975)],
            "limitations":"Empirical two-week moving circular blocks assume repeatable historical regimes; sparse weeks, tail jumps, execution errors and serial dependence beyond block length invalidate predictive interpretation."}


def _drawdown_strict(result:BacktestReport)->float:
    return result.intraday_max_drawdown


def summary(result:BacktestReport)->dict:
    weeks=weekly_table(result)
    rets=[w["account_return"] for w in weeks]
    geo=math.prod(max(0.0,1+r) for r in rets)**(1/len(rets))-1 if rets else 0.0
    streak=losses=0
    for t in result.trade_ledger:
        streak=streak+1 if t.realized_pnl<0 else 0
        losses=max(losses,streak)
    return {
        "strategy":result.configuration.strategy,
        "risk_fraction":result.configuration.risk_fraction,
        "start_equity":result.starting_cash,
        "ending_equity":result.ending_equity,
        "net_account_return":result.total_return,
        "median_weekly_return":statistics.median(rets) if rets else 0.0,
        "geometric_weekly_return":geo,
        "profitable_week_fraction":sum(r>0 for r in rets)/len(rets) if rets else 0.0,
        "weeks_doubled_fraction":sum(r>=1 for r in rets)/len(rets) if rets else 0.0,
        "worst_week":min(rets) if rets else 0.0,
        "weeks":len(rets),"max_daily_close_drawdown":result.max_drawdown,
        "max_mark_to_market_drawdown":_drawdown_strict(result),
        "max_marked_drawdown_can_be_artificial":result.stale_quote_marks>0,
        "longest_losing_trade_streak":losses,
        "exposure_fraction":result.exposure_fraction,
        "trade_count":result.completed_trades,
        "order_attempts":result.orders_attempted,
        "order_fills":result.order_fills,
        "canceled_or_unfilled_orders":result.canceled_orders,
        "option_quote_marks_stale":result.stale_quote_marks,
        "missing_executable_close_quotes":result.missing_quote_marks,
        "conservative_zero_recovery_exits":result.forced_worthless_exits,
        "fees_paid":result.fees_paid,
        "profit_factor_dollar_net":result.profit_factor,
        "median_option_premium_return":statistics.median(t.option_premium_return for t in result.trade_ledger)
            if result.trade_ledger else 0.0,
    }


def _write_csv(p:Path,rows:Sequence[dict])->None:
    if not rows:
        p.write_text("",encoding="utf-8")
        return
    with p.open("w",newline="",encoding="utf-8") as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _write_run(output:Path,name:str,result:BacktestReport)->None:
    output.mkdir(parents=True,exist_ok=True)
    _write_csv(output/(name+"-weekly.csv"),weekly_table(result))
    _write_csv(output/(name+"-trades.csv"),[asdict(t) for t in result.trade_ledger])
    _write_csv(output/(name+"-equity.csv"),[asdict(t) for t in result.daily_equity])
    _write_csv(output/(name+"-intraday-marks.csv"),[asdict(t) for t in result.intraday_marks])


def _score(s:dict)->float:
    # Predeclared simple risk-adjusted validation ranking; +100% week is
    # *not* an objective or a parameter-tuning criterion.
    if s["trade_count"]<3:
        return -1e8
    return s["geometric_weekly_return"]-2*s["max_mark_to_market_drawdown"]


def _candidate_key(cfg:ExperimentConfig)->str:
    return f"{cfg.strategy}__premium_{cfg.risk_fraction*100:.1f}pct"


def _split(days:Sequence[date],*,train:int,val:int,test:int,holdout:int,purge:int):
    if min(train,val,test,holdout)<1 or purge<1:
        raise ValueError("positive train/validation/test/holdout and >=1 purge session required")
    if len(days)<train+val+test+holdout+purge*3:
        raise ValueError("insufficient complete sessions for chronological splits")
    boundary=len(days)-holdout
    develop=days[:boundary-purge]
    hold=days[boundary:]
    folds=[]
    # Expanding training; nonoverlapping OOS test blocks. The train window
    # grows while the embargo retains an untouched day between folds.
    start=train+purge+val+purge
    while start+test<=len(develop):
        v_end=start-purge
        v_start=v_end-val
        tr_end=v_start-purge
        if tr_end<train:
            break
        folds.append((develop[:tr_end], develop[v_start:v_end], develop[start:start+test]))
        start+=test
    if not folds:
        raise ValueError("no non-overlapping walk-forward folds")
    return folds,hold


def _regime(s:SessionData)->str:
    first=s.bars[0].open
    final=s.bars[-1].close
    change=final/first-1
    total=(max(b.high for b in s.bars)-min(b.low for b in s.bars))/first
    if total>=.012:
        return "high_intraday_range"
    if change>=.004:
        return "bullish"
    if change<=-.004:
        return "bearish"
    return "sideways"


def run_development(root:Path,output:Path,days:Sequence[date],args)->dict:
    folds,holdout=_split(days,train=args.train_sessions,val=args.validation_sessions,
                         test=args.test_sessions,holdout=args.holdout_sessions,
                         purge=args.purge_sessions)
    candidates={_candidate_key(c):c for c in CANDIDATES}
    all_rows=[]
    validation_scores={name:[] for name in candidates}
    for fi,(train,val,test) in enumerate(folds):
        # Only load train/validation/test sessions. The holdout is not opened.
        prior=_load(root,train[-5:])
        val_sessions=_load(root,val)
        test_sessions=_load(root,test)
        for name,cfg in candidates.items():
            result=run_simulation(val_sessions,starting_cash=args.cash,cfg=cfg,
                                  prior_volume=_volume_seed(prior))
            s=summary(result)
            validation_scores[name].append(_score(s))
            all_rows.append({"fold":fi,"split":"validation","range_start":str(val[0]),
                             "range_end":str(val[-1]),**s})
            result=run_simulation(test_sessions,starting_cash=args.cash,cfg=cfg,
                                  prior_volume=_volume_seed(val_sessions))
            s=summary(result)
            all_rows.append({"fold":fi,"split":"walkforward_test","range_start":str(test[0]),
                             "range_end":str(test[-1]),**s})
    all_ranked=sorted(validation_scores,key=lambda name:(
        -statistics.mean(validation_scores[name]),name
    ))
    winner=all_ranked[0] if validation_scores[all_ranked[0]] and
        statistics.mean(validation_scores[all_ranked[0]])>-1e7 else None
    seal={
        "experiment_version":RESEARCH_VERSION,"code_sha256":code_hash(),
        "configurations":[asdict(c) for c in CANDIDATES],
        "folds":[{"train_end":str(x[0][-1]),"validation_start":str(x[1][0]),
                  "validation_end":str(x[1][-1]),"test_start":str(x[2][0]),
                  "test_end":str(x[2][-1])} for x in folds],
        "unseen_holdout_first_date":str(holdout[0]),
        "unseen_holdout_last_date":str(holdout[-1]),
        "holdout_sessions":len(holdout),
        "starting_cash":args.cash,"winner_selected_only_from_validation":winner,
        "selection_criterion":"mean validation geometric weekly return minus 2x intraday max drawdown; minimum 3 closed trades; no selection on fold tests or holdout",
        "configuration_grid":"three named strategies x 0.5/1/2 pct, fixed parameters; no other parameters searched",
        "holdout_peeking_not_allowed":True,
        "scenarios_predeclared":list(STRESS),
        "data_origin":str(root),
    }
    output.mkdir(parents=True,exist_ok=True)
    _write_csv(output/"development_all_candidates.csv",all_rows)
    # Exclusive create; cannot silently overwrite a prior seal.
    with (output/"sealed_design.json").open("x",encoding="utf-8") as f:
        json.dump(seal,f,indent=2,sort_keys=True)
    return {"status":"DEVELOPMENT_FROZEN","seal":str(output/"sealed_design.json"),
            "folds":len(folds),"candidate_configurations":len(candidates),
            "holdout_examined":False,"selected_by_validation":winner,
            "walkforward_results":str(output/"development_all_candidates.csv")}


def _stress(cfg:ExperimentConfig,name:str)->ExperimentConfig:
    if name=="base":
        return cfg
    if name=="two_ticks":
        return replace(cfg,adverse_option_ticks=2)
    if name=="double_fees":
        return replace(cfg,fee_per_contract_side=cfg.fee_per_contract_side*2)
    if name=="entry_delay_62s":
        return replace(cfg,latency_seconds=62,max_fill_wait_seconds=130)
    raise ValueError(name)


def run_holdout(root:Path,output:Path,days:Sequence[date],args)->dict:
    path=output/"sealed_design.json"
    if not path.is_file():
        raise FileNotFoundError("Run --mode development first to freeze the exact parameters")
    seal=json.loads(path.read_text())
    if seal["code_sha256"]!=code_hash() or seal["experiment_version"]!=RESEARCH_VERSION:
        raise ValueError("code changed after freeze; holdout cannot be accessed")
    if seal["starting_cash"]!=args.cash or str(root)!=seal["data_origin"]:
        raise ValueError("account or data root altered after freeze")
    last=[d for d in days if seal["unseen_holdout_first_date"]<=str(d)<=seal["unseen_holdout_last_date"]]
    if len(last)!=seal["holdout_sessions"] or str(last[0])!=seal["unseen_holdout_first_date"] or str(last[-1])!=seal["unseen_holdout_last_date"]:
        raise ValueError("holdout calendar changed after freeze")
    # The marker is created BEFORE reading any holdout file. A second call
    # aborts and does not allow repeated holdout-driven code tuning.
    with (output/"holdout_accessed.once").open("x") as f:
        f.write("Holdout access consumed by experimental protocol.\n")
    datasets=_load(root,last)
    # OOS results for ALL nine PREDECLARED strategies, including failures.
    result_rows=[]
    bootstrap={}
    for original in CANDIDATES:
        name=_candidate_key(original)
        scenarios=STRESS if name==seal["winner_selected_only_from_validation"] else ("base",)
        for scenario in scenarios:
            cfg=_stress(original,scenario)
            run=run_simulation(datasets,starting_cash=args.cash,cfg=cfg,
                               prior_volume=_volume_seed([]))
            key=name+"__"+scenario
            _write_run(output,key,run)
            s=summary(run)
            result_rows.append({"configuration":name,"scenario":scenario,**s})
            bootstrap[key]=block_bootstrap([r["account_return"] for r in weekly_table(run)])
    _write_csv(output/"holdout_all_candidates_and_stress.csv",result_rows)
    (output/"holdout_bootstrap.json").write_text(json.dumps(bootstrap,indent=2))
    # Pre-register bar ranges as descriptive regime labels, never as
    # post-hoc strategy selection features.
    regime_counts={}
    for item in datasets:
        regime=_regime(item)
        regime_counts[regime]=regime_counts.get(regime,0)+1
    winner=seal["winner_selected_only_from_validation"]
    selected=next((x for x in result_rows if x["configuration"]==winner and x["scenario"]=="base"),None)
    verified=bool(selected and selected["weeks"]>=3 and
                  selected["weeks_doubled_fraction"]>=.5 and
                  selected["geometric_weekly_return"]>=1 and
                  selected["max_mark_to_market_drawdown"]<=.15 and
                  not selected["max_marked_drawdown_can_be_artificial"])
    report={
        "status":"HOLDOUT_EVALUATED_ONCE","candidate_count":len(CANDIDATES),
        "holdout_start":str(last[0]),"holdout_end":str(last[-1]),
        "regimes":regime_counts,"selected_config":winner,
        "plus_100pct_weekly_low_drawdown_hypothesis_supported":verified,
        "decision":"NOT_SUPPORTED" if not verified else "PRELIMINARY_EVIDENCE_ONLY_NOT_PREDICTIVE",
        "scenarios":list(STRESS),"summary_csv":str(output/"holdout_all_candidates_and_stress.csv"),
        "bootstrap_file":str(output/"holdout_bootstrap.json"),
        "limits":"Historical bars give a volume-weighted typical-price VWAP proxy, not true trade VWAP. No holdout reuse allowed. Equity options are cash-account T+1, full-spread fills with 1-2 ticks adverse. No fills inferred from SPY candle returns.",
    }
    (output/"holdout_report.json").write_text(json.dumps(report,indent=2,sort_keys=True))
    return report


def main(argv:Sequence[str]|None=None)->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode",choices=("audit","development","holdout"),required=True)
    parser.add_argument("--data-dir",type=Path,required=True)
    parser.add_argument("--output-dir",type=Path,default=Path("research_results/weekly_options"))
    parser.add_argument("--cash",type=float,default=10000.0)
    parser.add_argument("--train-sessions",type=int,default=40)
    parser.add_argument("--validation-sessions",type=int,default=10)
    parser.add_argument("--test-sessions",type=int,default=10)
    parser.add_argument("--holdout-sessions",type=int,default=20)
    parser.add_argument("--purge-sessions",type=int,default=1)
    args=parser.parse_args(argv)
    info=audit(args.data_dir)
    if args.mode=="audit":
        print(json.dumps(info,indent=2))
        return 0
    if not info["usable_for_this_experiment"]:
        print(json.dumps({"status":"DATA_BLOCKED",**info},indent=2))
        return 2
    days=available_sessions(args.data_dir)
    full_session_calendar(days)
    result=(run_development(args.data_dir,args.output_dir,days,args)
            if args.mode=="development" else
            run_holdout(args.data_dir,args.output_dir,days,args))
    print(json.dumps(result,indent=2))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
