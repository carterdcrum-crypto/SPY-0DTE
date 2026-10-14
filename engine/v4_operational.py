"""V4 entry-quality-3x operational adapter shared by PAPER and guarded LIVE.

Unlike the archived CBBO one-minute proxy, this module REQUIRES completed
real SPY OHLCV minute bars and fresh timestamped 0DTE option bid/ask quotes.
Missing market-data entitlements, old or delayed options, missing volume,
greeks or depth -> no entry. No invented substitute observations.

V4 *entry setup*, 6-point quality rubric, 1/2/3 quantity choice, and
conditional hold exits are reused; production snapshots are not OPRA event
NBBO, so this is a separately identified, unvalidated live execution adapter.
"""
from __future__ import annotations

import math
import statistics
from datetime import date, datetime, timedelta, time, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from .regime_reclaim_frozen_signal import identify_setups
from .regime_hold_conditional import (
    EXTEND_MIN_RETURN, EXTEND_MIN_RVOL, EXTEND_MIN_SPY_MOMENTUM_BPS,
    EXTEND_MIN_PREMIUM_PEAK_FRACTION, MAX_QUOTE_SPREAD,
)
from .regime_reclaim_frozen_signal import ema
from .v4_quality_sizing import score_at_entry, size as quality_size
from .weekly_options_data import Bar
from .session_calendar import session_close

from threading import Lock

_SPY_CHART_LOCK=Lock()
_SPY_CHART={"candles":[],"source":"Webull OpenAPI · SPY M1",
            "reason":"Waiting for authenticated SPY 1-minute market data",
            "observed_at":None}


def spy_chart_30m(now:datetime|None=None)->dict:
    """A 30-minute rolling view, including a broker-reported *forming* 1m bar.

    This display-only cache never enters the quant model. Price updates are
    pushed every few seconds if Webull actually returns an unclosed candle.
    A completed candle remains immutable until its close. Missing/stale
    provider observations must not be presented as live or synthesized.
    """
    clock=now or datetime.now(timezone.utc)
    with _SPY_CHART_LOCK:
        state={**_SPY_CHART,"candles":[dict(x) for x in _SPY_CHART["candles"]]}
    rows=state["candles"]
    if not rows:
        return {**state,"state":"UNAVAILABLE","window_minutes":30,"interval":"1m",
                "asof":None,"age_seconds":None,"has_forming_candle":False}
    forming=bool(rows[-1].get("forming"))
    observed=state.get("observed_at")
    if observed:
        snapshot_age=max(0.0,(clock-datetime.fromisoformat(observed)).total_seconds())
    else:
        snapshot_age=float("inf")
    last_bar_end=datetime.fromisoformat(rows[-1]["end"])
    market_age=max(0.0,(clock-last_bar_end).total_seconds())
    # An unfinished bar is visibly LIVE only while an up-to-date response is
    # being observed. No arrival-time stamping can resurrect an OLD bar.
    market_is_current=forming and snapshot_age<=15 and market_age<=60
    is_recent_closed=not forming and market_age<=90
    status=("LIVE_FORMING" if market_is_current else
            "WAITING_FOR_NEXT_BAR" if is_recent_closed else "STALE")
    return {**state,"state":status,"window_minutes":30,"interval":"1m",
            "asof":observed if forming else last_bar_end.isoformat(),
            "age_seconds":round(snapshot_age if forming else market_age,1),
            "has_forming_candle":forming and market_is_current}


def spy_chart_5m(now:datetime|None=None)->dict:
    """Compatibility endpoint for earlier installed Android APKs."""
    result=spy_chart_30m(now)
    result["candles"]=result["candles"][-5:]
    result["window_minutes"]=5
    return result


def _store_spy_chart(bars:list[Bar],now:datetime|None=None,reason:str|None=None):
    """Stock OHLCV from Webull only; unfinished latest minute is flagged."""
    clock=now or datetime.now(timezone.utc)
    window=bars[-30:]
    # Preserve only contiguous bars from the end (e.g., RTH session start).
    contiguous=[]
    for b in reversed(window):
        if contiguous and (contiguous[-1].start-b.start).total_seconds()!=60:
            break
        contiguous.append(b)
    contiguous.reverse()
    observations=[]
    for b in contiguous:
        forming=b.start+timedelta(minutes=1)>clock-timedelta(seconds=2)
        observations.append({"time":b.start.isoformat(),
             "end":(b.start+timedelta(minutes=1)).isoformat(),
             "open":b.open,"high":b.high,"low":b.low,"close":b.close,
             "volume":b.volume,"forming":forming})
    with _SPY_CHART_LOCK:
        if observations:
            _SPY_CHART["candles"]=observations
            _SPY_CHART["observed_at"]=clock.isoformat()
            _SPY_CHART["reason"]=None
        elif not _SPY_CHART["candles"]:
            _SPY_CHART["reason"]=reason or "Webull has no current authenticated SPY OHLCV"

ET=ZoneInfo("America/New_York")
IDENTIFIER="v4_original_3x"
FEE=0.65
SLIPPAGE_PER_SHARE=.01
MAX_SOURCE_DELAY_SECONDS=3
MAX_SPREAD=.035
MAX_IV_RV=1.5
MAX_CONTRACTS=3


def _time(raw):
    if isinstance(raw,datetime):
        t=raw
    elif isinstance(raw,(int,float)):
        t=datetime.fromtimestamp(raw/(1000 if abs(raw)>1e11 else 1),timezone.utc)
    else:
        value=str(raw).strip().replace("Z","+00:00")
        t=datetime.fromisoformat(value)
    if t.tzinfo is None:
        raise ValueError("bar timestamps must include timezone")
    return t.astimezone(timezone.utc)


def parse_webull_bars(payload, now:datetime,*,include_forming:bool=False)->list[Bar]:
    """Strictly parse authenticated RTH M1 OHLCV without synthetic ticks.

    Webull bars are timestamped with a period start (examples sometimes carry
    nonzero seconds). Coerce within a minute to that period and reject
    duplicates/out-of-session/future or untrusted incomplete observations.
    """
    if now.tzinfo is None:
        raise ValueError("aware clock required")
    if hasattr(payload,"status_code") and payload.status_code != 200:
        raise ValueError("Webull SPY M1 market data denied/unavailable")
    if hasattr(payload,"json"):
        payload=payload.json()
    if isinstance(payload,dict) and isinstance(payload.get("data"),dict):
        payload=payload["data"]
    if not isinstance(payload,dict):
        raise ValueError("Webull M1 response not structured")
    rows=payload.get("result")
    if not isinstance(rows,list):
        raise ValueError("Webull M1 bars missing result array")
    # Official Webull stock-history response wraps individual symbol bars:
    # {"result":[{"symbol":"SPY","result":[...completed bars...]}]}.
    # Also tolerate the SDK's flattened documented one-symbol representation.
    if rows and isinstance(rows[0],dict) and isinstance(rows[0].get("result"),list):
        if len(rows)!=1 or rows[0].get("symbol")!="SPY":
            raise ValueError("Webull M1 feed must contain SPY and only SPY")
        rows=rows[0]["result"]
    elif payload.get("symbol") not in (None,"SPY"):
        raise ValueError("underlying is not SPY")
    today=now.astimezone(ET).date()
    checked=[]
    for row in rows:
        if not isinstance(row,dict) or row.get("trading_sessions","RTH")!="RTH":
            continue
        at=_time(row["time"])
        start=at.replace(second=0,microsecond=0)
        local=start.astimezone(ET)
        if local.date()!=today or not time(9,30)<=local.time()<time(16,0):
            continue
        if start>now.astimezone(timezone.utc):
            continue
        if (not include_forming and
                start+timedelta(minutes=1)>now.astimezone(timezone.utc)-timedelta(seconds=2)):
            continue
        values=[float(row[k]) for k in ("open","high","low","close")]
        volume=int(float(row["volume"]))
        if not all(math.isfinite(v) and v>0 for v in values) or volume<=0:
            raise ValueError("SPY completed bar has invalid OHLCV")
        o,h,l,c=values
        if h<max(o,c,l) or l>min(o,c,h):
            raise ValueError("SPY completed bar high/low inconsistent")
        checked.append(Bar(start,o,h,l,c,volume))
    dedup={}
    for bar in checked:
        if bar.start in dedup:
            previous=dedup[bar.start]
            if bar!=previous:
                raise ValueError("conflicting SPY minute bar")
        dedup[bar.start]=bar
    return [dedup[t] for t in sorted(dedup)]


def last_spy_bar_is_fresh(bars:list[Bar],now:datetime)->bool:
    return bool(bars and 0<=(now-bars[-1].available_at).total_seconds()<=90)


def _underlying_frame(bars:list[Bar],i:int):
    if i<20:
        return SimpleNamespace(timestamp=bars[i].start,
            market=SimpleNamespace(spot=bars[i].close,volume_ratio=0.0))
    volume=[b.volume for b in bars[i-20:i]]
    if min(volume)<=0:
        return None
    ratio=bars[i].volume/statistics.fmean(volume)
    return SimpleNamespace(timestamp=bars[i].start,
        market=SimpleNamespace(spot=bars[i].close,volume_ratio=ratio))


def _rv(bars:list[Bar],last:int)->float:
    if last<30:
        return 0.
    values=[b.close for b in bars[last-30:last+1]]
    ratios=[math.log(x/y) for y,x in zip(values,values[1:])]
    return statistics.stdev(ratios)*math.sqrt(252*390)


def candidate(now:datetime,bars:list[Bar],quotes,*,equity:float,settled:float,
              peak:float,max_contracts:int=3,exposure:float=.35):
    """Return (order proposal or None, diagnostic) with causal sizing.

    *Only* evaluate fresh selected option quotes in the 1–2min window
    FOLLOWING a completed SPY signal; quotes must have time, sizes, greeks.
    """
    if now.tzinfo is None:
        raise ValueError("aware now required")
    if not last_spy_bar_is_fresh(bars,now):
        return None,{"state":"WAITING_V4_BARS","reason":"SPY completed M1 OHLCV not fresh/available"}
    if len(bars)<55:
        return None,{"state":"WAITING_V4_BARS","reason":"at least 55 valid completed SPY M1 OHLCV bars required"}
    if any(b.volume<=0 for b in bars[-55:]):
        return None,{"state":"WAITING_V4_BARS","reason":"missing SPY M1 volume observation"}
    if any((b.start-a.start).total_seconds()!=60 for a,b in zip(bars[-55:],bars[-54:])):
        return None,{"state":"WAITING_V4_BARS","reason":"non-contiguous SPY completed M1 bars"}
    if equity<=0 or settled<=0 or peak<=0:
        return None,{"state":"RISK_BLOCKED","reason":"insufficient account capital"}
    if not quotes:
        return None,{"state":"WAITING_V4_QUOTES","reason":"no timestamped fresh SPY 0DTE production options"}
    close=session_close(now.astimezone(ET).date())
    if close is None or now.astimezone(ET).time()<time(9,52):
        return None,{"state":"NO_SIGNAL","reason":"outside frozen V4 entry window"}
    end=datetime.combine(now.astimezone(ET).date(),close,ET)
    if (end-now.astimezone(ET)).total_seconds()<20*60:
        return None,{"state":"ENTRY_WINDOW_CLOSED","reason":"do not enter 0DTE near expiry"}

    frames=[_underlying_frame(bars,i) for i in range(len(bars))]
    # Identify_setups reads a contiguous 35-bar trailing window.
    signals=identify_setups(frames)
    possible=[]
    for signal in signals:
        if signal["setup"] not in ("trend_reclaim","stretch_reclaim"):
            continue
        if not (signal["at"]+timedelta(minutes=1) <= now
                <= signal["at"]+timedelta(minutes=2)):
            continue
        i=next((n for n,b in enumerate(bars) if b.available_at==signal["at"]),None)
        if i is None:continue
        rv=_rv(bars,i)
        if rv<=0:continue
        for q in quotes:
            try:
                # Both paper collector records and live provider options supply
                # OPTION market timestamps, no receive-time-only relaxation.
                if q.source not in ("webull_production","tradier_production_realtime"):
                    continue
                if q.timestamp_quality!="option_quote_timestamp":
                    continue
                lag=(now-q.timestamp).total_seconds()
                if not 0<=lag<=MAX_SOURCE_DELAY_SECONDS:
                    continue
                if (now-q.received_at).total_seconds()>MAX_SOURCE_DELAY_SECONDS:
                    continue
                if q.underlying_timestamp is None or not 0<=(now-q.underlying_timestamp).total_seconds()<=MAX_SOURCE_DELAY_SECONDS:
                    continue
                if q.expiration!=now.astimezone(ET).date() or q.right!=signal["side"]:
                    continue
                if q.greeks is None:continue
                delta=float(q.greeks.delta)
                iv=float(q.greeks.implied_volatility)
                if not all(math.isfinite(v) for v in (delta,iv,q.ask,q.bid)):
                    continue
                if not .40<=abs(delta)<=.60 or not q.ask>q.bid>0:
                    continue
                if not q.bid_size or not q.ask_size:
                    continue
                spread=(q.ask-q.bid)/((q.ask+q.bid)/2)
                if spread>MAX_SPREAD or iv<=0 or iv/rv>MAX_IV_RV:
                    continue
                scoring_quote=SimpleNamespace(spread_fraction=spread,
                    delta=delta,implied_volatility=iv)
                quality=score_at_entry({"signal":signal,
                    "quote":scoring_quote,"entry_idx":0},
                    [SimpleNamespace(market=SimpleNamespace(realized_volatility=rv))])
                debit=round((q.ask+SLIPPAGE_PER_SHARE)*100+FEE,2)
                # This is a long option. Even with a modeled stop, the full
                # debit of EVERY contract is genuinely at risk.
                qty=quality_size("v4_quality_3x",score=quality,
                    equity=equity,settled=settled,peak=peak,debit=debit)
                hard=int((min(settled,equity*min(.35,exposure))+1e-8)//debit)
                qty=min(qty,MAX_CONTRACTS,max(0,int(max_contracts)),
                        int(q.bid_size),int(q.ask_size),hard)
                if qty<=0:
                    continue
                # Preserve the original $5k V4 signal / contract choice:
                # raw frozen trend/stretch, then nearest |delta| to .50.
                # Spread/IV/depth remain pre-trade guards, NOT a new regime.
                possible.append((abs(abs(delta)-.50),spread,q.option_symbol,
                    signal,q,quality,qty,debit,rv))
            except (ValueError,TypeError,ZeroDivisionError,AttributeError,OverflowError):
                continue
    if not possible:
        return None,{"state":"NO_SIGNAL","reason":"no as-of V4 entry/IV/liquidity/affordability-qualified 0DTE"}
    _,_,_,sig,q,quality,qty,debit,rv=min(possible,
         key=lambda x:(x[3]["at"],x[0],x[1],x[2]))
    return {
        "symbol":q.option_symbol,"quantity":qty,
        "quality_score":quality,"signal_at":sig["at"].isoformat(),
        "reason":f"{IDENTIFIER}; setup={sig['setup']}; quality={quality}/6; qty={qty}; observed_m1_webull",
        "cycle":sig["at"].isoformat(),"debit_per_contract":debit,
        "underlying_spot":q.underlying_price,
        "side":sig["side"],"rv":rv,
    },{"state":"SIGNAL","reason":"Original V4 frozen trend/stretch entry quality 3x candidate",
        "strategy":IDENTIFIER,"quality_score":quality,
        "contracts":qty,"signal":sig,"account_equity":equity}


def exit_reason(position, quote, bars:list[Bar], now:datetime, *, fee:float=FEE):
    """V4 full-position stop / 30% trail / 10-minute conditional extension.

    Never uses a quote to decide an exit if that quote's observation is stale.
    Liveness-independent session-close / disable liquidation handled by caller.
    """
    if quote is None:
        return None
    if not 0<=(now-quote.timestamp).total_seconds()<=MAX_SOURCE_DELAY_SECONDS:
        return None
    opened=position["opened_at"]
    opened=datetime.fromisoformat(opened) if isinstance(opened,str) else opened
    if opened.tzinfo is None:
        return "invalid_entry_clock_flatten"
    cost=float(position.get("average_cost",position.get("average_price",0.)*100+fee))
    peak=float(position.get("peak_exit_value",cost))
    if cost<=0:
        return "invalid_position_cost_flatten"
    credit=max(0.,(quote.bid-SLIPPAGE_PER_SHARE)*100-fee)
    if credit<=cost*.65:
        return "v4_hard_35pct"
    if credit<=max(peak,cost)*.70:
        return "v4_trailing_30pct"
    elapsed=(now-opened).total_seconds()
    if elapsed<600:
        return None
    # A delayed 10min observation is not permission for optimistic extension.
    if not last_spy_bar_is_fresh(bars,now):
        return "v4_10min_or_stale_bars_flatten"
    if credit<=max(peak,cost)*.80:
        return "v4_extended_trail_20pct"
    available=[b.available_at for b in bars]
    import bisect
    i=bisect.bisect_right(available,quote.timestamp)-1
    if i<35 or len(bars)<36:
        return "v4_10min_missing_momentum"
    if quote.ask<=quote.bid or not quote.bid_size or not quote.ask_size:
        return "v4_10min_untradeable_quote"
    spread=(quote.ask-quote.bid)/((quote.ask+quote.bid)/2)
    if spread>MAX_QUOTE_SPREAD or credit<cost*(1+EXTEND_MIN_RETURN):
        return "v4_10min_extension_rejected"
    if credit<peak*EXTEND_MIN_PREMIUM_PEAK_FRACTION:
        return "v4_10min_from_peak"
    prev_volume=[x.volume for x in bars[i-20:i]]
    if len(prev_volume)<20 or min(prev_volume)<=0:
        return "v4_10min_no_volume"
    if bars[i].volume/statistics.fmean(prev_volume)<EXTEND_MIN_RVOL:
        return "v4_10min_volume_faded"
    closes=[b.close for b in bars[i-35:i+1]]
    direction=1 if quote.right=="call" else -1
    if direction*(closes[-1]/closes[-4]-1)*10000<EXTEND_MIN_SPY_MOMENTUM_BPS:
        return "v4_10min_momentum_faded"
    if direction*(closes[-1]-ema(closes,8))<=0:
        return "v4_10min_ema_failed"
    return None



class WebullMinuteBarCache:
    """Entitled Webull M1 OHLCV reader, checked at 5-second intervals.

    No reconstructed OHLCV from 2-second option snapshots. Fail closed if
    the broker's stock-minute bars subscription is denied or out of date.
    """
    def __init__(self):
        self.bars=[]
        self.last_attempt=None
        self.error="SPY M1 bars not fetched"
        self.source="Webull OpenAPI stock M1 OHLCV"

    def refresh(self,provider,now:datetime):
        if self.last_attempt is not None:
            age=(now-self.last_attempt).total_seconds()
            if 0<=age<5:
                return self.bars
        self.last_attempt=now
        client=getattr(provider,"client",None) or getattr(getattr(provider,"provider",None),"client",None)
        try:
            if client is None or not hasattr(client.market_data,"get_history_bar"):
                raise ValueError("Webull M1 OHLCV entitlement/provider is unavailable")
            # Webull's latest-bars mode can contain an unfinished candle.
            # Fetch it, then strictly exclude unfinished minutes before
            # any quantitative signal can observe them.
            response=client.market_data.get_history_bar(
                "SPY","US_STOCK","M1",count="90",
                real_time_required=False,trading_sessions="RTH",
            )
            chart_bars=parse_webull_bars(response,now,include_forming=True)
            _store_spy_chart(chart_bars,now)
            # Trade decisions use only CONFIRMED completed bars. A forming
            # chart candle can update every 5 seconds but may never leak
            # into signal generation, risk management, or backtesting.
            fresh=parse_webull_bars(response,now)
            if not last_spy_bar_is_fresh(fresh,now) or len(fresh)<55:
                raise ValueError("Webull did not return 55 fresh completed SPY M1 RTH bars")
            self.bars=fresh
            self.error=None
        except Exception as exc:
            # Any uncertain history result is no permission to enter.
            self.bars=[]
            self.error=f"{type(exc).__name__}: no valid SPY M1 bar entitlement/data"
            _store_spy_chart([],now,reason=self.error)
        return self.bars
