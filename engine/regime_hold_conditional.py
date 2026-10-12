"""V4 conditional hold extension, isolated research policy.

At 10 minutes, extend ONLY after as-of profitability, contract liquidity,
completed SPY price-momentum, EMA8 and observed volume pass. Otherwise sell.
If extended, re-evaluate each received quote, stop on failed price structure,
independent hard loss, trailing premium stop, or market-close safety cutoff.
No future bars or quote prices used in any entry/hold decision.

Historical legacy archive lacks original OHLCV and executable NBBO; this is
a price-close/ratio proxy, not live-trading authorization.
"""
from __future__ import annotations

import bisect
from datetime import timedelta

from .regime_reclaim_frozen_signal import ema
from .regime_hold_sensitivity import (
    PREMIUM_FEE_PER_SIDE, SLIP_PER_SHARE, HARD_STOP, TRAIL_PCT,
    MAX_FIXED_MIN, BAR_TOLERANCE_SECONDS,
)

EXTEND_MIN_RETURN=0.10
EXTEND_MIN_RVOL=0.90
EXTEND_MIN_SPY_MOMENTUM_BPS=3.0
EXTEND_MIN_PREMIUM_PEAK_FRACTION=0.90
MAX_QUOTE_SPREAD=0.035
EXTEND_TRAIL=.20

def _price_credit(bid):
    return max(0.0,(bid-SLIP_PER_SHARE)*100.0-PREMIUM_FEE_PER_SIDE)

def _outcome(credit,debit,at,reason,extended=False):
    return {
        "pnl_usd":round(credit-debit,6),
        "exit_at":at,"exit_reason":reason,
        "extended":extended,
    }

def _asof_frame(frames, times, stamp):
    idx=bisect.bisect_right(times,stamp)-1
    if idx<0:
        return -1,None
    return idx,frames[idx]

def _hold_gate(frames, times, at, side, credit, debit, peak, symbol):
    """Only previously completed SPY closes and as-of option quote."""
    idx,frame=_asof_frame(frames,times,at)
    if frame is None or idx<34 or frame.option(symbol) is None:
        return False
    q=frame.option(symbol)
    if q.bid<=0 or q.ask<=q.bid or q.spread_fraction>MAX_QUOTE_SPREAD:
        return False
    if credit < debit*(1+EXTEND_MIN_RETURN):
        return False
    if credit < peak*EXTEND_MIN_PREMIUM_PEAK_FRACTION:
        return False
    if frame.market.volume_ratio < EXTEND_MIN_RVOL:
        return False
    spot=[f.market.spot for f in frames[max(0,idx-35):idx+1]]
    if len(spot)<35:
        return False
    direction=1 if side=="call" else -1
    change_bps=direction*(spot[-1]/spot[-4]-1)*10000
    if change_bps<EXTEND_MIN_SPY_MOMENTUM_BPS:
        return False
    if direction*(spot[-1]-ema(spot,8))<=0:
        return False
    return True

def simulate_conditional(entry_ask,path,frames,*,entry_at,side,symbol,cutoff):
    """Return realized quote-sample exit; None if terminal mark cannot be verified.

    The V1 10-minute test is preserved for non-qualifying positions;
    extension never ignores the compulsory 15-minute expiry buffer.
    """
    if not path or not frames or side not in ("call","put"):
        return None
    debit=(entry_ask+SLIP_PER_SHARE)*100+PREMIUM_FEE_PER_SIDE
    peak=debit
    due=entry_at+timedelta(minutes=MAX_FIXED_MIN)
    times=[f.timestamp+timedelta(minutes=1) for f in frames]
    extended=False
    gate_seen=False
    last_before_cutoff=None
    trail=TRAIL_PCT/100
    for at,bid in path:
        if at<=entry_at or at>cutoff:
            continue
        credit=_price_credit(bid)
        peak=max(peak,credit)
        last_before_cutoff=(at,credit)
        if credit<=debit*(1-HARD_STOP)+1e-9:
            return _outcome(credit,debit,at,"independent_hard_stop_35pct",extended)
        if credit<=peak*(1-trail)+1e-9:
            return _outcome(credit,debit,at,"trailing_stop_20pct_extended" if extended else "trailing_stop",extended)

        if not gate_seen and at>=due:
            # Do not infer profitability from a quote many minutes past due.
            if (at-due).total_seconds()>BAR_TOLERANCE_SECONDS:
                return None
            gate_seen=True
            if not _hold_gate(frames,times,at,side,credit,debit,peak,symbol):
                return _outcome(credit,debit,at,"standard_10min_exit",False)
            extended=True
            trail=min(trail,EXTEND_TRAIL)
            continue
        if extended:
            if not _hold_gate(frames,times,at,side,credit,debit,peak,symbol):
                return _outcome(credit,debit,at,"extended_momentum_or_profit_faded",True)
            # Already tested hard and 30% trail this tick; check newly tighter
            # stop IMMEDIATELY without waiting for the next quote.
            if credit<=peak*(1-trail)+1e-9:
                return _outcome(credit,debit,at,"trailing_stop_20pct_extended",True)
    if not gate_seen:
        return None
    if not extended:
        raise AssertionError("gate evaluated but failed to produce exit")
    if last_before_cutoff and abs((cutoff-last_before_cutoff[0]).total_seconds())<=BAR_TOLERANCE_SECONDS:
        at,credit=last_before_cutoff
        return _outcome(credit,debit,at,"mandatory_pre_expiry_liquidation",True)
    # Data gap: no assumed profitable fill at a stale quote.
    return None
