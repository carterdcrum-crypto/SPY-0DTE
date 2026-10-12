"""Independent SPY 0DTE research: Voss-INSPIRED public price-structure principles.

Attributed publicly verifiable ideas: map historical support/resistance;
read context first; WAIT for price-level confirmation; define invalidation;
review outcomes. This is a NEW QUANTITATIVE INTERPRETATION, *not* an
officially reproduced or endorsed @officialvosstrading private strategy.
At time of research, public YouTube video transcripts / exact trading
formulas were not retrievable; see docs/voss_public_research.md.

This experiment is STRICTLY SEPARATE from the frozen Regime Reclaim
candidate. Uses ONLY completed close + volume-ratio SPY minute data, so
levels are close-range PROXIES, not actual OHLC extremes, VWAP, orderflow.

Historical option CBBO minute buckets are NOT event-time executable NBBO.
Identical 30% whole-position trailing exit, independent -35% loss stop,
10-min max hold, no profit targets. $1,000 cash / T+1 settlement.
Research only. No broker actions. Already inspected 128-session archive.
"""
from __future__ import annotations
import argparse,csv,json,math,statistics
from collections import Counter,defaultdict
from datetime import datetime,timedelta,time
from pathlib import Path

from .burst_research import _research_files
from .data import load_canonical_path
from .exit_policy_comparison import _select_entry,_sampled_path
from .free_signal_screen import ET,_bps
from .trailing_only_sweep import simulate,period
from .regime_reclaim_winrate import cash_summary,summarize as baseline_summary

START_CASH=1000.0
TRAIL=30
HARD_STOP=35
MAX_HOLD=10
START_SIGNAL=time(9,52)
END_SIGNAL=time(15,25)
LEVEL_BACK=20
RETEST_LOOKAHEAD_COMPLETED_BARS=8
LEVEL_ZONE_BPS=3.5
BREAK_CLEAR_BPS=2.0
VOL_MIN=1.0
ARMS=(
    "voss_level_break_retest",
    "voss_support_resistance_rejection",
    "voss_confirmed_trend_pullback",
    "voss_structure_combo",
    "voss_structure_combo_option_cost_gate",
)
SETUPS=("level_break_retest","level_rejection","confirmed_trend_pullback")


def near(level:float,spot:float,width_bp:float=LEVEL_ZONE_BPS)->bool:
    return abs(_bps(spot,level))<=width_bp


def strict_completed(frames,idx:int,bars:int=24)->bool:
    return idx>=bars and all(
        (frames[k].timestamp-frames[k-1].timestamp).total_seconds()==60
        for k in range(idx-bars+1,idx+1)
    )


def valid_spots(prices)->bool:
    return all(math.isfinite(x) and x>0 for x in prices)


def asof_structure_signals(frames)->list[dict]:
    """Close-only approximate support/resistance, no future bar references.

    Repeated support/resistance contacts need at least two prior-close
    touches of a near fixed 20-close level, separated by 3 bars.
    Breakout/retest is a 3-STAGE finite-state pattern, with the level
    frozen at breakout. A distinct later confirmation closes away.
    """
    prices=[f.market.spot for f in frames]
    events=[]
    # pending side and frozen reference level.
    pending=None
    for i in range(LEVEL_BACK+4,len(frames)):
        signal_at=frames[i].timestamp+timedelta(minutes=1)
        clock=signal_at.astimezone(ET).time()
        if not START_SIGNAL<=clock<END_SIGNAL:continue
        if not strict_completed(frames,i,LEVEL_BACK+4):
            pending=None
            continue
        last=prices[i];prev=prices[i-1]
        prior=prices[i-LEVEL_BACK-1:i-1]
        if not valid_spots([last,prev]+prior):continue
        rvol=frames[i].market.volume_ratio
        if not math.isfinite(rvol) or rvol<VOL_MIN:continue
        resistance=max(prior)
        support=min(prior)
        # Support/resistance is a ZONE; we do not claim candle high/lows.
        resistance_touches=[j for j,p in enumerate(prior)
                            if near(resistance,p,2.5)]
        support_touches=[j for j,p in enumerate(prior)
                         if near(support,p,2.5)]
        has_resistance=(len(resistance_touches)>=2 and
            resistance_touches[-1]-resistance_touches[0]>=3)
        has_support=(len(support_touches)>=2 and
            support_touches[-1]-support_touches[0]>=3)
        momentum=_bps(last,prices[i-5])
        context=_bps(last,prices[i-15])
        # Process existing frozen breakout state BEFORE detecting new one.
        if pending:
            side=pending["side"]
            level=pending["level"]
            age=i-pending["break_i"]
            if age>RETEST_LOOKAHEAD_COMPLETED_BARS:
                pending=None
            elif pending["retest_i"] is None:
                if age>=1 and near(level,last):
                    pending["retest_i"]=i
                elif (side=="call" and _bps(last,level)<-LEVEL_ZONE_BPS*2
                      or side=="put" and _bps(last,level)>LEVEL_ZONE_BPS*2):
                    pending=None
            elif i>pending["retest_i"]:
                good=(last>prev and _bps(last,level)>=BREAK_CLEAR_BPS
                      if side=="call" else
                      last<prev and _bps(last,level)<=-BREAK_CLEAR_BPS)
                if good:
                    events.append({
                        "at":signal_at,"side":side,
                        "setup":"level_break_retest",
                        "level":round(level,4),"context_bp":round(context,3),
                        "volume_ratio":rvol,
                        "reference_at":pending["break_at"].isoformat(),
                    })
                    pending=None
                elif not near(level,last,LEVEL_ZONE_BPS*2):
                    pending=None
        # Independent support/resistance level rejection / price-action
        # confirmation: previous completed close touched level, current
        # completed close rejects *away* from level by >=2bps.
        if has_support and near(support,prev) and _bps(last,support)>=BREAK_CLEAR_BPS and last>prev:
            events.append({"at":signal_at,"side":"call",
                "setup":"level_rejection","level":round(support,4),
                "volume_ratio":rvol,"context_bp":round(context,3),
                "reference_at":signal_at.isoformat()})
        if has_resistance and near(resistance,prev) and _bps(last,resistance)<=-BREAK_CLEAR_BPS and last<prev:
            events.append({"at":signal_at,"side":"put",
                "setup":"level_rejection","level":round(resistance,4),
                "volume_ratio":rvol,"context_bp":round(context,3),
                "reference_at":signal_at.isoformat()})
        # Trend-context pullback confirmation around a past 20-bar
        # price mean. Does NOT claim Voss's personal setup or EMA rules.
        mean20=statistics.fmean(prior)
        mean5=statistics.fmean(prices[i-6:i-1])
        # directional 15-minute bias plus completed previous close on
        # other side of MA; latest return through 5-close prior mean.
        if context>=8 and prev<=mean5 and last>mean5 and last>mean20 and momentum>=2.:
            events.append({"at":signal_at,"side":"call",
                "setup":"confirmed_trend_pullback","level":round(mean5,4),
                "volume_ratio":rvol,"context_bp":round(context,3),
                "reference_at":signal_at.isoformat()})
        if context<=-8 and prev>=mean5 and last<mean5 and last<mean20 and momentum<=-2.:
            events.append({"at":signal_at,"side":"put",
                "setup":"confirmed_trend_pullback","level":round(mean5,4),
                "volume_ratio":rvol,"context_bp":round(context,3),
                "reference_at":signal_at.isoformat()})
        # Breakout state: require a previously mapped multi-touch level,
        # first valid completed close outside it, with confirming volume.
        if pending is None and rvol>=1.25:
            if has_resistance and _bps(last,resistance)>=BREAK_CLEAR_BPS and prev<=resistance:
                pending={"side":"call","level":resistance,
                         "break_i":i,"retest_i":None,"break_at":signal_at}
            elif has_support and _bps(last,support)<=-BREAK_CLEAR_BPS and prev>=support:
                pending={"side":"put","level":support,
                         "break_i":i,"retest_i":None,"break_at":signal_at}
    return events


def quote_cost_good(option,market)->bool:
    iv_ratio=(option.implied_volatility/market.realized_volatility
              if market.realized_volatility>0 else math.inf)
    return option.spread_fraction<=.035 and iv_ratio<=1.5


def match(arm,kind,good):
    if arm not in ARMS:raise ValueError("invalid Voss-inspired arm")
    return {
        "voss_level_break_retest":kind=="level_break_retest",
        "voss_support_resistance_rejection":kind=="level_rejection",
        "voss_confirmed_trend_pullback":kind=="confirmed_trend_pullback",
        "voss_structure_combo":True,
        "voss_structure_combo_option_cost_gate":good,
    }[arm]


def backtest(source:Path,out:Path)->dict:
    states={arm:{"settled":START_CASH,"pending":0.,"daily":[]} for arm in ARMS}
    ledger=[]
    diag=Counter()
    market_days=[]
    for path in _research_files(source):
        frames=list(load_canonical_path(path))
        if not frames:continue
        day=frames[0].timestamp.astimezone(ET).date().isoformat()
        market_days.append(day)
        events=asof_structure_signals(frames)
        diag["sessions"]+=1
        diag["signal_candidates"]+=len(events)
        diag["minute_frames"]+=len(frames)
        cases=[]
        for signal in events:
            choice=_select_entry(frames,signal["at"],signal["side"])
            if choice is None:
                diag["unquoted_signals"]+=1
                continue
            at_i,opt=choice
            entry=frames[at_i].timestamp+timedelta(minutes=1)
            debit=round((opt.ask+.01)*100+.65,2)
            cases.append({
                "signal":signal,"index":at_i,
                "entry_at":entry,"quote":opt,"debit":debit,
                "quality":quote_cost_good(opt,frames[at_i].market),
            })
        cases.sort(key=lambda c:(c["entry_at"],
                   SETUPS.index(c["signal"]["setup"]),c["quote"].symbol))
        for arm in ARMS:
            state=states[arm]
            state["settled"]+=state["pending"] # T+1, no same-day sales recycling
            state["pending"]=0.
            settled=state["settled"]
            pending=0.
            flat=None
            count=0
            for case in cases:
                e=case["signal"];opt=case["quote"];debit=case["debit"]
                if not match(arm,e["setup"],case["quality"]):continue
                if flat is not None and case["entry_at"]<=flat:continue
                if debit>settled+1e-8:
                    diag[arm+"_insufficient_settled_cash"]+=1
                    continue
                settled-=debit
                trace=_sampled_path(frames,case["index"],opt.symbol)
                if trace is None:
                    # gap: reserve time slot, reverse unfilled hypothetical
                    settled+=debit
                    flat=case["entry_at"]+timedelta(minutes=MAX_HOLD)
                    diag[arm+"_no_future_bid_path"]+=1
                    continue
                simulated=simulate(opt.ask,trace,TRAIL)
                pnl=float(simulated["pnl_usd"])
                pending+=max(0.,debit+pnl)
                flat=datetime.fromisoformat(simulated["exit_time"])
                count+=1
                ledger.append({
                    "day":day,"arm":arm,"setup":e["setup"],
                    "right":e["side"],"signal_at":e["at"].isoformat(),
                    "entry_at":case["entry_at"].isoformat(),
                    "exit_at":simulated["exit_time"],
                    "contract":opt.symbol,"level_close_proxy":e["level"],
                    "reference_at":e["reference_at"],
                    "relative_volume":e["volume_ratio"],
                    "debit_usd":debit,"pnl_usd":pnl,
                    "exit_reason":simulated["exit_reason"],
                    "option_premium_return_pct":simulated["return_on_premium_pct"],
                    "cost_gate":case["quality"],
                })
            state["settled"]=settled
            state["pending"]=pending
            state["daily"].append({"day":day,"equity_usd":round(settled+pending,4),
                                   "trades":count})
    days=sorted(set(market_days))
    results={}
    for arm in ARMS:
        rows=[r for r in ledger if r["arm"]==arm]
        positive=[r["pnl_usd"] for r in rows if r["pnl_usd"]>0]
        negative=[r["pnl_usd"] for r in rows if r["pnl_usd"]<=0]
        counts=Counter(r["day"] for r in rows)
        pnl=sum(r["pnl_usd"] for r in rows)
        results[arm]={
            "trades":len(rows),"calendar_days":len(days),
            "active_days":sum(counts[d]>0 for d in days),
            "trades_per_market_day":round(len(rows)/len(days),3) if days else None,
            "days_2_or_more_trades":sum(counts[d]>=2 for d in days),
            "wins":len(positive),
            "win_pct":round(100*len(positive)/len(rows),2) if rows else None,
            "mean_winner_usd":round(statistics.fmean(positive),2) if positive else None,
            "mean_loser_usd":round(statistics.fmean(negative),2) if negative else None,
            "modeled_pnl_usd":round(pnl,2),
            "added_2usd_each_extra_execution_stress_pnl_usd":
                round(pnl-2*len(rows),2),
            "modeled_1000_cash_account":cash_summary(states[arm]["daily"],START_CASH),
            "periods":{p:{
                "trades":len(subset:=[r for r in rows if period(r["day"])==p]),
                "modeled_pnl_usd":round(sum(r["pnl_usd"] for r in subset),2),
            } for p in ("development","validation","previously_studied_diagnostic")},
        }
    report={
        "research_name":"Voss public price-structure inspired SPY 0DTE exploratory research",
        "channel":"https://www.youtube.com/@officialvosstrading",
        "source_for_general_principles":"https://vosstradingvip.com/",
        "source_limit":"Public site supports context, support/resistance, price confirmation, defined invalidation; YouTube video transcripts/specific formula NOT verified; ALL quantitative rules here are newly invented testable hypotheses, NOT attributable as Voss's exact signals.",
        "research_only":True,"independent_from_regime_reclaim":True,
        "archived_sessions_previously_studied":True,
        "no_untouched_holdout":True,"option_quote_source":"legacy_CBBO_1m_NOT_event_executable_NBBO",
        "actual_OHLCV_support_resistance":False,
        "cash_start_usd":START_CASH,"cash_settlement":"T+1",
        "exit":{"trail_pct":TRAIL,"hard_stop_initial_pct":HARD_STOP,
                "max_hold_minutes":MAX_HOLD,
                "fixed_take_profit":False,"partial_sales":False},
        "predeclared_setups":SETUPS,"predeclared_research_arms":ARMS,
        "coverage":dict(diag),"results":results,"orders":0,"data_purchased":0,
    }
    out.mkdir(parents=True,exist_ok=True)
    (out/"report.json").write_text(json.dumps(report,indent=2,sort_keys=True)+"\n")
    with (out/"trades.csv").open("w",newline="") as f:
        columns=["day","arm","setup","right","signal_at","entry_at","exit_at",
                 "contract","level_close_proxy","reference_at","relative_volume",
                 "debit_usd","pnl_usd","exit_reason",
                 "option_premium_return_pct","cost_gate"]
        w=csv.DictWriter(f,fieldnames=columns);w.writeheader();w.writerows(ledger)
    print("VOSS_STRUCTURE_REPORT "+json.dumps(report,sort_keys=True),flush=True)
    return report


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--data-dir",type=Path,default=Path("/data/research"))
    p.add_argument("--output-dir",type=Path,
                   default=Path("/data/research/voss_public_structure"))
    x=p.parse_args()
    backtest(x.data_dir,x.output_dir)


if __name__=="__main__":
    main()
