"""Locked combined Regime Reclaim signal extracted verbatim from exploratory arm.

Data interface: completed, contiguous one-minute SPY close + as-of volume ratio.
No option prices, future bars, or model updates affect which bar emits a signal.
"""
from __future__ import annotations
import math
import statistics
from datetime import timedelta, time
from zoneinfo import ZoneInfo

ET=ZoneInfo("America/New_York")
AFTER_OPEN=time(9,52)
DAY_END=time(15,25)
TRAIL_PCT=30
HARD_STOP_PCT=35
MAX_HOLD_MIN=10
START_CASH=1000.0
FEE_PER_SIDE=0.65
SLIP_PER_SHARE=0.01
HARD_STOP=0.35

def _bps(current:float,past:float)->float:
    return (current/past-1.0)*10000 if past>0 else 0.0

def ema(values:list[float],period:int)->float:
    if not values or period<2:raise ValueError("missing EMA data")
    alpha=2./(period+1.)
    e=values[0]
    for x in values[1:]:
        e=alpha*x+(1-alpha)*e
    return e


def contiguous(frames,i:int,n:int)->bool:
    return i>=n and all(
        (frames[k].timestamp-frames[k-1].timestamp).total_seconds()==60
        for k in range(i-n+1,i+1)
    )


def identify_setups(frames)->list[dict]:
    """Use information as-of signal bar close. No option outcome/exit data."""
    out=[]
    spots=[x.market.spot for x in frames]
    for i in range(35,len(frames)):
        signal=frames[i].timestamp+timedelta(minutes=1)
        et=signal.astimezone(ET).time()
        if et<AFTER_OPEN or et>=DAY_END:
            continue
        if not contiguous(frames,i,35):
            continue
        recent=spots[i-35:i+1]
        if not all(math.isfinite(x) and x>0 for x in recent):
            continue
        rvol=frames[i].market.volume_ratio
        if not math.isfinite(rvol) or rvol<.75:continue
        current=spots[i];prev=spots[i-1]
        # EMA recomputed on 35 completed bars; previous EMA excludes latest.
        fast=ema(recent[:-1],8)
        slow=ema(recent[:-1],21)
        slow10=ema(recent[:-11],21)
        newfast=ema(recent,8)
        trend=_bps(slow,slow10)
        twenty=spots[i-20:i]
        rng=(max(twenty)-min(twenty))
        path=sum(abs(spots[j]-spots[j-1]) for j in range(i-14,i+1))
        efficiency=abs(spots[i]-spots[i-15])/path if path>0 else 0.
        # Direction-confirmed pullback: previous close under/over the
        # fast EMA, latest close reclaims it within a slower EMA trend.
        trend_side=None
        if (trend>=2. and prev<=fast and current>newfast and
            current>slow and current>prev and efficiency>=.18):
            trend_side="call"
        elif (trend<=-2. and prev>=fast and current<newfast and
              current<slow and current<prev and efficiency>=.18):
            trend_side="put"
        if trend_side is not None:
            out.append({"at":signal,"side":trend_side,"setup":"trend_reclaim",
                        "volume_ratio":rvol,"trend_bps":round(trend,3),
                        "efficiency":round(efficiency,4)})
        # Exhaustion/reversion: prior *completed* close moved outside
        # a PREVIOUS 20-bar +/- 1.2std band, current closes back inside.
        # The reference band is frozen prior to previous completed close.
        base=spots[i-22:i-2]
        avg=statistics.fmean(base)
        sigma=statistics.pstdev(base)
        if sigma>0:
            low=avg-1.2*sigma;high=avg+1.2*sigma
            stretch=None
            if prev<low and current>low and current>prev and efficiency<.60:
                stretch="call"
            elif prev>high and current<high and current<prev and efficiency<.60:
                stretch="put"
            if stretch is not None:
                out.append({"at":signal,"side":stretch,"setup":"stretch_reclaim",
                            "volume_ratio":rvol,"trend_bps":round(trend,3),
                            "efficiency":round(efficiency,4)})
        # Failed close-range breakout: previous candle was beyond its
        # pre-existing 20 minute extreme, latest closes BACK inside.
        reference=spots[i-22:i-2]
        high=max(reference);low=min(reference)
        failed=None
        if prev>high and current<high and current<prev:
            failed="put"
        elif prev<low and current>low and current>prev:
            failed="call"
        if failed:
            out.append({"at":signal,"side":failed,"setup":"failed_range_reentry",
                        "volume_ratio":rvol,"trend_bps":round(trend,3),
                        "efficiency":round(efficiency,4)})
    return out


def simulate(ask: float, path, trail_pct: int | None) -> dict:
    """Return entire position at one sampled bid; no target, scaling or runner."""
    if not path or (trail_pct is not None and not 0 < trail_pct < 100):
        raise ValueError("invalid trace or percentage")
    entry = (ask + SLIP_PER_SHARE) * 100.0 + FEE_PER_SIDE
    peak = entry
    for i, (at, bid) in enumerate(path):
        exit_value = max(0.0, (bid - SLIP_PER_SHARE) * 100.0 - FEE_PER_SIDE)
        peak = max(peak, exit_value)
        if exit_value <= entry * (1.0 - HARD_STOP) + 1e-9:
            why = "independent_hard_stop_35pct"
        elif trail_pct is not None and exit_value <= peak * (1.0 - trail_pct / 100.0) + 1e-9:
            why = "trailing_stop"
        elif i == len(path) - 1:
            why = "hard_max_hold_10min"
        else:
            continue
        return {"pnl_usd": round(exit_value - entry, 6),
                "return_on_premium_pct": round(100.0*(exit_value/entry - 1),6),
                "exit_reason": why, "exit_time":at.isoformat()}
    raise RuntimeError("position remained unsettled")

