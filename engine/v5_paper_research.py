"""Fail-closed V5-to-Railway PAPER/SHADOW candidate bridge (research only).

This module is NOT called by production V4 collector, PAPER or LIVE workers.
It has no order submission, owner toggle, brokerage, or deployment side effects.
V5 promotion requires independent QC/out-of-sample and prospective proof.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta
import math
from types import SimpleNamespace

from quantconnect.v5_core import (
    latest_signals, realized_volatility, quality_score, contract_quantity,
)
from .v4_operational import ET, MAX_SOURCE_DELAY_SECONDS, last_spy_bar_is_fresh
from .session_calendar import session_close


def paper_research_candidate(now, bars, quotes, *, equity, settled, peak,
                             variant="v5_current", maximum_exposure=.35):
    """Return a proposal for audit, NEVER an instruction to a Webull broker.

    Last underlying bar is the ONE-MINUTE LATER execution context, not
    part of signal calculation; bar[-2] ends at signal timestamp.
    Requires a fresh current quote independently observed within 3 seconds.
    No missing option Greek/size observation is inferred.
    """
    if variant not in ("v5_current", "v5_optimized"):
        return None, {"state":"RESEARCH_ONLY_REFUSAL",
                      "reason":"only guarded V5 variants permitted"}
    if now.tzinfo is None:
        raise ValueError("aware time required")
    local=now.astimezone(ET)
    close=session_close(local.date())
    if close is None or local.time()<time(9,52):
        return None, {"state":"MARKET_CLOSED"}
    remaining=(datetime.combine(local.date(),close,ET)-local).total_seconds()
    if remaining<20*60:
        return None, {"state":"ENTRY_WINDOW_CLOSED"}
    if len(bars)<37 or not last_spy_bar_is_fresh(bars,now):
        return None, {"state":"WAITING_AUTHENTIC_M1"}
    recent=bars[-37:]
    if any(b.start.astimezone(ET).date()!=local.date() or b.volume<=0
           for b in recent):
        return None, {"state":"WAITING_CONTIGUOUS_M1"}
    if any((b.start-a.start).total_seconds()!=60 for a,b in
           zip(recent,recent[1:])):
        return None, {"state":"WAITING_CONTIGUOUS_M1"}
    # Frozen completed signal excludes the *newer* confirmation-minute bar.
    signal_bars=bars[:-1]
    closes=[b.close for b in signal_bars]
    highs=[b.high for b in signal_bars]
    lows=[b.low for b in signal_bars]
    volumes=[b.volume for b in signal_bars]
    signals=latest_signals(closes,highs,lows,volumes,variant)
    if not signals:
        return None, {"state":"NO_V5_SIGNAL"}
    signal_at=signal_bars[-1].available_at
    if not 60<=(now-signal_at).total_seconds()<=120:
        return None, {"state":"WAITING_NEXT_MINUTE_OBSERVATION"}
    rv=realized_volatility(closes)
    if rv<=0 or not (0<equity and 0<settled and 0<peak):
        return None, {"state":"RISK_OR_VOL_DATA_BLOCKED"}
    options=[]
    for sig in signals:
        for q in quotes:
            try:
                if (q.source not in ("webull_production","tradier_production_realtime")
                    or q.timestamp_quality!="option_quote_timestamp"
                    or q.expiration!=local.date() or q.right!=sig.side
                    or q.underlying_timestamp is None):
                    continue
                if any(not 0<=(now-stamp).total_seconds()<=MAX_SOURCE_DELAY_SECONDS
                       for stamp in (q.timestamp,q.received_at,q.underlying_timestamp)):
                    continue
                bid,ask=float(q.bid),float(q.ask)
                bid_size,ask_size=int(q.bid_size),int(q.ask_size)
                if not ask>bid>0 or min(bid_size,ask_size)<=0:
                    continue
                spread=(ask-bid)/((ask+bid)/2)
                if spread>.03:
                    continue
                delta=float(q.greeks.delta)
                iv=float(q.greeks.implied_volatility)
                if not all(map(math.isfinite,(delta,iv))) or iv<=0 or iv/rv>1.5:
                    continue
                if not .4<=abs(delta)<=.6:
                    continue
                score=quality_score(sig,spread,delta,iv,rv)
                qty=contract_quantity(variant,score,equity,settled,peak,ask,
                    bid_size=bid_size,ask_size=ask_size,
                    maximum_exposure=min(.35,maximum_exposure))
                if qty>0:
                    options.append((abs(abs(delta)-.5),spread,str(q.option_symbol),
                                    sig,q,qty,score))
            except (AttributeError,ValueError,TypeError,ZeroDivisionError,OverflowError):
                continue
    if not options:
        return None, {"state":"NO_ASOF_0DTE_NBBO_OR_SIZE"}
    _,_,_,sig,q,qty,score=min(options,key=lambda a:a[:3])
    return {"research_only":True,"send_order":False,
            "variant":variant,"signal_at":signal_at.isoformat(),
            "setup":sig.setup,"side":sig.side,"symbol":q.option_symbol,
            "quantity":qty,"quality_score":score,
            "observed_bid":float(q.bid),"observed_ask":float(q.ask),
            "observed_quote_at":q.timestamp.isoformat()}, {
            "state":"SHADOW_CANDIDATE_ONLY","broker_order_authorized":False}
