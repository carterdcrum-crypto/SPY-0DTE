"""Research-only implementable SPY 0DTE intraday simulator.

Uses *completed* SPY OHLCV bars for signals and actual event-observed option
bid/ask for option fills. Order time and next-quote latency are explicit.
Never imports a live broker, HTTP executor, or a paper trader.
"""
from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta, time
from typing import Literal, Sequence

from .session_calendar import session_close
from .weekly_options_data import NY, Bar, Quote, SessionData

StrategyName = Literal["orb_simple", "orb_filtered", "vwap_reclaim"]


@dataclass(frozen=True)
class ExperimentConfig:
    strategy: StrategyName = "orb_simple"
    risk_fraction: float = 0.01
    daily_loss_fraction: float = .05
    profit_take_fraction: float = .65
    stop_premium_fraction: float = .35
    max_hold_minutes: int = 20
    broker_cutoff_minutes: int = 15  # 15:45 ET standard, 12:45 on NYSE early close
    latency_seconds: int = 2
    max_fill_wait_seconds: int = 90
    max_quote_age_seconds: int = 75
    fee_per_contract_side: float = .68  # commission + regulatory estimate
    adverse_option_ticks: int = 1
    option_tick_size: float = .01
    min_abs_delta: float = .40
    max_abs_delta: float = .60
    max_spread_fraction: float = .15
    minimum_open_interest: int = 25
    minimum_bid_size: int = 1
    minimum_ask_size: int = 1
    min_rvol: float = 1.25

    def __post_init__(self) -> None:
        if self.strategy not in ("orb_simple","orb_filtered","vwap_reclaim"):
            raise ValueError("unsupported strategy")
        if not 0 < self.risk_fraction <= 1:
            raise ValueError("premium exposure must be in (0,1]")
        if not 0 < self.daily_loss_fraction < 1:
            raise ValueError("daily loss trigger must be in (0,1)")
        if not 0 < self.stop_premium_fraction < 1 or self.profit_take_fraction <= 0:
            raise ValueError("invalid exit thresholds")
        if min(self.max_hold_minutes,self.broker_cutoff_minutes,self.max_fill_wait_seconds,
               self.max_quote_age_seconds) < 1 or self.latency_seconds < 0:
            raise ValueError("invalid durations")
        if self.fee_per_contract_side < 0 or self.adverse_option_ticks < 0 or self.option_tick_size < 0:
            raise ValueError("invalid execution costs")


@dataclass(frozen=True)
class LedgerTrade:
    symbol: str
    side: str
    contracts: int
    entry_at: str
    exit_at: str
    entry_ask_fill: float
    exit_bid_fill: float
    entry_debit: float
    exit_credit: float
    total_fees: float
    realized_pnl: float
    option_premium_return: float
    entry_signal: str
    exit_reason: str
    entry_quote_at: str
    exit_quote_at: str | None


@dataclass(frozen=True)
class DailyEquity:
    day: str
    settled_cash: float
    unsettled_proceeds: float
    realized_pnl_cumulative: float
    unrealized_pnl: float
    fees_paid_cumulative: float
    equity: float
    completed_trades: int
    order_attempts: int
    fills: int
    equity_reconciles: bool


@dataclass(frozen=True)
class EquityMark:
    at: str
    equity: float
    settled_cash: float
    unsettled_cash: float
    realized_pnl: float
    unrealized_pnl: float
    fees_paid: float
    stale_option_mark: bool
    reconciles: bool


@dataclass(frozen=True)
class BacktestReport:
    configuration: ExperimentConfig
    starting_cash: float
    ending_equity: float
    total_return: float
    max_drawdown: float
    orders_attempted: int
    order_fills: int
    completed_trades: int
    canceled_orders: int
    missing_quote_marks: int
    stale_quote_marks: int
    forced_worthless_exits: int
    fees_paid: float
    profit_factor: float
    exposure_fraction: float
    trade_ledger: tuple[LedgerTrade,...]
    daily_equity: tuple[DailyEquity,...]
    intraday_marks: tuple[EquityMark,...]
    intraday_max_drawdown: float


@dataclass
class _Position:
    quote: Quote
    contracts: int
    fill: float
    debit: float
    entry_at: datetime
    signal: str
    last_bid: float
    last_quote_at: datetime


@dataclass
class _Order:
    kind: Literal["buy","sell"]
    symbol: str
    placed_at: datetime
    ready_at: datetime
    expires_at: datetime
    max_debit: float = 0
    contracts: int = 0
    signal: str = ""


def _next_settlement(day: date) -> date:
    # T+1 exchange trading day, excluding verified holidays.
    cursor = day+timedelta(days=1)
    for _ in range(15):
        if session_close(cursor) is not None:
            return cursor
        cursor += timedelta(days=1)
    raise ValueError("settlement date unavailable")


def _vwap_proxy(bars: Sequence[Bar]) -> float:
    # Approximate volume-weighted typical bar price; this is NOT true
    # transaction-level VWAP. Never label it exact VWAP.
    denom = sum(b.volume for b in bars)
    return sum(b.typical*b.volume for b in bars)/denom if denom > 0 else math.nan


def _opening_range(bars: Sequence[Bar], day: date) -> tuple[float,float] | None:
    expected = {
        datetime.combine(day,time(9,30),NY)+timedelta(minutes=i)
        for i in range(15)
    }
    observed={b.start.astimezone(NY) for b in bars if b.start.astimezone(NY) in expected}
    if len(observed)!=15 or any(b.volume <= 0 for b in bars if b.start.astimezone(NY) in expected):
        return None
    included=[b for b in bars if b.start.astimezone(NY) in expected]
    return max(b.high for b in included),min(b.low for b in included)


def _candidate(
    strategy:StrategyName,history:Sequence[Bar],ranges:tuple[float,float]|None,
    vwap:float,prior_vwap:float,
    prior_minutes:dict[int,list[int]],min_rvol:float,
) -> tuple[str,str]|None:
    if ranges is None or len(history)<16:
        return None
    bar=history[-1];prior=history[-2]
    if bar.available_at.astimezone(NY).time()<=time(9,45):
        return None
    high,low=ranges
    if strategy in ("orb_simple","orb_filtered"):
        side="call" if bar.close>high and prior.close<=high else "put" if bar.close<low and prior.close>=low else ""
        if not side:
            return None
        if strategy=="orb_filtered":
            minute=bar.start.astimezone(NY).hour*60+bar.start.astimezone(NY).minute
            vols=prior_minutes.get(minute,[])
            # Prior days only; today's or future-day volume may not enter denominator.
            if len(vols)<3 or sum(vols)<=0:
                return None
            rvol=bar.volume/(sum(vols)/len(vols))
            if rvol<min_rvol or not math.isfinite(vwap):
                return None
            if (side=="call" and bar.close<=vwap) or (side=="put" and bar.close>=vwap):
                return None
        return side, strategy
    if not (math.isfinite(vwap) and math.isfinite(prior_vwap) and len(history)>=21):
        return None
    # Price has established directional trend since the opening range; the
    # *completed* previous bar touches/probes the bar-VWAP proxy, and current
    # completed bar reclaims it. Require directional 5-minute slope.
    if (bar.close>high and bar.close>vwap and
        prior.low<=prior_vwap and prior.close<=prior_vwap and
        bar.close>bar.open and bar.close>history[-6].close):
        return "call","vwap_reclaim"
    if (bar.close<low and bar.close<vwap and
        prior.high>=prior_vwap and prior.close>=prior_vwap and
        bar.close<bar.open and bar.close<history[-6].close):
        return "put","vwap_reclaim"
    return None


def _eligible_quote(q:Quote,as_of:datetime,side:str,cfg:ExperimentConfig)->bool:
    if q.expiry!=as_of.astimezone(NY).date() or q.right!=side:
        return False
    if not 0 <= (as_of-q.observed_at).total_seconds()<=cfg.max_quote_age_seconds:
        return False
    if not (cfg.min_abs_delta<=abs(q.delta)<=cfg.max_abs_delta):
        return False
    if not (q.bid>0 and q.ask>q.bid and q.ask_size>=cfg.minimum_ask_size and q.bid_size>=cfg.minimum_bid_size):
        return False
    if q.open_interest<cfg.minimum_open_interest:
        return False
    return (q.ask-q.bid)/((q.ask+q.bid)/2)<=cfg.max_spread_fraction


def _select_quote(last:dict[str,Quote],as_of:datetime,side:str,cfg:ExperimentConfig,spot:float)->Quote|None:
    eligible=[q for q in last.values() if _eligible_quote(q,as_of,side,cfg)]
    return min(eligible,key=lambda q:(
        abs(abs(q.delta)-.50),(q.ask-q.bid)/q.ask,
        abs(q.strike-spot),q.symbol,
    )) if eligible else None


def run_simulation(sessions:Sequence[SessionData],*,starting_cash:float,cfg:ExperimentConfig,
                   prior_volume:dict[int,list[int]]|None=None)->BacktestReport:
    if starting_cash<=0:
        raise ValueError("starting cash must be positive")
    if not sessions or any(a.day>=b.day for a,b in zip(sessions,sessions[1:])):
        raise ValueError("require chronological nonempty unique sessions")
    settled=float(starting_cash)
    outstanding:list[tuple[date,float]]=[]
    realized=0.0
    fees=0.0
    position:_Position|None=None
    pending:_Order|None=None
    ledger:list[LedgerTrade]=[]
    daily:list[DailyEquity]=[]
    marks:list[EquityMark]=[]
    orders=fills=cancels=missing=stale=worthless=0
    observed_open_minutes=0
    possible_open_minutes=0
    # Accumulate *prior-day* minute volumes, retaining at most five days.
    volume_history:dict[int,deque[int]]=defaultdict(lambda:deque(maxlen=5))
    if prior_volume:
        for minute,vals in prior_volume.items():
            volume_history[minute].extend(vals[-5:])

    def cash_equity() -> float:
        return settled+sum(value for _,value in outstanding)

    def mark_at(now:datetime) -> None:
        nonlocal stale
        underlying=cash_equity()
        value=0.0
        unrealized=0.0
        ambiguous=False
        if position is not None:
            if (now-position.last_quote_at).total_seconds()>cfg.max_quote_age_seconds:
                stale+=1
                ambiguous=True
            else:
                # Executable bid-based liquidation mark; no look-ahead.
                possible_price=max(0.0,position.last_bid-cfg.adverse_option_ticks*cfg.option_tick_size)
                value=max(0.0,possible_price*position.contracts*100-
                          cfg.fee_per_contract_side*position.contracts)
            unrealized=value-position.debit
        equity=underlying+value
        good=abs(equity-(starting_cash+realized+unrealized))<1e-5
        if not good:
            raise AssertionError("mark-to-market account equity does not reconcile")
        marks.append(EquityMark(now.isoformat(),equity,settled,
                                sum(v for _,v in outstanding),realized,
                                unrealized,fees,ambiguous,True))

    def close_position(at:datetime,fill:float,reason:str,quote_at:datetime|None):
        nonlocal position,realized,fees,settled,worthless
        assert position is not None
        q=position
        # A real sale is credited gross, while the exit fee is separately
        # debited from settled cash. On an unfilled expiry/zero-recovery path
        # there was no sale and there is no fictional exit commission.
        gross_proceeds=round(fill*100*q.contracts,8)
        exit_fee=cfg.fee_per_contract_side*q.contracts if quote_at is not None else 0.0
        proceeds=gross_proceeds-exit_fee
        settled-=exit_fee
        fees+=exit_fee
        pnl=proceeds-q.debit
        realized+=pnl
        outstanding.append((_next_settlement(at.astimezone(NY).date()),gross_proceeds))
        premium_return=(fill/q.fill-1) if q.fill>0 else -1
        ledger.append(LedgerTrade(
            symbol=q.quote.symbol,side=q.quote.right,contracts=q.contracts,
            entry_at=q.entry_at.isoformat(),exit_at=at.isoformat(),
            entry_ask_fill=q.fill,exit_bid_fill=fill,
            entry_debit=q.debit,exit_credit=proceeds,
            total_fees=cfg.fee_per_contract_side*q.contracts+exit_fee,
            realized_pnl=pnl,option_premium_return=premium_return,
            entry_signal=q.signal,exit_reason=reason,
            entry_quote_at=q.quote.observed_at.isoformat(),
            exit_quote_at=quote_at.isoformat() if quote_at else None,
        ))
        if quote_at is None:
            worthless+=1
        position=None

    for session in sessions:
        day=session.day
        matured=sum(v for d,v in outstanding if d<=day)
        settled+=matured
        outstanding=[(d,v) for d,v in outstanding if d>day]
        assert position is None and pending is None
        day_open_equity=cash_equity()
        latest:dict[str,Quote]={}
        seen_bars:list[Bar]=[]
        prev_vwap=math.nan
        or_range:tuple[float,float]|None=None
        cutoff=session.close_at-timedelta(minutes=cfg.broker_cutoff_minutes)
        events=[(q.observed_at,0,q.symbol,q) for q in session.quotes if q.observed_at<=cutoff]
        events += [(b.available_at,1,"",b) for b in session.bars if b.available_at<=cutoff]
        events.sort(key=lambda x:(x[0],x[1],x[2]))
        # The first 15 completed bars fix true bar highs/lows. If any are
        # absent, refuse ALL trading today rather than using partial OR.
        expected=[b for b in session.bars if
                  time(9,30)<=b.start.astimezone(NY).time()<time(9,45)]
        or_range=_opening_range(expected,day)
        last_now:datetime|None=None
        for now,kind,_,event in events:
            last_now=now
            if pending is not None and now>pending.expires_at:
                pending=None
                cancels+=1
            if kind==0:
                q:Quote=event
                latest[q.symbol]=q
                if position and q.symbol==position.quote.symbol:
                    position.last_bid=q.bid
                    position.last_quote_at=now
                if pending and pending.symbol==q.symbol and now>=pending.ready_at:
                    if pending.kind=="buy":
                        price=q.ask+cfg.adverse_option_ticks*cfg.option_tick_size
                        debit=price*100*pending.contracts+cfg.fee_per_contract_side*pending.contracts
                        # Verify AGAIN at the executable quote, and reject
                        # cross-spread, unreasonable size, or stale order.
                        if (_eligible_quote(q,now,q.right,cfg) and
                            debit<=pending.max_debit+1e-9 and debit<=settled+1e-9 and
                            q.ask_size>=pending.contracts):
                            settled-=debit
                            fees+=cfg.fee_per_contract_side*pending.contracts
                            position=_Position(q,pending.contracts,price,debit,now,
                                               pending.signal,q.bid,now)
                            fills+=1
                        else:
                            cancels+=1
                        pending=None
                    elif pending.kind=="sell" and position:
                        price=max(0.0,q.bid-cfg.adverse_option_ticks*cfg.option_tick_size)
                        close_position(now,price,pending.signal,now)
                        fills+=1
                        pending=None
                if position and pending is None and q.symbol==position.quote.symbol:
                    held=(now-position.entry_at).total_seconds()/60
                    if q.bid<=position.fill*(1-cfg.stop_premium_fraction):
                        reason="observed_option_bid_stop"
                    elif q.bid>=position.fill*(1+cfg.profit_take_fraction):
                        reason="observed_option_bid_target"
                    elif held>=cfg.max_hold_minutes or now>=cutoff-timedelta(seconds=90):
                        reason="time_or_cutoff_exit"
                    else:
                        reason=""
                    if reason:
                        pending=_Order("sell",q.symbol,now,
                                       now+timedelta(seconds=cfg.latency_seconds),
                                       cutoff,contracts=position.contracts,signal=reason)
                        orders+=1
            else:
                b:Bar=event
                if seen_bars:
                    prev_vwap=_vwap_proxy(seen_bars)
                seen_bars.append(b)
                vwap=_vwap_proxy(seen_bars)
                if position is None and pending is None and now<cutoff-timedelta(minutes=3):
                    if cash_equity()>=day_open_equity*(1-cfg.daily_loss_fraction):
                        signal=_candidate(cfg.strategy,seen_bars,or_range,vwap,prev_vwap,
                            {k:list(v) for k,v in volume_history.items()},cfg.min_rvol)
                        if signal:
                            side,reason=signal
                            chosen=_select_quote(latest,now,side,cfg,b.close)
                            if chosen:
                                max_debit=min(cfg.risk_fraction*cash_equity(),settled)
                                expected_cost=(chosen.ask+cfg.adverse_option_ticks*cfg.option_tick_size)*100+cfg.fee_per_contract_side
                                count=int(max_debit//expected_cost)
                                count=min(count,chosen.ask_size)
                                if count>=1:
                                    pending=_Order(
                                        "buy",chosen.symbol,now,
                                        now+timedelta(seconds=cfg.latency_seconds),
                                        min(now+timedelta(seconds=cfg.max_fill_wait_seconds),cutoff),
                                        max_debit=max_debit,contracts=count,signal=reason,
                                    )
                                    orders+=1
            # Equity marks never use future quotes.
            mark_at(now)
        if pending is not None:
            pending=None
            cancels+=1
        # If no *subsequent* executable quote appears before cutoff, an
        # emergency exit cannot be proven. Treat the 0DTE premium as lost.
        # This is deliberately conservative, not a fabricated bid execution.
        if position is not None:
            missing+=1
            close_position(cutoff,0.0,"no_executable_close_quote_zero_recovery",None)
        mark_at(cutoff)
        equity=cash_equity()
        # No position is carried beyond each session.
        expected=starting_cash+realized
        reconciles=abs(equity-expected)<1e-5
        if not reconciles:
            raise AssertionError(f"cash/receivables/realized P&L do not reconcile on {day}")
        daily.append(DailyEquity(
            day.isoformat(),settled,sum(x for _,x in outstanding),realized,0.0,
            fees,equity,len(ledger),orders,fills,True,
        ))
        for b in session.bars:
            minute=b.start.astimezone(NY).hour*60+b.start.astimezone(NY).minute
            if b.volume>=0:
                volume_history[minute].append(b.volume)
    # End point includes next-day-settling proceeds: these are *receivables*,
    # not available cash until the next verified exchange trading day.
    equity=daily[-1].equity
    peak=starting_cash
    dd=0.0
    for v in daily:
        peak=max(peak,v.equity)
        dd=max(dd,1-v.equity/peak)
    peak_intraday=starting_cash
    intraday_dd=0.0
    for v in marks:
        peak_intraday=max(peak_intraday,v.equity)
        intraday_dd=max(intraday_dd,1-v.equity/peak_intraday)
    gains=sum(t.realized_pnl for t in ledger if t.realized_pnl>0)
    losses=-sum(t.realized_pnl for t in ledger if t.realized_pnl<0)
    pf=gains/losses if losses>0 else (math.inf if gains else 0.0)
    return BacktestReport(
        cfg,starting_cash,equity,equity/starting_cash-1,dd,orders,fills,
        len(ledger),cancels,missing,stale,worthless,fees,
        pf,(sum((datetime.fromisoformat(t.exit_at)-datetime.fromisoformat(t.entry_at)).total_seconds()
                for t in ledger)/(len(sessions)*6.5*3600)) if sessions else 0.0,
        tuple(ledger),tuple(daily),tuple(marks),intraday_dd,
    )
