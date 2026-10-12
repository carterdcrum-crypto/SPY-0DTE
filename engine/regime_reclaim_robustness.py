"""Falsification tests of predeclared positive SPY 0DTE candidate.

Treat the entire archive as INSPECTED; day-block bootstrap is descriptive,
NOT unseen holdout, calibrated prediction, or true NBBO option execution.
No strategy tuning, broker orders, or market data purchases.
"""
from __future__ import annotations
import argparse,csv,json,math,random,statistics
from collections import defaultdict,Counter
from datetime import date,datetime
from .free_signal_screen import ET
from pathlib import Path
from .burst_research import _research_files

CANDIDATE="blend_trend_or_stretch_cost_aware"
STRESS_LEVELS=(0,2,5,10)
BOOT_REPS=4000
SEED=20261012

def wilson(k:int,n:int,z:float=1.96):
    if n==0:return None
    p=k/n
    d=1+z*z/n
    mid=(p+z*z/(2*n))/d
    half=(z/d)*math.sqrt(p*(1-p)/n+z*z/(4*n*n))
    return [round(100*(mid-half),2),round(100*(mid+half),2)]

def run(ledger_path:Path,data_dir:Path,out:Path)->dict:
    with ledger_path.open(newline="") as f:
        rows=list(csv.DictReader(f))
    market_dates=sorted({
        file.name[9:19] for file in _research_files(data_dir)})
    # filenames expected: spy_0dte_YYYY-MM-DD.csv.gz
    records=[x for x in rows if x["arm"]==CANDIDATE]
    days=defaultdict(list)
    for x in records:days[x["day"]].append(x)
    if not market_dates or not records:
        raise ValueError("missing real archived session or chosen arm ledger")
    empirical=[sum(float(x["pnl_usd"]) for x in days[d]) for d in market_dates]
    frequency=[len(days[d]) for d in market_dates]
    biggest=sorted(((empirical[i],market_dates[i]) for i in range(len(empirical))),reverse=True)
    highest_gain=biggest[0]
    baseline=sum(empirical)
    rng=random.Random(SEED)
    intervals={}
    for extra in STRESS_LEVELS:
        r=[p-extra*n for p,n in zip(empirical,frequency)]
        samples=[]
        for _ in range(BOOT_REPS):
            picked=[]
            while len(picked)<len(r):
                first=rng.randrange(len(r))
                picked.extend(r[(first+j)%len(r)] for j in range(5))
            samples.append(statistics.fmean(picked[:len(r)]))
        samples.sort()
        intervals[f"extra_{extra}_per_trade_usd"]={
            "aggregate_cost_stressed_historical_usd":round(sum(r),2),
            "five_session_block_95pct_mean_pnl_usd_per_market_session":[
                round(samples[int(BOOT_REPS*.025)],3),
                round(samples[int(BOOT_REPS*.975)-1],3)],
            "positive_bootstrap_fraction_pct":round(
                100*sum(v>0 for v in samples)/BOOT_REPS,2)
        }
    weeks=defaultdict(float)
    for i,d in enumerate(market_dates):
        wk=date.fromisoformat(d).isocalendar()[:2]
        weeks[str(wk)]+=empirical[i]
    winners=[x for x in records if float(x["pnl_usd"])>0]
    side=defaultdict(list)
    setup=defaultdict(list)
    et=defaultdict(list)
    for x in records:
        pnl=float(x["pnl_usd"])
        side[x["side"]].append(pnl)
        setup[x["setup"]].append(pnl)
        at=datetime.fromisoformat(x["entry_at"]).astimezone(ET).time()
        bucket=("before_1100" if at < datetime.strptime("11:00","%H:%M").time() else
                ("1100_to_1300" if at<datetime.strptime("13:00","%H:%M").time() else "1300_plus"))
        et[bucket].append(pnl)
    def group(g):
        return {k:{"trades":len(vals),"win_pct":round(
            100*sum(v>0 for v in vals)/len(vals),2),
            "sum_pnl_usd":round(sum(vals),2)}
            for k,vals in sorted(g.items())}
    res={
        "candidate":CANDIDATE,
        "purpose":"DESCRIPTIVE_FALSIFICATION_NOT_NEW_HIGH_WINRATE_CLAIM",
        "archive_sessions":len(market_dates),
        "trades":len(records),
        "win_rate_pct":round(100*len(winners)/len(records),2),
        "unadjusted_wilson_95pct_winrate_CI_pct":wilson(len(winners),len(records)),
        "daily_block_bootstrap":intervals,
        "best_single_day":{"day":highest_gain[1],"net_pnl_usd":round(highest_gain[0],2)},
        "sum_without_best_day_usd":round(baseline-highest_gain[0],2),
        "sum_without_best_three_days_usd":round(
            baseline-sum(v for v,_ in biggest[:3]),2),
        "five_largest_days":biggest[:5],
        "positive_weeks":sum(v>0 for v in weeks.values()),
        "negative_weeks":sum(v<0 for v in weeks.values()),
        "no_trade_or_flat_weeks":sum(v==0 for v in weeks.values()),
        "total_weeks":len(weeks),
        "weekly_one_contract_additive_pnl_usd":weeks,
        "by_option_side":group(side),
        "by_setup":group(setup),
        "by_time_bucket_diagnostic_only":group(et),
        "warnings":[
            "Eight candidate strategies evaluated; the apparent winner may be selection bias.",
            "No untouched historical out-of-sample, real orders, option tick/event-NBBO, or new market data.",
            "Historical trades are output of a one-position-per-arm $1000 T+1 cash simulation; additive PnL without extra stress being rerouted through affordability.",
            "Option quotes are sampled CBBO minute buckets and do not capture true intraminute stop fills.",
            "Wilson interval assumes trade independence (not realistic); block bootstrap handles some calendar clustering but does not correct selection bias.",
            "Positive historical cumulative results do not validate repeated +100% weekly account returns.",
        ]
    }
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(res,sort_keys=True,indent=2,default=dict)+"\n")
    print("REGIME_RECLAIM_ROBUSTNESS "+json.dumps(res,sort_keys=True,default=dict),flush=True)
    return res


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--ledger",type=Path,default=Path("/data/research/regime_reclaim/trades.csv"))
    p.add_argument("--data-dir",type=Path,default=Path("/data/research"))
    p.add_argument("--output",type=Path,default=Path("/data/research/regime_reclaim/robustness.json"))
    a=p.parse_args();run(a.ledger,a.data_dir,a.output)

if __name__=="__main__":
    main()
