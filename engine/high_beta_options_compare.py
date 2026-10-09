"""Option-quote execution research: SPY 0DTE vs high-beta screened stocks.

Strictly REAL event-observed option NBBO (buy ASK, sell BID), contract
multiplier/fees/extra execution ticks, cash-account affordability and
closed-trade drawdown. No option return from SPY-price movement, no
fabricated universe, no purchased data, NO BROKER ORDER CAPABILITY.

The prior repo's 128 SPY-only 1m snapshots do not supply a whole market
stock universe with lagged ATR/beta/RVOL, nor independently timestamped
cross-symbol options quote book. CLI therefore FAILS CLOSED when inputs
are absent; it never pretends to have discovered winning stock options.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
import re
import statistics
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date,datetime,timedelta,timezone
from pathlib import Path
from typing import Sequence

from .free_signal_screen import ET
from .high_beta_stock_screen import (
    StockMinute, StockSignal, read_stock_minutes, screening_audit,
    signals_by_symbol_day,
)
from .weighted_daily_coverage import POLICIES, decision, FAMILIES

OCC=re.compile(r"^([A-Z]{1,6})(\d{6})([CP])(\d{8})$")
VALID_EVENT_SOURCE=frozenset({"nbbo-event","cmbp-1","tcbbo"})
INITIAL_CASH_USD=10000.0  # scenario / NOT user's actual account
CONTRACT_EXPOSURE_LIMIT_FRACTION=.05
FEE_PER_CONTRACT_PER_SIDE_USD=.68  # illustrative; broker fee may differ
EXTRA_OPTION_SLIPPAGE_PER_SHARE=.01
MAX_ENTRY_WAIT_SECONDS=90
MAX_EXIT_WAIT_SECONDS=90
OBSERVATION_LATENCY_SECONDS=2
MAX_QUOTE_SPREAD_MID_FRACTION=.25
MAX_STRIKE_DISTANCE_SPOT_FRACTION=.05
MAX_STOCK_DTE_DAYS=7
HOLD_MINUTES=10


@dataclass(frozen=True)
class NBBO:
    observed_at:datetime  # actual observation/availability, NOT interval start
    underlying:str
    option_symbol:str
    expiration:date
    right:str
    strike:float
    bid:float
    ask:float
    bid_size:int
    ask_size:int
    source:str

    def __post_init__(self):
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("quote must have timezone and observation time")
        if self.source not in VALID_EVENT_SOURCE:
            raise ValueError("minute bucket close, unverified provider, and synthetic quotes are forbidden")
        compact=self.option_symbol.replace(" ","")
        m=OCC.fullmatch(compact)
        if not m or m.group(1)!=self.underlying:
            raise ValueError("real OCC underlying/contract symbol required")
        expiry=datetime.strptime(m.group(2),"%y%m%d").date()
        right="call" if m.group(3)=="C" else "put"
        strike=int(m.group(4))/1000.
        if expiry!=self.expiration or right!=self.right or abs(strike-self.strike)>1.e-7:
            raise ValueError("OCC expiry/right/strike mismatch")
        if self.observed_at.astimezone(ET).date()>self.expiration:
            raise ValueError("expired contract event")
        if any(not math.isfinite(x) for x in (self.strike,self.bid,self.ask)):
            raise ValueError("invalid nonfinite NBBO")
        if self.ask<=0 or self.bid<0 or self.ask<self.bid or self.bid_size<0 or self.ask_size<0:
            raise ValueError("invalid NBBO quote/size")

    @property
    def spread_fraction(self)->float:
        return (self.ask-self.bid)/max((self.ask+self.bid)/2.,1.e-9)


@dataclass(frozen=True)
class FilledTrade:
    mode:str
    strategy:str
    underlying:str
    contract:str
    right:str
    expiration:str
    dte_calendar_days:int
    setup:str
    indicator_score:int
    signal_at:str
    entry_observed_at:str
    exit_observed_at:str
    entry_debit_one_contract_usd:float
    exit_credit_one_contract_usd:float
    return_on_premium_cost_percent:float
    realized_pnl_usd:float
    cash_after_closure_usd:float


def _read_csv(file:Path):
    return gzip.open(file,"rt",encoding="utf-8",newline="") if file.suffix==".gz" else file.open("r",encoding="utf-8",newline="")


def read_event_options(files:Sequence[Path])->tuple[NBBO,...]:
    needed={"observed_at","underlying","option_symbol","expiration",
            "right","strike","bid","ask","bid_size","ask_size","source"}
    records=[]
    seen=set()
    for path in sorted(files):
        with _read_csv(path) as f:
            reader=csv.DictReader(f)
            if not reader.fieldnames or not needed.issubset(reader.fieldnames):
                raise ValueError("event-observed quote source missing schema fields")
            for x in reader:
                quote=NBBO(
                    observed_at=datetime.fromisoformat(x["observed_at"].replace("Z","+00:00")),
                    underlying=x["underlying"].strip(),
                    option_symbol=x["option_symbol"].strip(),
                    expiration=date.fromisoformat(x["expiration"]),
                    right=x["right"].strip().lower(),
                    strike=float(x["strike"]),bid=float(x["bid"]),ask=float(x["ask"]),
                    bid_size=int(x["bid_size"]),ask_size=int(x["ask_size"]),
                    source=x["source"].strip(),
                )
                key=(quote.underlying,quote.option_symbol,quote.observed_at)
                if key in seen:
                    raise ValueError("duplicate option NBBO observation")
                seen.add(key)
                records.append(quote)
    return tuple(sorted(records,key=lambda x:(x.observed_at,x.option_symbol)))


def select_entry_quote(
    events:Sequence[NBBO], signal:StockSignal, *, mode:str,
)->NBBO|None:
    """Entry quote happens AFTER signal + latency; no stale quote reuse.

    At each actual post-signal observed event, choose only currently
    offered side/strike/expiry. Time priority avoids peeking at a future
    quote to choose a retrospectively better strike.
    """
    if mode not in ("SPY_0DTE","HIGH_BETA_STOCK_OPTIONS"):
        raise ValueError("unknown comparison mode")
    at=datetime.fromisoformat(signal.opportunity.signal_at)
    begin=at+timedelta(seconds=OBSERVATION_LATENCY_SECONDS)
    deadline=begin+timedelta(seconds=MAX_ENTRY_WAIT_SECONDS)
    desired_right=signal.opportunity.original_direction
    day=at.astimezone(ET).date()
    available=[]
    for q in events:
        qtime=q.observed_at.astimezone(at.tzinfo)
        if not(begin<=qtime<=deadline):
            continue
        dte=(q.expiration-day).days
        if q.underlying!=signal.symbol or q.right!=desired_right or dte<0:
            continue
        if mode=="SPY_0DTE" and dte!=0:
            continue
        if mode=="HIGH_BETA_STOCK_OPTIONS" and dte>MAX_STOCK_DTE_DAYS:
            continue
        if q.bid_size<1 or q.ask_size<1 or q.bid<=0:
            continue
        if q.spread_fraction>MAX_QUOTE_SPREAD_MID_FRACTION:
            continue
        if abs(q.strike-signal.underlying_spot_at_signal)> (
            MAX_STRIKE_DISTANCE_SPOT_FRACTION*signal.underlying_spot_at_signal):
            continue
        available.append(q)
    if not available:
        return None
    return min(available,key=lambda q:(
        q.observed_at,
        (q.expiration-day).days,
        abs(q.strike-signal.underlying_spot_at_signal),
        q.option_symbol,
    ))


def select_exit_quote(events:Sequence[NBBO],entry:NBBO)->NBBO|None:
    target=entry.observed_at+timedelta(minutes=HOLD_MINUTES)
    deadline=target+timedelta(seconds=MAX_EXIT_WAIT_SECONDS)
    available=[
        q for q in events if q.underlying==entry.underlying
        and q.option_symbol==entry.option_symbol
        and target<=q.observed_at<=deadline
        and q.bid_size>=1 and q.bid>=0
    ]
    return min(available,key=lambda q:q.observed_at) if available else None


def _complete_sessions(records:Sequence[StockMinute],mode:str)->set[str]:
    symbols={"SPY"} if mode=="SPY_0DTE" else {
        x.symbol for x in records if x.symbol!="SPY"}
    return {x.timestamp.astimezone(ET).date().isoformat()
            for x in records if x.symbol in symbols}


def _source_signals(records:Sequence[StockMinute],mode:str)->tuple[StockSignal,...]:
    if mode=="SPY_0DTE":
        spy=[x for x in records if x.symbol=="SPY"]
        return signals_by_symbol_day(spy,apply_high_beta_screen=False)
    if mode=="HIGH_BETA_STOCK_OPTIONS":
        st=[x for x in records if x.symbol!="SPY"]
        return signals_by_symbol_day(st,apply_high_beta_screen=True)
    raise ValueError("unknown mode")


def simulate(
    *,
    mode:str,
    policy:str,
    raw_signals:Sequence[StockSignal],
    option_quotes:Sequence[NBBO],
    common_days:Sequence[str],
    starting_cash:float=INITIAL_CASH_USD,
)->dict:
    """One cash-limited long contract, no parallel positions, no leverage.

    Missing entry or exit quotes make performance ranking incomplete:
    missing exits are NOT falsely treated as profitable or omitted from
    a claim of complete backtest.
    """
    if starting_cash<=0:
        raise ValueError("starting cash must be positive")
    if policy not in POLICIES:
        raise ValueError("unsupported fixed strategy")
    days=set(common_days)
    candidates=[x for x in raw_signals
                if x.opportunity.day in days and decision(x.opportunity,policy)]
    candidates.sort(key=lambda x:(
        datetime.fromisoformat(x.opportunity.signal_at),
        -x.opportunity.weighted_total,x.symbol,
        FAMILIES.index(x.opportunity.setup),
    ))
    by_contract=defaultdict(list)
    by_underlying=defaultdict(list)
    for q in option_quotes:
        by_contract[q.option_symbol].append(q)
        by_underlying[q.underlying].append(q)
    cash=starting_cash
    blocked_until=None
    no_entry=no_exit=unaffordable=0
    records=[]
    unresolved=[]
    for signal in candidates:
        when=datetime.fromisoformat(signal.opportunity.signal_at)
        if blocked_until is not None and when<=blocked_until:
            continue
        option=select_entry_quote(by_underlying[signal.symbol],signal,mode=mode)
        if option is None:
            no_entry+=1
            continue
        debit=(option.ask+EXTRA_OPTION_SLIPPAGE_PER_SHARE)*100+FEE_PER_CONTRACT_PER_SIDE_USD
        if debit>cash or debit>starting_cash*CONTRACT_EXPOSURE_LIMIT_FRACTION:
            unaffordable+=1
            continue
        exitquote=select_exit_quote(by_contract[option.option_symbol],option)
        if exitquote is None:
            no_exit+=1
            unresolved.append({
                "signal_at":signal.opportunity.signal_at,"underlying":signal.symbol,
                "option_symbol":option.option_symbol,"entry_quote_at":option.observed_at.isoformat(),
                "reason":"REAL_OPTION_EXIT_NBBO_UNOBSERVED_NOT_A_WIN_OR_ZERO",
            })
            # An actual position was entered. Without an exit observation,
            # no subsequent trade may be placed in this replay; fail closed.
            blocked_until=datetime.max.replace(tzinfo=timezone.utc)
            break
        credit=max(0.,exitquote.bid-EXTRA_OPTION_SLIPPAGE_PER_SHARE)*100-FEE_PER_CONTRACT_PER_SIDE_USD
        pnl=credit-debit
        cash+=pnl
        blocked_until=exitquote.observed_at
        records.append(FilledTrade(
            mode=mode,strategy=policy,underlying=signal.symbol,
            contract=option.option_symbol,right=option.right,
            expiration=option.expiration.isoformat(),
            dte_calendar_days=(option.expiration-when.astimezone(ET).date()).days,
            setup=signal.opportunity.setup,indicator_score=signal.opportunity.weighted_total,
            signal_at=when.isoformat(),
            entry_observed_at=option.observed_at.isoformat(),
            exit_observed_at=exitquote.observed_at.isoformat(),
            entry_debit_one_contract_usd=round(debit,4),
            exit_credit_one_contract_usd=round(credit,4),
            return_on_premium_cost_percent=round(100*pnl/debit,4),
            realized_pnl_usd=round(pnl,4),
            cash_after_closure_usd=round(cash,4),
        ))
    net=sum(x.realized_pnl_usd for x in records)
    positives=sum(x.realized_pnl_usd>0 for x in records)
    negative=sum(x.realized_pnl_usd<0 for x in records)
    daily=defaultdict(float)
    for trade in records:
        day=datetime.fromisoformat(trade.exit_observed_at).astimezone(ET).date().isoformat()
        daily[day]+=trade.realized_pnl_usd
    for d in common_days:
        daily[d]+=0.
    high=starting_cash
    equity=starting_cash
    dd=0.
    for trade in records:
        equity+=trade.realized_pnl_usd
        high=max(high,equity)
        dd=max(dd,(high-equity)/high)
    gains=sum(max(0,x.realized_pnl_usd) for x in records)
    losses=-sum(min(0,x.realized_pnl_usd) for x in records)
    complete=(no_entry==0 and no_exit==0 and unaffordable==0)
    # It is legitimate that many signals lack affordable quotes, but NOT
    # legitimate to rank partly observed contract returns as representative.
    return {
        "mode":mode,"strategy":policy,"calendar_days_compared":len(common_days),
        "candidate_signals_AFTER_screen_before_execution":len(candidates),
        "observed_completed_option_roundtrips":len(records),
        "missing_entry_quote_signals":no_entry,
        "missing_exit_quote_signals":no_exit,
        "unaffordable_one_contract_signals":unaffordable,
        "quote_and_cash_coverage_complete":complete,
        "eligible_for_complete_head_to_head_ranking":complete and len(records)>=30,
        "net_realized_quote_assumption_pnl_usd":round(net,2),
        "net_return_on_INITIAL_SCENARIO_cash_percent":round(100*net/starting_cash,4),
        "trades_positive_pnl":positives,"trades_negative_pnl":negative,
        "win_fraction":positives/len(records) if records else None,
        "mean_premium_return_pct":(
            round(statistics.fmean(x.return_on_premium_cost_percent for x in records),4)
            if records else None),
        "profit_factor":round(gains/losses,4) if losses>0 else None,
        "days_profit_usd_positive":sum(daily[d]>0 for d in common_days),
        "days_profit_usd_negative":sum(daily[d]<0 for d in common_days),
        "days_profit_usd_zero":sum(daily[d]==0 for d in common_days),
        "worst_closed_trade_day_usd":round(min(daily.values()),2) if daily else None,
        "max_closed_trade_equity_drawdown_fraction_UNDERSTATES_INTRATRADE":round(dd,6),
        "stock_options_0DTE_count":sum(x.dte_calendar_days==0 for x in records),
        "stock_options_1_to_7_calendar_DTE_count":sum(x.dte_calendar_days>0 for x in records),
        "incomplete_unresolved_positions":unresolved,
        "trades":records,
        "DISCLAIMER":"Event-observed option quote ASK->BID with assumed slippage and fees, no actual fills. Closed-trade DD excludes intratrade risk.",
    }


def compare_modes(stock_bars:Sequence[StockMinute],
                  quotes:Sequence[NBBO],
                  *,starting_cash:float=INITIAL_CASH_USD)->dict:
    universe=screening_audit([x for x in stock_bars if x.symbol!="SPY"])
    per_mode={mode:_source_signals(stock_bars,mode)
              for mode in ("SPY_0DTE","HIGH_BETA_STOCK_OPTIONS")}
    days=sorted(_complete_sessions(stock_bars,"SPY_0DTE")
                & _complete_sessions(stock_bars,"HIGH_BETA_STOCK_OPTIONS"))
    # This only intersects sessions present in files, not guarantee all
    # symbols were surveyed that date. Missing universe is a sample bias.
    details={}
    ledger=[]
    for policy in POLICIES:
        m={}
        for mode,signals in per_mode.items():
            result=simulate(mode=mode,policy=policy,raw_signals=signals,
                            option_quotes=quotes,common_days=days,
                            starting_cash=starting_cash)
            trades=result.pop("trades")
            ledger.extend(asdict(t) for t in trades)
            m[mode]=result
        a=m["SPY_0DTE"]
        b=m["HIGH_BETA_STOCK_OPTIONS"]
        comparable=(len(days)>=20 and
                    a["eligible_for_complete_head_to_head_ranking"] and
                    b["eligible_for_complete_head_to_head_ranking"])
        same_expiry=b["stock_options_1_to_7_calendar_DTE_count"]==0
        # Never claim a reliable winner just because a sample lost less;
        # data could contain survivorship bias or previously seen dates.
        details[policy]={
            "modes":m,
            "historical_comparison_status":(
                "HISTORICAL_DESCRIPTIVE_ONLY_NEEDS_UNSEEN_VALIDATION"
                if comparable and same_expiry
                else ("DIFFERENT_OPTION_TENORS_NOT_LIKE_FOR_LIKE"
                      if comparable else "BLOCKED_INCOMPLETE_OR_UNOBSERVED_QUOTES")),
            "higher_OBSERVED_historical_scenario_net_pnl_if_complete":(
                ("SPY_0DTE" if a["net_realized_quote_assumption_pnl_usd"]>
                 b["net_realized_quote_assumption_pnl_usd"] else
                 "HIGH_BETA_STOCK_OPTIONS")
                if comparable and same_expiry and
                a["net_realized_quote_assumption_pnl_usd"]!=
                b["net_realized_quote_assumption_pnl_usd"] else None),
            "true_predictive_winner":None,
        }
    return {
        "experiment":"strict_real_high_beta_stock_option_mode_vs_SPY_0DTE",
        "strict_stock_screen":universe,
        "matched_calendar_dates":len(days),
        "dates":days,
        "scenario_starting_settled_cash_usd":starting_cash,
        "contract_premium_exposure_limit_fraction":CONTRACT_EXPOSURE_LIMIT_FRACTION,
        "cost_assumptions":{"per_contract_each_side_usd":FEE_PER_CONTRACT_PER_SIDE_USD,
            "adverse_option_ticks_per_share_each_side":EXTRA_OPTION_SLIPPAGE_PER_SHARE},
        "stock_nearest_option_expiry_days_allowed":[0,7],
        "spy_options_expiry_days_required":0,
        "matched_event_time_option_nbbo_required":True,
        "underlying_intraday_PnL_not_used":True,
        "uses_reused_untouched_holdout":False,
        "live_orders_enabled":False,
        "incremental_data_purchase_usd":0,
        "policies":details,"filled_trade_ledger":ledger,
    }


def _paths(root:Path,prefix:str)->tuple[Path,...]:
    return tuple(sorted([*root.glob(f"{prefix}*.csv"),*root.glob(f"{prefix}*.csv.gz")]))


def main(argv:Sequence[str]|None=None)->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir",type=Path,default=Path("/data/research"))
    parser.add_argument("--output-dir",type=Path,
                        default=Path("/data/research/high_beta_stock_mode"))
    parser.add_argument("--initial-cash",type=float,default=INITIAL_CASH_USD)
    a=parser.parse_args(argv)
    a.output_dir.mkdir(parents=True,exist_ok=True)
    stock_paths=_paths(a.data_dir,"stock_minutes_")
    quote_paths=_paths(a.data_dir,"option_nbbo_")
    missing=[]
    if not stock_paths:
        missing.append("stock_minutes_*.csv(.gz) spanning UNFILTERED stock universe AND SPY with point-in-time historical screen metrics")
    if not quote_paths:
        missing.append("option_nbbo_*.csv(.gz) observed-event SPY+stock OCC options NBBO and sizes")
    if missing:
        ready={
            "experiment":"HIGH_BETA_STOCK_OPTIONS_VS_SPY_0DTE",
            "status":"BLOCKED_MISSING_HISTORICAL_REAL_MARKET_INPUTS",
            "missing":missing,"stock_universe_screened":False,
            "historical_option_PnL":None,"profitable_strategy":None,
            "never_substitute_128_SPY_only_minutes_for_stock_universe_or_option_nbbo":True,
            "thresholds_requested":{
                "optionable":True,"RVOL_gt":1,"ATR14_dollars_gt":1,
                "cumulative_shares_gt":1000000,"price_dollars_gt":30,
                "beta252_gt":1.5},
            "options_tenor_caveat":"SPY 0DTE versus nearest stock option 0-7 calendar DTE; stratify expiry for fair assessment.",
            "new_data_purchased_usd":0,"live_order_changes":0,
        }
        (a.output_dir/"readiness.json").write_text(json.dumps(ready,indent=2))
        print("HIGH BETA COMPARISON READINESS: "+json.dumps(ready,sort_keys=True),flush=True)
        return 0
    bars=read_stock_minutes(stock_paths)
    quotes=read_event_options(quote_paths)
    if not any(x.symbol=="SPY" for x in bars) or not any(x.symbol!="SPY" for x in bars):
        raise ValueError("need BOTH SPY and an unfiltered contemporaneous STOCK universe")
    if not any(x.underlying=="SPY" for x in quotes) or not any(x.underlying!="SPY" for x in quotes):
        raise ValueError("need genuine SPY and STOCK options quotes, not spot-only bars")
    result=compare_modes(bars,quotes,starting_cash=a.initial_cash)
    ledger=result.pop("filled_trade_ledger")
    (a.output_dir/"summary.json").write_text(json.dumps(result,indent=2,sort_keys=True))
    with (a.output_dir/"OCC_NBBO_ASK_BID_CASH_SCENARIO_NOT_LIVE_TRADES.csv").open("w",newline="") as f:
        if ledger:
            w=csv.DictWriter(f,fieldnames=list(ledger[0]))
            w.writeheader()
            w.writerows(ledger)
    print("HIGH BETA COMPARISON: "+json.dumps({
        "matched_calendar_days":result["matched_calendar_dates"],
        "days_with_any_stock_candidate":sum(
            x["symbols_ever_passed_AS_OF_that_minute"]>0
            for x in result["strict_stock_screen"]["coverage_by_day"].values()),
        "policy_status":{p:x["historical_comparison_status"] for p,x in result["policies"].items()},
        "real_options_historical_data":True,"new_data_purchase_usd":0,
        "report_path":str(a.output_dir),
    },sort_keys=True),flush=True)
    for policy,result_ in result["policies"].items():
        for mode,m in result_["modes"].items():
            print("HIGH BETA MODE RESULT: "+json.dumps({
                "policy":policy,"mode":mode,"trades":m["observed_completed_option_roundtrips"],
                "quote_coverage_complete":m["quote_and_cash_coverage_complete"],
                "net_pnl_usd_scenario":m["net_realized_quote_assumption_pnl_usd"],
                "scenario_account_return_percent":m["net_return_on_INITIAL_SCENARIO_cash_percent"],
                "days_positive":m["days_profit_usd_positive"],
                "days_negative":m["days_profit_usd_negative"],
                "net_premium_return_pct":m["mean_premium_return_pct"],
                "max_closed_trade_drawdown_fraction":m["max_closed_trade_equity_drawdown_fraction_UNDERSTATES_INTRATRADE"],
                "caution":result_["historical_comparison_status"],
            },sort_keys=True),flush=True)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
