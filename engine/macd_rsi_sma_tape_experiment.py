"""Research-only MACD(12,26,9) + Wilder RSI(14) + SMA(20/50) +
close-price action + actual trade-tape side/size fusion.

The archived SPY files have 1m SPOT/CLOSE and an observed volume_ratio.
They do NOT contain OHLC high/low bars, signed tick trades or depth.
Thus all "price action" here is a completed-CLOSE proxy, and volume_ratio
is a proxy, NEVER genuine tape reading. Genuine tape filters are marked
UNAVAILABLE unless the user explicitly supplies timestamped SPY trade
events with aggressor side and quantity. This module does not buy data,
execute trades, or calculate an executable SPY 0DTE option P&L.

Signals are observed as of the completed *third* confirmation minute
from the established no-overlap close-breakout baseline. Entry and
exit are *next completed SPY close* and 10-minute-later close proxies.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import statistics
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Iterable, Sequence

from .burst_research import _research_files, iter_research_directory
from .causal_breakout_router import session_delayed_events
from .data import HistoricalFrame
from .free_signal_screen import ET, PERIODS

POLICIES = (
    "same_delayed_clock_baseline",
    "macd_rsi_sma",
    "macd_rsi_sma_close_price_action",
    "macd_rsi_sma_price_action_volume_proxy",
    "macd_rsi_sma_price_action_true_tape",
    "full_fusion_true_tape_plus_volume",
)
TAPE_POLICIES = frozenset(POLICIES[-2:])
MIN_INDICATOR_BARS = 75
MACD_CROSS_RECENCY_BARS = 5
RSI_LENGTH = 14
CLOSE_SMA_FAST = 20
CLOSE_SMA_SLOW = 50
VOLUME_RATIO_THRESHOLD = 1.25
MIN_CONFIRM_MOVE_BPS = 1.0
MAX_CONFIRM_ADVERSE_BPS = 2.0
TAPE_LOOKBACK_SECONDS = 180
TAPE_MIN_CLASSIFIED_TRADES = 10
TAPE_MIN_SIDE_IMBALANCE = 0.20
ASSUMED_SPY_SPOT_FRICTION_BPS = 2.0  # NOT options trading cost
BOOTSTRAP_REPS = 1000
BOOTSTRAP_BLOCK_SESSIONS = 5
BOOTSTRAP_SEED = 30017


@dataclass(frozen=True)
class TapeTrade:
    """Confirmed SPY *trade* event, signed by the feed's trade aggressor.

    'B' = buyer-initiated, 'A' = seller-initiated. Never infer side from
    close-based price changes or interpret quote updates as executions.
    """
    timestamp: datetime
    side: str
    size: int
    price: float
    symbol: str = "SPY"


@dataclass(frozen=True)
class TapeSignal:
    available: bool
    trades: int
    buy_size: int
    sell_size: int
    signed_imbalance: float | None
    reason: str


@dataclass(frozen=True)
class FusedOpportunity:
    day: str
    period: str
    initial_direction: str
    breakout_signal_at: str
    confirmed_at: str
    entry_proxy_at: str
    exit_proxy_at: str
    macd: float
    macd_signal: float
    macd_cross_aligned_recent: bool
    rsi14: float
    sma20: float
    sma50: float
    indicator_confluence: bool
    close_price_action_confirmation: bool
    volume_ratio_at_original_breakout: float
    tape_available: bool
    tape_trades: int
    tape_buy_size: int
    tape_sell_size: int
    tape_signed_imbalance: float | None
    tape_direction_confirmed: bool
    # Strictly outcome-only; NEVER used to generate a decision.
    signed_10min_spy_close_move_bps: float


def _sma_at(prices:Sequence[float],length:int)->list[float|None]:
    result=[None]*len(prices)
    running=0.
    for i,x in enumerate(prices):
        running+=x
        if i>=length:
            running-=prices[i-length]
        if i>=length-1:
            result[i]=running/length
    return result


def _ema_seeded(values:Sequence[float|None],span:int)->list[float|None]:
    """SMA seed at first full window of finite values, then causal EMA."""
    ret=[None]*len(values)
    seed=[]
    k=2./(span+1.)
    ema=None
    for i,x in enumerate(values):
        if x is None:
            seed=[]
            ema=None
            continue
        if not math.isfinite(x):
            raise ValueError("non-finite EMA input")
        if ema is None:
            seed.append(x)
            if len(seed)==span:
                ema=statistics.fmean(seed)
                ret[i]=ema
        else:
            ema=k*x+(1-k)*ema
            ret[i]=ema
    return ret


def _rsi_wilder(prices:Sequence[float],period:int=RSI_LENGTH)->list[float|None]:
    ret=[None]*len(prices)
    if len(prices)<=period:
        return ret
    moves=[prices[k]-prices[k-1] for k in range(1,len(prices))]
    avg_gain=sum(max(x,0.) for x in moves[:period])/period
    avg_loss=sum(max(-x,0.) for x in moves[:period])/period
    def rsi(g:float,l:float)->float:
        if l==0:
            return 50. if g==0 else 100.
        return 100-100/(1+g/l)
    ret[period]=rsi(avg_gain,avg_loss)
    for i in range(period+1,len(prices)):
        diff=moves[i-1]
        avg_gain=((period-1)*avg_gain+max(diff,0.))/period
        avg_loss=((period-1)*avg_loss+max(-diff,0.))/period
        ret[i]=rsi(avg_gain,avg_loss)
    return ret


def _macd(prices:Sequence[float]):
    slow=_ema_seeded(prices,26)
    fast=_ema_seeded(prices,12)
    line=[(f-s if f is not None and s is not None else None)
          for f,s in zip(fast,slow)]
    signal=_ema_seeded(line,9)
    return line,signal


def _bps(now:float,earlier:float)->float:
    return (now/earlier-1.)*10000. if earlier>0 else math.nan


def _cross_recent(macd:Sequence[float|None],
                  signal:Sequence[float|None],end:int,
                  bullish:bool)->bool:
    """Cross must be known by T+3, occurred in last 5 COMPLETE bars."""
    if end<MACD_CROSS_RECENCY_BARS:
        return False
    direction=1 if bullish else -1
    if macd[end] is None or signal[end] is None:
        return False
    if direction*(macd[end]-signal[end])<=0:
        return False
    for i in range(end-MACD_CROSS_RECENCY_BARS+1,end+1):
        prev_m,prev_s=macd[i-1],signal[i-1]
        cur_m,cur_s=macd[i],signal[i]
        if None in (prev_m,prev_s,cur_m,cur_s):
            continue
        if direction*(prev_m-prev_s)<=0 and direction*(cur_m-cur_s)>0:
            return True
    return False


def read_verified_trade_tape(path:Path)->dict[str,tuple[TapeTrade,...]]:
    """Read only explicit SPY trade records with true signed aggressor side.

    CSV columns: ts_event (ISO-8601 with timezone), symbol, action,
    side, size, price. Requires action=='T' and SPY, side in A/B,
    strictly positive size and price. Reject *any* ambiguous/invalid
    observations instead of silently falling back to made-up tape.
    """
    grouped=defaultdict(list)
    with path.open(newline="",encoding="utf8") as f:
        reader=csv.DictReader(f)
        required={"ts_event","symbol","action","side","size","price"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError("missing required true SPY trade tape fields")
        last=None
        for row in reader:
            ts=datetime.fromisoformat(row["ts_event"].strip().replace("Z","+00:00"))
            if ts.tzinfo is None or ts.utcoffset() is None:
                raise ValueError("timezone-aware ts_event required")
            if row["symbol"]!="SPY" or row["action"]!="T" or row["side"] not in ("A","B"):
                raise ValueError("true SPY aggressor TRADE action and side required")
            size=int(row["size"])
            price=float(row["price"])
            if size<=0 or not math.isfinite(price) or price<=0:
                raise ValueError("invalid tape trade size/price")
            if last is not None and ts<last:
                raise ValueError("tape trades not chronologically ordered")
            last=ts
            day=ts.astimezone(ET).date().isoformat()
            grouped[day].append(TapeTrade(ts,row["side"],size,price))
    return {d:tuple(trades) for d,trades in grouped.items()}


def tape_at(trades:Sequence[TapeTrade]|None,as_of:datetime)->TapeSignal:
    if trades is None:
        return TapeSignal(False,0,0,0,None,"NO_VERIFIED_TRADE_TAPE_SOURCE")
    window_start=as_of-timedelta(seconds=TAPE_LOOKBACK_SECONDS)
    sub=[x for x in trades if window_start<=x.timestamp<=as_of]
    if len(sub)<TAPE_MIN_CLASSIFIED_TRADES:
        return TapeSignal(False,len(sub),0,0,None,"INSUFFICIENT_SIGNED_REAL_TRADES")
    buys=sum(t.size for t in sub if t.side=="B")
    sells=sum(t.size for t in sub if t.side=="A")
    if buys+sells<=0:
        return TapeSignal(False,len(sub),buys,sells,None,"ZERO_SIGNED_TAPE_VOLUME")
    imbalance=(buys-sells)/(buys+sells)
    return TapeSignal(True,len(sub),buys,sells,imbalance,"OBSERVED_SIGNED_TRADE_TAPE")


def session_opportunities(
    frames:Sequence[HistoricalFrame],
    *,
    tape:Sequence[TapeTrade]|None=None,
)->list[FusedOpportunity]:
    if not frames:
        return []
    prices=[f.market.spot for f in frames]
    times=[f.timestamp for f in frames]
    macd,signal=_macd(prices)
    rsi=_rsi_wilder(prices)
    sma20=_sma_at(prices,CLOSE_SMA_FAST)
    sma50=_sma_at(prices,CLOSE_SMA_SLOW)
    completed_to_idx={f.timestamp+timedelta(minutes=1):idx
                      for idx,f in enumerate(frames)}
    delayed=session_delayed_events(frames)
    results=[]
    for event in delayed:
        i=completed_to_idx[datetime.fromisoformat(event.confirmation_at)]
        if i<MIN_INDICATOR_BARS:
            continue
        if any((times[k+1]-times[k]).total_seconds()!=60
               for k in range(i-MIN_INDICATOR_BARS,i)):
            continue
        if any(v is None or not math.isfinite(v) for v in
               (macd[i],signal[i],rsi[i],sma20[i],sma50[i])):
            continue
        side=1 if event.original_direction=="call" else -1
        bullish=side>0
        cross=_cross_recent(macd,signal,i,bullish)
        rsi_aligned=(50<=rsi[i]<=75) if bullish else (25<=rsi[i]<=50)
        trend_aligned=(prices[i]>sma20[i]>sma50[i]) if bullish else (
            prices[i]<sma20[i]<sma50[i])
        # A *CLOSE-only* momentum/structure proxy. No candlestick highs/lows.
        # Read signal already includes prior 3 completed bars and boundary.
        prior_change=side*_bps(prices[i],prices[i-3])
        worst_pullback=min(side*_bps(prices[k],prices[i-3])
                           for k in range(i-2,i+1))
        price_confirmed=(
            event.confirm_extension_signed_bps>=0 and
            prior_change>=MIN_CONFIRM_MOVE_BPS and
            worst_pullback>=-MAX_CONFIRM_ADVERSE_BPS and
            not event.observed_first3_snapback
        )
        direction=side
        # At the end of bar i, nothing after confirmation can enter
        # the tape vote. If archive lacks real signed trades, tape
        # criteria remain N/A, not inferred from volume_ratio.
        t=tape_at(tape,datetime.fromisoformat(event.confirmation_at))
        tape_support=(
            t.available and t.signed_imbalance is not None and
            direction*t.signed_imbalance>=TAPE_MIN_SIDE_IMBALANCE
        )
        orig_index=completed_to_idx[datetime.fromisoformat(event.signal_at)]
        volume=frames[orig_index].market.volume_ratio
        results.append(FusedOpportunity(
            day=event.session,period=_period(date.fromisoformat(event.session)),
            initial_direction=event.original_direction,
            breakout_signal_at=event.signal_at,confirmed_at=event.confirmation_at,
            entry_proxy_at=event.proxy_entry_at,exit_proxy_at=event.proxy_exit_at,
            macd=macd[i],macd_signal=signal[i],
            macd_cross_aligned_recent=cross,
            rsi14=rsi[i],sma20=sma20[i],sma50=sma50[i],
            indicator_confluence=bool(cross and rsi_aligned and trend_aligned),
            close_price_action_confirmation=price_confirmed,
            volume_ratio_at_original_breakout=volume,
            tape_available=t.available,
            tape_trades=t.trades,tape_buy_size=t.buy_size,
            tape_sell_size=t.sell_size,tape_signed_imbalance=t.signed_imbalance,
            tape_direction_confirmed=tape_support,
            signed_10min_spy_close_move_bps=event.delayed_raw_breakout_direction_signed_spy_bps,
        ))
    return results


def _period(d:date)->str:
    for label,start,stop in PERIODS:
        if start<=d<=stop:
            return label
    return "outside_studied_periods"


def accepts(o:FusedOpportunity,policy:str)->bool:
    """Decision-only causal features; no outcome, future bar, or option quote."""
    if policy not in POLICIES:
        raise ValueError("unknown fixed technical-indicator research policy")
    if policy=="same_delayed_clock_baseline":
        return True
    if policy=="macd_rsi_sma":
        return o.indicator_confluence
    if policy=="macd_rsi_sma_close_price_action":
        return o.indicator_confluence and o.close_price_action_confirmation
    if policy=="macd_rsi_sma_price_action_volume_proxy":
        return (o.indicator_confluence and o.close_price_action_confirmation
                and math.isfinite(o.volume_ratio_at_original_breakout)
                and o.volume_ratio_at_original_breakout>=VOLUME_RATIO_THRESHOLD)
    if policy=="macd_rsi_sma_price_action_true_tape":
        return (o.indicator_confluence and o.close_price_action_confirmation
                and o.tape_available and o.tape_direction_confirmed)
    return (o.indicator_confluence and o.close_price_action_confirmation
            and math.isfinite(o.volume_ratio_at_original_breakout)
            and o.volume_ratio_at_original_breakout>=VOLUME_RATIO_THRESHOLD
            and o.tape_available and o.tape_direction_confirmed)


def _bootstrap(days:Sequence[str],selected:Sequence[FusedOpportunity])->dict:
    if not days:
        return {"available":False}
    by_day=defaultdict(float)
    for o in selected:
        by_day[o.day]+=o.signed_10min_spy_close_move_bps-ASSUMED_SPY_SPOT_FRICTION_BPS
    series=[by_day[d] for d in days]
    n=len(series)
    block=min(n,BOOTSTRAP_BLOCK_SESSIONS)
    rng=random.Random(BOOTSTRAP_SEED)
    means=[]
    for _ in range(BOOTSTRAP_REPS):
        picks=[]
        while len(picks)<n:
            st=rng.randrange(n)
            picks.extend((st+k)%n for k in range(block))
        means.append(statistics.fmean(series[j] for j in picks[:n]))
    means.sort()
    return {"ci95_daily_signed_spy_bps":[
        round(means[round(.025*(len(means)-1))],4),
        round(means[round(.975*(len(means)-1))],4)],
        "trading_days_including_no_signals":n,
        "method":"5-session circular block bootstrap of SPY basis-point proxies, NOT account returns"}


def _score(rows:Sequence[FusedOpportunity],days:Sequence[str],
           policy:str,has_tape_source:bool)->dict:
    applicable=not(policy in TAPE_POLICIES and not has_tape_source)
    selected=[o for o in rows if accepts(o,policy)] if applicable else []
    raw=[r.signed_10min_spy_close_move_bps for r in selected]
    net=[x-ASSUMED_SPY_SPOT_FRICTION_BPS for x in raw]
    daily=defaultdict(float)
    weekly=defaultdict(float)
    for r,v in zip(selected,net):
        daily[r.day]+=v
        iso=date.fromisoformat(r.day).isocalendar()
        weekly[(iso.year,iso.week)]+=v
    active_weeks={(date.fromisoformat(d).isocalendar().year,
                   date.fromisoformat(d).isocalendar().week) for d in days}
    running=top=decline=0.
    for day in days:
        running+=daily[day]
        top=max(top,running)
        decline=max(decline,top-running)
    return {
        "status":"EVALUATED" if applicable else "UNAVAILABLE_VERIFIED_SPY_TAPE",
        "days_in_period":len(days),
        "baseline_indicator_eligible_events":len(rows),
        "signals_selected":len(selected) if applicable else None,
        "signals_skipped":len(rows)-len(selected) if applicable else None,
        "correct_spy_direction":sum(x>0 for x in raw) if applicable else None,
        "correct_signed_gt2bp_spy":sum(x>2 for x in raw) if applicable else None,
        "correct_signed_gt5bp_spy":sum(x>5 for x in raw) if applicable else None,
        "mean_signed_spy_bps":round(statistics.fmean(raw),4) if raw else None,
        "mean_signed_spy_bps_after_hypothetical_2bp":(
            round(statistics.fmean(net),4) if net else None),
        "mean_net_signed_spy_bps_per_day_including_no_signal":(
            round(sum(net)/len(days),4) if applicable and days else None),
        "worst_week_additive_signed_spy_bps":(
            round(min(weekly.get(w,0.) for w in active_weeks),4)
            if applicable and active_weeks else None),
        "max_cumulative_additive_spy_bps_decline_NOT_EQUITY_DRAWDOWN":(
            round(decline,4) if applicable else None),
        "bootstrap":_bootstrap(days,selected) if applicable else None,
        "account_return":None,"option_pnl":None,"actual_option_fills":None,
        "account_max_drawdown":None,
    }


def run_study(frames:Iterable[HistoricalFrame], *,
              actual_tape:dict[str,tuple[TapeTrade,...]]|None=None)->dict:
    current=[]
    yesterday=None
    framecount=0
    sessions=0
    days=defaultdict(list)
    ledger=defaultdict(list)
    def flush()->None:
        nonlocal current,sessions
        if not current:
            return
        day=current[0].timestamp.astimezone(ET).date()
        sessions+=1
        label=_period(day)
        has_tape=actual_tape.get(day.isoformat()) if actual_tape is not None else None
        rows=session_opportunities(current,tape=has_tape)
        if label in {name for name,_,_ in PERIODS}:
            days[label].append(day.isoformat())
            ledger[label].extend(rows)
        current=[]
    for frame in frames:
        day=frame.timestamp.astimezone(ET).date()
        if yesterday is not None and day<yesterday:
            raise ValueError("market sessions not chronological")
        if yesterday is not None and day!=yesterday:
            flush()
        yesterday=day
        current.append(frame)
        framecount+=1
    flush()
    summary={}
    for period,_,_ in PERIODS:
        summary[period]={
            policy:_score(ledger[period],days[period],policy,actual_tape is not None)
            for policy in POLICIES
        }
    return {
        "experiment":"macd_12_26_9_rsi14_sma20_50_close_price_action_true_signed_tape_optional",
        "sessions":sessions,"frames":framecount,
        "indicator_parameters_fixed_before_history":[12,26,9,14,20,50,MACD_CROSS_RECENCY_BARS],
        "indicator_min_continuous_minutes":MIN_INDICATOR_BARS,
        "continuation_adaptive_gate_enabled":False,
        "tape_actual_aggressor_source_supplied":actual_tape is not None,
        "tape_proxy_fabrication":False,
        "research_only":True,"new_market_data_purchased":False,
        "historical_dates_previously_inspected_not_untouched":True,
        "policies":POLICIES,"periods":summary,
        "events":tuple(r for period in ledger for r in ledger[period]),
        "actual_option_fills":0,"option_pnl":None,"account_return":None,
    }


def _save_csv(path:Path,items:Sequence[dict])->None:
    with path.open("w",newline="",encoding="utf-8") as f:
        if items:
            writer=csv.DictWriter(f,fieldnames=list(items[0]))
            writer.writeheader()
            writer.writerows(items)


def main(argv:Sequence[str]|None=None)->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir",type=Path,default=Path("/data/research"))
    parser.add_argument("--output-dir",type=Path,
                        default=Path("/data/research/indicator_tape_fusion"))
    parser.add_argument("--verified-spy-tape-csv",type=Path,default=None,
                        help="Optional existing event-time trade CSV; never fetches or buys data")
    args=parser.parse_args(argv)
    source=_research_files(args.data_dir)
    if not source:
        print("INDICATOR TAPE EXPERIMENT BLOCKED: archived minute closes missing",flush=True)
        return 2
    true_tape=(read_verified_trade_tape(args.verified_spy_tape_csv)
               if args.verified_spy_tape_csv is not None else None)
    report=run_study(iter_research_directory(args.data_dir),actual_tape=true_tape)
    args.output_dir.mkdir(parents=True,exist_ok=True)
    rows=report.pop("events")
    event_rows=[]
    for x in rows:
        d=asdict(x)
        d.update({policy:accepts(x,policy) if
                  not(policy in TAPE_POLICIES and true_tape is None) else None
                  for policy in POLICIES})
        event_rows.append(d)
    _save_csv(args.output_dir/"MACD_RSI_SMA_spot_event_ledger_NOT_OPTIONS_PNL.csv",event_rows)
    h=hashlib.sha256()
    for file in source:
        h.update(file.name.encode())
        h.update(str(file.stat().st_size).encode())
    report["source_file_count"]=len(source)
    report["source_metadata_sha256"]=h.hexdigest()
    report["classification"]="CLOSE-PRICE RESEARCH; NO REAL OPTIONS FILLS; TAPE N/A WITHOUT ACTUAL EVENT RECORDS"
    (args.output_dir/"report.json").write_text(json.dumps(report,indent=2,sort_keys=True))
    print("INDICATOR TAPE COVERAGE: "+json.dumps({
        "archived_sessions":report["sessions"],"frames":report["frames"],
        "events":len(rows),"new_data_purchases_usd":0,
        "true_spy_tape_supplied":true_tape is not None,
        "folder":str(args.output_dir)},sort_keys=True),flush=True)
    for period,models in report["periods"].items():
        for name,stats in models.items():
            print("INDICATOR TAPE RESULT: "+json.dumps({
                "period":period,"policy":name,
                "status":stats["status"],"events":stats["baseline_indicator_eligible_events"],
                "selected":stats["signals_selected"],
                "correct_direction":stats["correct_spy_direction"],
                "signed_gt2bps":stats["correct_signed_gt2bp_spy"],
                "signed_gt5bps":stats["correct_signed_gt5bp_spy"],
                "mean_after_2bp_spot_proxy":stats["mean_signed_spy_bps_after_hypothetical_2bp"],
                "daily_net_spy_bps_including_no_trade":stats["mean_net_signed_spy_bps_per_day_including_no_signal"],
                "bootstrap95_day_bps":stats["bootstrap"].get("ci95_daily_signed_spy_bps")
                    if stats["bootstrap"] is not None else None,
                "actual_option_pnl":None,
            },sort_keys=True),flush=True)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
