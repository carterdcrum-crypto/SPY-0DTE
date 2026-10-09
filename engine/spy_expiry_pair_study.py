"""SPY **0DTE versus 1 next-TRADING-session-to-expiry** quote-based study.

Frozen SPY signals, same entry clock, direction, exit horizon, calendar days,
and fee/slippage assumptions for both expiration variants. As-of event NBBO
ASK entry/BID exit, near-.50 actual quoted delta (no Black-Scholes invention).
Research ONLY: these are QUOTE SCENARIOS, not broker fills or live returns.

No paid market data purchase, network request, AI price simulation, or orders.
Old SPY-only snapshot bars/CBBO minute buckets DO NOT qualify as observed
event quotes or complete OHLCV. Missing data produces explicit BLOCKED audit.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Sequence

from .data import HistoricalFrame
from .market import MarketSnapshot
from .session_calendar import session_close
from .weekly_options_data import (
    NY,Bar,Quote,aware,_open_file,_get_file,ACCEPTED_SCHEMAS,
)
from .weighted_daily_coverage import (
    POLICIES,Opportunity,session_opportunities,select_nonoverlapping,
)

CONTRACT_MULTIPLIER=100
ENTRY_LATENCY_SECONDS=2
MAX_ENTRY_EVENT_DELAY_SECONDS=90
MAX_EXIT_EVENT_DELAY_SECONDS=90
HOLD_MINUTES=10
MINUTES_BEFORE_CLOSE_TO_EXIT=15
MIN_ABS_DELTA=.40
MAX_ABS_DELTA=.60
MAX_NBBO_SPREAD_FRACTION=.15
MIN_OPEN_INTEREST=25
ILLUSTRATIVE_FEES_PER_CONTRACT_EACH_SIDE=.68
ADVERSE_SLIPPAGE_PER_SHARE_EACH_SIDE=.01
DEFAULT_SCENARIO_CASH=10000.
MAX_ORIGINAL_CASH_FRACTION_PER_PREMIUM=.10
MIN_MATCHED_DAYS=20
MIN_MATCHED_PAIRS=30


@dataclass(frozen=True)
class DteSession:
    day:date
    bars:tuple[Bar,...]
    quotes:tuple[Quote,...]
    close_at:datetime


@dataclass(frozen=True)
class QuoteRoundTrip:
    expiry_kind:str
    policy:str
    signal_at:str
    entry_observed_at:str
    exit_observed_at:str
    contract:str
    right:str
    expiration:str
    ask_plus_adverse_slippage:float
    bid_minus_adverse_slippage:float
    entry_cash_debit_usd:float
    exit_cash_receivable_usd:float
    realized_quote_pnl_usd:float
    premium_return_percent:float


def next_trading_session(day:date)->date:
    """Recognized exchange calendar only; Friday/holiday 1DTE is not +1 date."""
    if session_close(day) is None:
        raise ValueError("not a verified exchange session")
    for n in range(1,16):
        tomorrow=day+timedelta(days=n)
        if session_close(tomorrow) is not None:
            return tomorrow
    raise ValueError("next trading session not in recognized calendar")


def load_pair_session(directory:Path,day:date)->DteSession:
    close=session_close(day)
    if close is None:
        raise ValueError("unknown or closed exchange day")
    bars_file=_get_file(directory,f"spy_bars_{day}")
    quotes_file=_get_file(directory,f"spy_option_quotes_{day}")
    if bars_file is None or quotes_file is None:
        raise FileNotFoundError(f"{day}: complete OHLCV and actual event NBBO BOTH required")
    open_at=datetime.combine(day,time(9,30),NY)
    close_at=datetime.combine(day,close,NY)
    bars=[]
    with _open_file(bars_file) as f:
        reader=csv.DictReader(f)
        required={"ts_start","open","high","low","close","volume"}
        if not required.issubset(reader.fieldnames or ()):
            raise ValueError("missing genuine SPY completed OHLCV bar fields")
        for row in reader:
            b=Bar(aware(row["ts_start"]),float(row["open"]),float(row["high"]),
                  float(row["low"]),float(row["close"]),int(row["volume"]))
            if open_at<=b.start.astimezone(NY) and b.available_at.astimezone(NY)<=close_at:
                bars.append(b)
    bars.sort(key=lambda x:x.start)
    if any((b.start-a.start)!=timedelta(minutes=1) for a,b in zip(bars,bars[1:])):
        raise ValueError("discontinuous SPY 1-minute OHLCV input: fail closed")
    expirations={day,next_trading_session(day)}
    quotes=[]
    with _open_file(quotes_file) as f:
        reader=csv.DictReader(f)
        required={"observed_at","symbol","expiration","right","strike",
                  "bid","ask","delta","bid_size","ask_size","volume",
                  "open_interest","schema"}
        if not required.issubset(reader.fieldnames or ()):
            raise ValueError("missing observed event-time OCC option quote fields")
        for row in reader:
            if row["schema"].strip().lower() not in ACCEPTED_SCHEMAS:
                raise ValueError("no minute-bucket CBBO or synthetic options timestamps")
            expiry=date.fromisoformat(row["expiration"])
            if expiry not in expirations:
                continue
            q=Quote(
                aware(row["observed_at"]),row["symbol"].replace(" ",""),
                expiry,row["right"].strip().lower(),float(row["strike"]),
                float(row["bid"]),float(row["ask"]),float(row["delta"]),
                int(row["bid_size"]),int(row["ask_size"]),int(row["volume"]),
                int(row["open_interest"]),
            )
            if open_at<=q.observed_at.astimezone(NY)<=close_at:
                quotes.append(q)
    quotes.sort(key=lambda q:(q.observed_at,q.symbol))
    return DteSession(day,tuple(bars),tuple(quotes),close_at.astimezone(timezone.utc))


def _minute_of_day(when:datetime)->int:
    e=when.astimezone(NY)
    return e.hour*60+e.minute


def generate_same_signals(
    session:DteSession,prior_day_minute_volumes:dict[int,list[int]],
)->list[Opportunity]:
    """Existing causal MACD/RSI/SMA weighted SPY setups, unchanged.

    The extra volume proxy uses **previous complete days** at matching
    bar clock, never same-day future volume, shared across expiries.
    """
    frames=[]
    for b in session.bars:
        known=prior_day_minute_volumes.get(_minute_of_day(b.start),[])
        vol_ratio=(b.volume/(sum(known)/len(known))
                   if len(known)>=3 and sum(known)>0 else 1.)
        frames.append(HistoricalFrame(
            timestamp=b.start,options=(),
            market=MarketSnapshot(
                spot=b.close,bid=b.close,ask=b.close,volume_ratio=vol_ratio,
                realized_volatility=0.,implied_volatility=0.,
                minutes_to_close=(session.close_at-b.available_at).total_seconds()/60,
            ),
        ))
    return session_opportunities(frames)


def _quote_eligible(q:Quote,expiry:date,right:str)->bool:
    return bool(
        q.expiry==expiry and q.right==right
        and q.bid>0 and q.ask>q.bid
        and q.bid_size>=1 and q.ask_size>=1
        and q.open_interest>=MIN_OPEN_INTEREST
        and MIN_ABS_DELTA<=abs(q.delta)<=MAX_ABS_DELTA
        and (q.ask-q.bid)/((q.ask+q.bid)/2)<=MAX_NBBO_SPREAD_FRACTION
    )


def _entry(session:DteSession,op:Opportunity,expiry:date)->Quote|None:
    """Only post-signal OBSERVED quotes; no stale lookback future contract."""
    signal_at=datetime.fromisoformat(op.signal_at)
    first=signal_at+timedelta(seconds=ENTRY_LATENCY_SECONDS)
    last=first+timedelta(seconds=MAX_ENTRY_EVENT_DELAY_SECONDS)
    matching=[
        q for q in session.quotes
        if first<=q.observed_at<=last
        and _quote_eligible(q,expiry,op.original_direction)
    ]
    if not matching:
        return None
    # Choose the earliest actual observation time; select delta only
    # among options contemporaneously visible AT THAT TIMESTAMP.
    first_event=min(q.observed_at for q in matching)
    return min((q for q in matching if q.observed_at==first_event),
               key=lambda q:(abs(abs(q.delta)-.5),(q.ask-q.bid)/q.ask,
                             q.symbol))


def _exit(session:DteSession,entry:Quote)->Quote|None:
    """First witnessed bid quote from EXACT SAME contract after 10 minutes."""
    desired=entry.observed_at+timedelta(minutes=HOLD_MINUTES)
    deadline=min(session.close_at-timedelta(minutes=MINUTES_BEFORE_CLOSE_TO_EXIT),
                 desired+timedelta(seconds=MAX_EXIT_EVENT_DELAY_SECONDS))
    qualified=[
        q for q in session.quotes
        if q.symbol==entry.symbol and desired<=q.observed_at<=deadline
        and q.bid_size>=1 and q.bid>=0
    ]
    return min(qualified,key=lambda q:q.observed_at) if qualified else None


def _roundtrip(op:Opportunity,entry:Quote,exit_quote:Quote,
               kind:str,policy:str)->QuoteRoundTrip:
    buy=entry.ask+ADVERSE_SLIPPAGE_PER_SHARE_EACH_SIDE
    sell=max(0.,exit_quote.bid-ADVERSE_SLIPPAGE_PER_SHARE_EACH_SIDE)
    debit=CONTRACT_MULTIPLIER*buy+ILLUSTRATIVE_FEES_PER_CONTRACT_EACH_SIDE
    credit=CONTRACT_MULTIPLIER*sell-ILLUSTRATIVE_FEES_PER_CONTRACT_EACH_SIDE
    pnl=credit-debit
    return QuoteRoundTrip(
        expiry_kind=kind,policy=policy,signal_at=op.signal_at,
        entry_observed_at=entry.observed_at.isoformat(),
        exit_observed_at=exit_quote.observed_at.isoformat(),
        contract=entry.symbol,right=entry.right,expiration=entry.expiry.isoformat(),
        ask_plus_adverse_slippage=round(buy,4),
        bid_minus_adverse_slippage=round(sell,4),
        entry_cash_debit_usd=round(debit,4),
        exit_cash_receivable_usd=round(credit,4),
        realized_quote_pnl_usd=round(pnl,4),
        premium_return_percent=round(100*pnl/debit,4),
    )


def _blank_account(cash:float)->dict:
    return {"settled":cash,"receivables":[],"pnl":0.,"peak":cash,
            "drawdown":0.,"trades":[]}


def _settle(account:dict,session_day:date)->None:
    due=0.
    remaining=[]
    for settle_day,amount in account["receivables"]:
        if settle_day<=session_day:
            due+=amount
        else:
            remaining.append((settle_day,amount))
    account["settled"]+=due
    account["receivables"]=remaining


def _apply_pair_trade(account:dict,trade:QuoteRoundTrip,day:date)->None:
    account["settled"]-=trade.entry_cash_debit_usd
    account["receivables"].append(
        (next_trading_session(day),trade.exit_cash_receivable_usd))
    account["pnl"]+=trade.realized_quote_pnl_usd
    account["trades"].append(trade)
    equity=account["pnl"]  # compare closed-trade PnL path, not cash settlement
    account["peak"]=max(account["peak"],equity)
    account["drawdown"]=max(account["drawdown"],account["peak"]-equity)


def _stats(account:dict,initial_cash:float,days:Sequence[str])->dict:
    trades=account["trades"]
    pnl=[x.realized_quote_pnl_usd for x in trades]
    daily={day:0. for day in days}
    for x in trades:
        d=datetime.fromisoformat(x.exit_observed_at).astimezone(NY).date().isoformat()
        if d in daily:daily[d]+=x.realized_quote_pnl_usd
    sumwin=sum(max(0.,v) for v in pnl)
    sumloss=-sum(min(0.,v) for v in pnl)
    return {
        "observed_paired_1_contract_trades":len(trades),
        "quote_scenario_pnl_usd":round(sum(pnl),2) if trades else None,
        "return_pct_on_initial_scenario_cash":round(
            100*sum(pnl)/initial_cash,4) if trades else None,
        "average_premium_return_pct":round(
            statistics.fmean(x.premium_return_percent for x in trades),4)
            if trades else None,
        "win_rate_of_paired_quote_scenarios":(
            sum(v>0 for v in pnl)/len(pnl) if pnl else None),
        "profit_factor":round(sumwin/sumloss,4) if sumloss>0 else None,
        "positive_option_quote_pnl_days":sum(v>0 for v in daily.values()),
        "negative_option_quote_pnl_days":sum(v<0 for v in daily.values()),
        "zero_or_untraded_days":sum(v==0 for v in daily.values()),
        "max_closed_trade_additive_drawdown_usd_NOT_INTRATRADE":round(
            account["drawdown"],2),
        "actual_broker_fills":0,
        "intratrade_max_drawdown":None,
    }


def _policy_compare(
    sessions:Sequence[DteSession],candidates:dict[date,list[Opportunity]],
    policy:str,starting_cash:float,
)->tuple[dict,list[dict]]:
    accounts={"0DTE":_blank_account(starting_cash),
              "1DTE":_blank_account(starting_cash)}
    rejected=defaultdict(int)
    rows=[]
    last_exit=None
    days=[s.day.isoformat() for s in sessions]
    for session in sessions:
        for acc in accounts.values():
            _settle(acc,session.day)
        for op in select_nonoverlapping(candidates[session.day],policy):
            signal=datetime.fromisoformat(op.signal_at)
            if last_exit is not None and signal<=last_exit:
                rejected["overlapping_quotes"]+=1
                continue
            # Both options must be sold before the same day's close.
            if signal+timedelta(
                minutes=HOLD_MINUTES+MINUTES_BEFORE_CLOSE_TO_EXIT+2,
            )>session.close_at:
                rejected["late_session"]+=1
                continue
            entries={
                "0DTE":_entry(session,op,session.day),
                "1DTE":_entry(session,op,next_trading_session(session.day)),
            }
            if not all(entries.values()):
                if entries["0DTE"] is None:rejected["missing_0dte_entry_event"]+=1
                if entries["1DTE"] is None:rejected["missing_1dte_entry_event"]+=1
                continue
            exits={kind:_exit(session,entry) for kind,entry in entries.items()}
            if not all(exits.values()):
                if exits["0DTE"] is None:rejected["missing_0dte_exit_event"]+=1
                if exits["1DTE"] is None:rejected["missing_1dte_exit_event"]+=1
                # An unidentified future liquidation invalidates ANY later
                # pair results on that day; mark the whole comparison incomplete.
                rejected["unresolved_entered_option_position"]+=1
                last_exit=session.close_at
                continue
            trades={
                kind:_roundtrip(op,entries[kind],exits[kind],kind,policy)
                for kind in ("0DTE","1DTE")
            }
            if any(
                t.entry_cash_debit_usd>starting_cash*MAX_ORIGINAL_CASH_FRACTION_PER_PREMIUM
                or t.entry_cash_debit_usd>accounts[kind]["settled"]
                for kind,t in trades.items()
            ):
                rejected["unaffordable_one_contract_in_either_book"]+=1
                continue
            for kind,t in trades.items():
                _apply_pair_trade(accounts[kind],t,session.day)
                rows.append(asdict(t))
            last_exit=max(datetime.fromisoformat(t.exit_observed_at)
                          for t in trades.values())
    complete=not any(
        rejected[k] for k in (
            "missing_0dte_entry_event","missing_1dte_entry_event",
            "missing_0dte_exit_event","missing_1dte_exit_event",
            "unresolved_entered_option_position",
        ))
    matching=len(accounts["0DTE"]["trades"])
    enough=len(days)>=MIN_MATCHED_DAYS and matching>=MIN_MATCHED_PAIRS
    return {
        "policy":policy,
        "same_underlying_signal_entry_exit_horizon":True,
        "matched_quote_pairs":matching,
        "trading_days_included":len(days),
        "matched_data_coverage_complete":complete,
        "minimum_sample_achieved":enough,
        "reject_reasons":dict(rejected),
        "zero_trade_benchmark_usd":0,
        "accounts":{
            kind:_stats(account,starting_cash,days)
            for kind,account in accounts.items()},
        "winner_status":(
            "DESCRIPTIVE_PAIRED_QUOTES_ONLY_NOT_PROSPECTIVE_EDGE"
            if enough and complete
            else "BLOCKED_INSUFFICIENT_MATCHED_REAL_OPTION_QUOTES"),
        "future_predictive_winner":None,
    },rows


def compare(sessions:Sequence[DteSession],*,starting_cash:float=DEFAULT_SCENARIO_CASH)->dict:
    if not sessions or starting_cash<=0:
        raise ValueError("need sessions and positive cash")
    if any(a.day>=b.day for a,b in zip(sessions,sessions[1:])):
        raise ValueError("sessions must be strictly chronological")
    prior_volume:dict[int,list[int]]=defaultdict(list)
    candidates={}
    for session in sessions:
        candidates[session.day]=generate_same_signals(session,prior_volume)
        # Only now update yesterday's complete bar volumes for tomorrow:
        for b in session.bars:
            key=_minute_of_day(b.start)
            prior_volume[key].append(b.volume)
            if len(prior_volume[key])>20:
                prior_volume[key]=prior_volume[key][-20:]
    models={}
    ledger=[]
    for policy in POLICIES:
        m,rows=_policy_compare(sessions,candidates,policy,starting_cash)
        models[policy]=m
        ledger.extend(rows)
    return {
        "experiment":"SPY_same_spot_signals_0DTE_vs_next_TRADING_SESSION_expiration",
        "data_sessions":len(sessions),
        "same_frozen_macd_rsi_sma_price_action_strategies":list(POLICIES),
        "1DTE_meaning":"Expiry on NEXT VERIFIED TRADING SESSION, not necessarily tomorrow calendar date",
        "same_day_closures_only":True,
        "option_delta_target_abs":.5,
        "actual_transaction_fills":0,
        "model_is_research_only":True,
        "auto_trade_unchanged_off":True,
        "adaptive_signal_gate_disabled":True,
        "new_market_data_purchases_usd":0,
        "scenario_cash_usd":starting_cash,
        "cost_assumptions":{
            "per_contract_side_fee_usd":ILLUSTRATIVE_FEES_PER_CONTRACT_EACH_SIDE,
            "adverse_spread_slippage_per_share_per_side":ADVERSE_SLIPPAGE_PER_SHARE_EACH_SIDE,
        },
        "known_dates_previously_inspected_no_new_holdout":True,
        "strategies":models,"trade_ledger":ledger,
    }


def input_audit(directory:Path)->dict:
    bars=sorted(directory.glob("spy_bars_*.csv*"))
    quotes=sorted(directory.glob("spy_option_quotes_*.csv*"))
    legacy=sorted(directory.glob("spy_0dte_*.csv*"))
    paired_dates=sorted(
        {p.name[9:19] for p in bars}&{p.name[18:28] for p in quotes})
    return {
        "bar_files":len(bars),"event_quote_files":len(quotes),
        "legacy_spy_snapshot_files_UNFIT":len(legacy),
        "paired_bar_and_quote_day_files":len(paired_dates),
        "1DTE_historical_quote_expiration_coverage":"NOT_CHECKED" if paired_dates else "MISSING",
        "0DTE_historical_quote_expiration_coverage":"NOT_CHECKED" if paired_dates else "MISSING",
        "ready_to_score":bool(paired_dates),
        "blocker":None if paired_dates else (
            "Need genuine completed SPY 1-minute OHLCV and SAME-SESSION event-observed "
            "SPY OCC NBBO for BOTH 0DTE and next trading-session expiry; archived "
            "minute SPY snapshots/cbbo-1m do not qualify."
        ),
        "no_paid_data_fetch":True,"new_market_data_purchases_usd":0,
        "actual_option_profit_result":None,
    }


def main(argv:Sequence[str]|None=None)->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data-dir",type=Path,default=Path("/data/research"))
    p.add_argument("--output-dir",type=Path,default=Path("/data/research/spy_0_vs_1dte"))
    p.add_argument("--initial-cash",type=float,default=DEFAULT_SCENARIO_CASH)
    args=p.parse_args(argv)
    args.output_dir.mkdir(parents=True,exist_ok=True)
    audit=input_audit(args.data_dir)
    (args.output_dir/"readiness.json").write_text(json.dumps(audit,indent=2,sort_keys=True))
    print("SPY 0DTE VS 1DTE DATA AUDIT: "+json.dumps(audit,sort_keys=True),flush=True)
    if not audit["ready_to_score"]:
        return 0
    # Failure to obtain BOTH expiry variants must not create fake P&L.
    dates=sorted(date.fromisoformat(
        p.name[9:19]) for p in args.data_dir.glob("spy_bars_*.csv*")
        if (args.data_dir/f"spy_option_quotes_{p.name[9:19]}.csv").exists()
        or (args.data_dir/f"spy_option_quotes_{p.name[9:19]}.csv.gz").exists()
    )
    sessions=[load_pair_session(args.data_dir,day) for day in sorted(set(dates))]
    insufficient={
        s.day.isoformat():{
            "0DTE":sum(q.expiry==s.day for q in s.quotes),
            "1DTE":sum(q.expiry==next_trading_session(s.day) for q in s.quotes),
        } for s in sessions
        if not any(q.expiry==s.day for q in s.quotes)
        or not any(q.expiry==next_trading_session(s.day) for q in s.quotes)
    }
    if insufficient:
        audit.update({"ready_to_score":False,
             "blocker":"At least one paired date lacks observed quote events for 0DTE or 1 next-session expiry",
             "missing_expiration_quote_coverage_by_day":insufficient})
        (args.output_dir/"readiness.json").write_text(json.dumps(audit,indent=2))
        print("SPY 0DTE VS 1DTE BLOCKED: "+json.dumps(audit,sort_keys=True),flush=True)
        return 0
    result=compare(sessions,starting_cash=args.initial_cash)
    rows=result.pop("trade_ledger")
    (args.output_dir/"comparison.json").write_text(json.dumps(result,indent=2,sort_keys=True))
    with (args.output_dir/"matched_observed_quotes_NOT_BROKER_FILLS.csv").open(
        "w",newline="",encoding="utf8",
    ) as f:
        if rows:
            writer=csv.DictWriter(f,fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    for policy,data in result["strategies"].items():
        print("SPY 0DTE VS 1DTE POLICY: "+json.dumps({
            "policy":policy,"days":data["trading_days_included"],
            "matched_pairs":data["matched_quote_pairs"],
            "0dte_quote_scenario":data["accounts"]["0DTE"],
            "1dte_quote_scenario":data["accounts"]["1DTE"],
            "winner_status":data["winner_status"],
            "predictive_winner":None,
        },sort_keys=True),flush=True)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
