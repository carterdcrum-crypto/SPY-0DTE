"""Point-in-time HIGH-BETA STOCK OPTIONS research mode — scanner & signals.

User's STRICT screen: optionable, time-of-day relative volume >1,
PRIOR-DAY 14-session ATR in DOLLARS >1, observed cumulative shares >1M,
last observed share price >$30, and prior-session-only 252d beta >1.5.

Stock minutes MUST be whole-universe pre-screen 1m bars, with causal
same-clock twenty-PREVIOUS-session cumulative/minute volume denominators.
Reference facts ATR and beta MUST be computed no later than previous session
and must be supplied from actual historical sources, not modern snapshots.
No stock candidates/data are fabricated from the existing SPY-only archive.

Signal families and MACD/RSI/SMA weight rules are imported unchanged from
the earlier SPY research; no lookahead/strategy tuning. Order timestamps
remain experimental until real option NBBO data arrives.
"""
from __future__ import annotations

import csv
import gzip
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, Sequence

from .data import HistoricalFrame
from .free_signal_screen import ET, PERIODS
from .market import MarketSnapshot
from .weighted_daily_coverage import Opportunity, session_opportunities


@dataclass(frozen=True)
class ScreenConfig:
    min_relative_volume: float = 1.0
    min_atr14_dollars: float = 1.0
    min_cumulative_shares: int = 1_000_000
    min_spot_dollars: float = 30.0
    min_beta252: float = 1.5


@dataclass(frozen=True)
class StockMinute:
    """All source fields are **known at bar completion**, no final-day totals."""
    timestamp: datetime  # start of one-minute OHLCV bar, timezone-aware
    symbol: str
    close: float
    minute_volume: int
    cumulative_volume: int  # observed cumulative shares at completed bar
    prior20_mean_cumvol_same_clock: float
    prior20_mean_minutevol_same_clock: float
    atr14_prev_session_dollars: float
    beta252_prev_session: float
    optionable_asof: bool
    metrics_last_session: date
    # Required metadata, not used in strategy selection:
    history_source: str

    def __post_init__(self)->None:
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("stock minute timestamp needs explicit timezone")
        if not self.symbol or not self.symbol.isascii() or not self.symbol.isupper():
            raise ValueError("stock symbol must be uppercase")
        if self.metrics_last_session>=self.timestamp.astimezone(ET).date():
            raise ValueError("ATR/beta/20d baseline cannot use current or future sessions")
        if any(not math.isfinite(v) for v in (
            self.close,self.prior20_mean_cumvol_same_clock,
            self.prior20_mean_minutevol_same_clock,self.atr14_prev_session_dollars,
            self.beta252_prev_session)):
            raise ValueError("nonfinite historical screening metric")
        if self.close<=0 or self.minute_volume<0 or self.cumulative_volume<self.minute_volume:
            raise ValueError("invalid minute/cumulative shares or share price")
        if (self.prior20_mean_cumvol_same_clock<=0
                or self.prior20_mean_minutevol_same_clock<=0
                or self.atr14_prev_session_dollars<0):
            raise ValueError("invalid point-in-time historical denominators")
        if not self.history_source.strip():
            raise ValueError("historical source provenance is required")

    @property
    def rvol_asof(self)->float:
        return self.cumulative_volume/self.prior20_mean_cumvol_same_clock

    @property
    def minute_rvol_asof(self)->float:
        return self.minute_volume/self.prior20_mean_minutevol_same_clock

    @property
    def available_at(self)->datetime:
        return self.timestamp+timedelta(minutes=1)


@dataclass(frozen=True)
class StockSignal:
    symbol: str
    underlying_spot_at_signal: float
    observed_rvol_asof: float
    observed_cumulative_volume: int
    observed_atr14_prev_session_dollars: float
    observed_beta252_prev_session: float
    opportunity: Opportunity


def screen(stock:StockMinute,cfg:ScreenConfig=ScreenConfig())->bool:
    return bool(
        stock.optionable_asof
        and stock.rvol_asof>cfg.min_relative_volume
        and stock.atr14_prev_session_dollars>cfg.min_atr14_dollars
        and stock.cumulative_volume>cfg.min_cumulative_shares
        and stock.close>cfg.min_spot_dollars
        and stock.beta252_prev_session>cfg.min_beta252
    )


def calc_prior_atr14(prior_ohlc:Sequence[tuple[float,float,float]],
                     *,length:int=14)->float:
    """Historical ATR using previous CLOSE to include overnight gaps.

    Requires at least 15 finished daily OHLC bars; all supplied days
    MUST predate target session. This function does not load today's bar.
    """
    if length<2 or len(prior_ohlc)<length+1:
        raise ValueError("ATR requires previous completed daily bars")
    records=prior_ohlc[-length-1:]
    trs=[]
    for idx in range(1,len(records)):
        high,low,close=records[idx]
        previous_close=records[idx-1][2]
        if not all(math.isfinite(v) and v>0
                   for v in (high,low,close,previous_close)) or high<low:
            raise ValueError("invalid historical OHLC for ATR")
        trs.append(max(high-low,abs(high-previous_close),abs(low-previous_close)))
    return sum(trs)/length


def calc_prior_beta252(stock_returns:Sequence[float],
                       benchmark_returns:Sequence[float],
                       *,lookback:int=252)->float:
    """Point-in-time beta from prior 252 matched daily total returns."""
    if lookback<3 or len(stock_returns)<lookback or len(benchmark_returns)<lookback:
        raise ValueError("beta needs matched completed daily returns")
    s=list(stock_returns[-lookback:])
    b=list(benchmark_returns[-lookback:])
    if not all(math.isfinite(x) for x in s+b):
        raise ValueError("nonfinite daily returns")
    ms=sum(s)/len(s)
    mb=sum(b)/len(b)
    cov=sum((a-ms)*(z-mb) for a,z in zip(s,b))
    variance=sum((z-mb)**2 for z in b)
    if variance<=0:
        raise ValueError("benchmark beta variance must be positive")
    return cov/variance


def _csv(path:Path):
    return gzip.open(path,"rt",newline="",encoding="utf8") if path.suffix==".gz" else path.open("r",newline="",encoding="utf8")


def read_stock_minutes(files:Sequence[Path])->tuple[StockMinute,...]:
    """Reject missing metrics/invalid chronology. No automatic downloading."""
    required={
        "ts_start","symbol","close","minute_volume","cumulative_volume",
        "prior20_mean_cumvol_same_clock","prior20_mean_minutevol_same_clock",
        "atr14_prev_session_dollars","beta252_prev_session",
        "optionable_asof","metrics_last_session","history_source",
    }
    rows=[]
    last={}
    for source in sorted(files):
        with _csv(source) as f:
            reader=csv.DictReader(f)
            if not reader.fieldnames or not required.issubset(reader.fieldnames):
                raise ValueError(f"{source}: missing point-in-time stock screening columns")
            for r in reader:
                valid=r["optionable_asof"].strip().lower()
                if valid not in ("true","false","1","0"):
                    raise ValueError("optionable_asof must be true/false")
                x=StockMinute(
                    timestamp=datetime.fromisoformat(r["ts_start"].replace("Z","+00:00")),
                    symbol=r["symbol"].strip(),
                    close=float(r["close"]),
                    minute_volume=int(r["minute_volume"]),
                    cumulative_volume=int(r["cumulative_volume"]),
                    prior20_mean_cumvol_same_clock=float(r["prior20_mean_cumvol_same_clock"]),
                    prior20_mean_minutevol_same_clock=float(r["prior20_mean_minutevol_same_clock"]),
                    atr14_prev_session_dollars=float(r["atr14_prev_session_dollars"]),
                    beta252_prev_session=float(r["beta252_prev_session"]),
                    optionable_asof=valid in ("true","1"),
                    metrics_last_session=date.fromisoformat(r["metrics_last_session"]),
                    history_source=r["history_source"],
                )
                day=x.timestamp.astimezone(ET).date()
                old=last.get(x.symbol)
                if old is not None and (x.timestamp<=old.timestamp or
                     (old.timestamp.astimezone(ET).date()==day and
                      x.cumulative_volume<old.cumulative_volume)):
                    raise ValueError("nonchronological/declining stock volume or repeated minute")
                last[x.symbol]=x
                rows.append(x)
    return tuple(rows)


def signals_by_symbol_day(bars:Sequence[StockMinute],
                          cfg:ScreenConfig=ScreenConfig(), *,
                          apply_high_beta_screen:bool=True)->tuple[StockSignal,...]:
    """Screen each candidate at its own signal minute, not day-end.

    SPY 0DTE comparison reuses the exact same indicator strategies
    but MUST bypass the stock beta>1.5 screen; set the switch only
    when processing SPY-only underlying minute bars.
    """
    grouped=defaultdict(list)
    for bar in bars:
        grouped[(bar.symbol,bar.timestamp.astimezone(ET).date())].append(bar)
    results=[]
    for (symbol,day),vals in sorted(grouped.items()):
        ordered=sorted(vals,key=lambda x:x.timestamp)
        minutes=[
            HistoricalFrame(
                timestamp=x.timestamp,
                market=MarketSnapshot(
                    spot=x.close,bid=x.close,ask=x.close,
                    realized_volatility=0.,implied_volatility=0.,
                    volume_ratio=x.minute_rvol_asof,
                    minutes_to_close=(
                        datetime.combine(day,datetime.min.time(),ET).replace(
                            hour=16,minute=0)-x.available_at).total_seconds()/60,
                ),
                options=(),
            ) for x in ordered
        ]
        lookup={x.available_at:x for x in ordered}
        for event in session_opportunities(minutes):
            minute=lookup[datetime.fromisoformat(event.signal_at)]
            if not apply_high_beta_screen or screen(minute,cfg):
                results.append(StockSignal(
                    symbol=symbol,underlying_spot_at_signal=minute.close,
                    observed_rvol_asof=minute.rvol_asof,
                    observed_cumulative_volume=minute.cumulative_volume,
                    observed_atr14_prev_session_dollars=minute.atr14_prev_session_dollars,
                    observed_beta252_prev_session=minute.beta252_prev_session,
                    opportunity=event,
                ))
    return tuple(sorted(results,key=lambda e:(e.opportunity.signal_at,e.symbol,e.opportunity.setup)))


def screening_audit(bars:Sequence[StockMinute],
                    cfg:ScreenConfig=ScreenConfig())->dict:
    grouped=defaultdict(list)
    for x in bars:
        grouped[x.timestamp.astimezone(ET).date()].append(x)
    coverage={}
    for day,observed in sorted(grouped.items()):
        eligibility={x.symbol for x in observed if screen(x,cfg)}
        coverage[day.isoformat()]={
            "symbols_observed_any_time":len({x.symbol for x in observed}),
            "symbols_ever_passed_AS_OF_that_minute":len(eligibility),
            "symbols_passed":sorted(eligibility),
        }
    return {
        "thresholds_STRICT":{
            "optionable_asof":True,
            "relative_volume_tod_cumulative_gt":1.,
            "atr14_PREVIOUS_session_DOLLARS_gt":1.,
            "current_cumulative_shares_gt":1_000_000,
            "underlying_spot_gt_usd":30.,
            "signed_beta252_PREVIOUS_session_gt":1.5,
        },
        "observed_trading_sessions":len(coverage),
        "observed_stock_symbols":sorted({x.symbol for x in bars}),
        "coverage_by_day":coverage,
        "warning":"Input must include entire contemporaneous optionable stock UNIVERSE, not only later-known movers; vendor point-in-time provenance is required.",
    }
