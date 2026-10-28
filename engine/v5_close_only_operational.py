"""Causal V5 close-proxy proposal shared by PAPER and owner-authorized LIVE.

The archived $867k full-cash+drawdown replay lacks historical executable depth
and market-impact data. Paper never sends broker orders; the separate LIVE
executor may send owner-authorized orders using this proposal, but first
rechecks actual Webull settled cash and fresh displayed bid/ask depth.
Neither live nor paper promises the proxy research return.
No lookahead, fabricated OHLCV or AI decision models.
"""
from __future__ import annotations

import math
import statistics
from datetime import datetime, timedelta
from types import SimpleNamespace

from .v5_archive_proxy import close_proxy_signals
from .v4_operational import (
    ET, FEE, SLIPPAGE_PER_SHARE, MAX_SOURCE_DELAY_SECONDS,
    last_spy_bar_is_fresh, _rv,
)
from .session_calendar import session_close
from quantconnect.v5_core import quality_score

PROFILE = "v5_close_only_unrestricted_research"
MAX_PREMIUM_SPREAD = .05
# Deliberate post-archive V5 production variant. The archived $867,017.60
# reference used absolute delta 0.40-0.60; keep archival replay unchanged.
V5_MIN_ABS_DELTA = .35
V5_MAX_ABS_DELTA = .65
V5_MAX_IV_RV = 1.5
# Frozen from the $867,017.60 128-session V5 close-proxy study.
V5_MIN_COMPLETED_BARS = 37
V5_LATEST_ENTRY_MINUTES = 26
V5_MAX_HOLD_MINUTES = 18

def _reference_frames(bars):
    """Observed SPY completed close and observed volume-derived RVOL only."""
    frames = []
    for i, b in enumerate(bars):
        prev = bars[i-20:i] if i >= 20 else []
        rvol = b.volume / statistics.fmean(x.volume for x in prev) if (
            len(prev)==20 and min(x.volume for x in prev)>0) else 0.
        frames.append(SimpleNamespace(timestamp=b.start,
            market=SimpleNamespace(spot=b.close, volume_ratio=rvol)))
    return frames


def paper_candidate(now, bars, quotes, *, equity, settled, peak,
                    risk_fraction: float = 1.0, risk_opening_equity: float | None = None):
    """Independent paper signal / live-observation preview; never an order.

    Verify fresh option quote AND its underlying timestamp and genuine size.
    Does not infer a fill; paper execution uses sampled spread-crossing debit.
    """
    if now.tzinfo is None:
        raise ValueError("timezone-aware now required")
    local = now.astimezone(ET)
    close = session_close(local.date())
    if close is None or local.hour < 9 or (local.hour == 9 and local.minute < 52):
        return None, {"state":"MARKET_CLOSED"}
    # Replay blocks entry in the last 26 minutes of the SPY session.
    if (datetime.combine(local.date(),close,ET)-local).total_seconds() <= V5_LATEST_ENTRY_MINUTES*60:
        return None, {"state":"ENTRY_WINDOW_CLOSED"}
    if not (equity>0 and settled>0 and peak>0):
        return None, {"state":"INSUFFICIENT_SETTLED_CASH"}
    # Original unrestricted V5 proposal is now bounded by the as-of, durable
    # prior-session drawdown throttle. Real settled cash/depth always win.
    opening = equity if risk_opening_equity is None else float(risk_opening_equity)
    if (not math.isfinite(float(risk_fraction)) or not 0 < risk_fraction <= 1
            or not math.isfinite(opening) or opening <= 0):
        return None, {"state":"INVALID_V5_DRAWDOWN_BUDGET"}
    budget = min(float(settled), opening * risk_fraction)
    # Close-proxy challenger first evaluates i=35; entry uses the NEXT
    # completed minute, so require 36 signal bars + 1 confirmation bar.
    if len(bars)<V5_MIN_COMPLETED_BARS or not last_spy_bar_is_fresh(bars,now):
        return None, {"state":"WAITING_REAL_COMPLETED_SPY_M1"}
    tail=bars[-V5_MIN_COMPLETED_BARS:]
    if any(b.start.astimezone(ET).date()!=local.date() or b.volume<=0 for b in tail):
        return None, {"state":"NONCONTIGUOUS_REAL_SPY_M1"}
    if any((b.start-a.start).total_seconds()!=60 for a,b in zip(tail,tail[1:])):
        return None, {"state":"NONCONTIGUOUS_REAL_SPY_M1"}
    # bar[-2] is the signal close; bar[-1] is the next FINISHED minute.
    sig_at=bars[-2].available_at
    if not 60 <= (now-sig_at).total_seconds() <= 125:
        return None, {"state":"WAITING_AFTER_SIGNAL_MINUTE"}
    frames=_reference_frames(bars[:-1])
    setups=close_proxy_signals(frames,len(frames)-1,challenger=True)
    if not setups:
        return None, {
            "state":"NO_CLOSE_ONLY_V5_SIGNAL",
            "reason":"No momentum ignition, liquidity reversal, or volatility expansion on completed SPY candles",
            "entry_diagnostics":{
                "signal_detected":False,"data_ready":True,
                "settled_cash_usd":round(float(settled),2),
                "cheapest_qualified_contract_usd":None,
                "cheapest_qualified_ask":None,
                "rejection_code":"no_close_only_signal",
                "rejection_counts":{},
                "quotes_observed":0,
                "signals_found":0,"asof":now.isoformat(),
            },
        }
    rv=_rv(bars,len(bars)-3)
    if rv<=0:
        return None, {"state":"MISSING_REALIZED_VOLATILITY"}
    # Diagnostics use the EXACT SAME screening sequence as execution.
    # The delta gate is intentionally widened for this owner-selected V5
    # variant; quote-spread cap is now owner-selected at 5%, while quote
    # freshness, executable depth, IV and cash gates remain unchanged.
    quotes=tuple(quotes)
    checks = {
        "wrong_expiration_or_side": 0, "untrusted_source": 0,
        "stale_quote": 0, "invalid_bid_ask": 0,
        "missing_depth": 0, "missing_greeks": 0,
        "delta_out_of_range": 0, "iv_out_of_range": 0,
        "wide_spread": 0, "insufficient_settled_cash": 0,
    }
    # Count each distinct option/side just once even if multiple setup
    # families signal in the same minute. Execution still evaluates every
    # setup and quote combination exactly as before.
    counted_rejections=set()
    def reject(code, sig, q):
        identity=(sig.side, getattr(q, "option_symbol", id(q)))
        if identity not in counted_rejections:
            counted_rejections.add(identity)
            checks[code] += 1

    cheapest=None
    cheapest_ask=None
    choices=[]
    for sig in setups:
        for q in quotes:
            try:
                if q.expiration!=local.date() or q.right!=sig.side:
                    reject("wrong_expiration_or_side", sig, q)
                    continue
                if (q.source!="webull_production"
                    or q.timestamp_quality!="option_quote_timestamp"
                    or q.underlying_timestamp is None):
                    reject("untrusted_source", sig, q)
                    continue
                if any(not 0 <= (now-at).total_seconds() <= MAX_SOURCE_DELAY_SECONDS
                       for at in (q.timestamp,q.received_at,q.underlying_timestamp)):
                    reject("stale_quote", sig, q)
                    continue
                bid=float(q.bid); ask=float(q.ask)
                bs=int(q.bid_size); a_s=int(q.ask_size)
                if not (math.isfinite(bid) and math.isfinite(ask) and ask>bid>0):
                    reject("invalid_bid_ask", sig, q)
                    continue
                if bs<1 or a_s<1:
                    reject("missing_depth", sig, q)
                    continue
                if q.greeks is None:
                    reject("missing_greeks", sig, q)
                    continue
                delta=float(q.greeks.delta); iv=float(q.greeks.implied_volatility)
                if not (math.isfinite(delta) and math.isfinite(iv)):
                    reject("missing_greeks", sig, q)
                    continue
                if not V5_MIN_ABS_DELTA <= abs(delta) <= V5_MAX_ABS_DELTA:
                    reject("delta_out_of_range", sig, q)
                    continue
                if not (iv > 0 and iv / rv <= V5_MAX_IV_RV):
                    reject("iv_out_of_range", sig, q)
                    continue
                spread=(ask-bid)/((ask+bid)/2)
                if spread>MAX_PREMIUM_SPREAD:
                    reject("wide_spread", sig, q)
                    continue
                debit=round((ask+SLIPPAGE_PER_SHARE)*100+FEE,2)
                # Show the CHEAPEST quote which passes ALL non-cash gates.
                # A lower ask from a stale/wide/untrusted quote is not eligible.
                if cheapest is None or debit<cheapest:
                    cheapest=debit
                    cheapest_ask=ask
                # Cash-first, then peak-to-trough EOD risk fraction and
                # independently observed option quote depth.
                n=min(int((budget+1e-8)//debit),bs,a_s)
                if n<1:
                    reject("insufficient_settled_cash", sig, q)
                    continue
                score=quality_score(sig,spread,delta,iv,rv)
                choices.append((abs(abs(delta)-.5),spread,str(q.option_symbol),
                    sig,q,n,debit,score))
            except (AttributeError,TypeError,ValueError,ZeroDivisionError,OverflowError):
                reject("missing_greeks", sig, q)
                continue
    reason_codes = {
        "wrong_expiration_or_side": "No matching same-day call/put contract",
        "untrusted_source": "Option quote source or timestamp unverified",
        "stale_quote": "Option or underlying quote older than 3 seconds",
        "invalid_bid_ask": "Invalid option bid/ask prices",
        "missing_depth": "No executable size on both sides of the quote",
        "missing_greeks": "Option delta or implied volatility unavailable",
        "delta_out_of_range": "Option delta outside 0.35–0.65",
        "iv_out_of_range": "Option implied volatility exceeds the V5 threshold",
        "wide_spread": "Option bid/ask spread exceeds 5%",
        "insufficient_settled_cash": "Settled cash cannot cover one qualifying contract",
    }
    blocking = "eligible" if choices else next(
        (name for name in (
            "insufficient_settled_cash", "wide_spread", "iv_out_of_range",
            "delta_out_of_range", "missing_greeks", "missing_depth",
            "invalid_bid_ask", "stale_quote", "untrusted_source",
            "wrong_expiration_or_side",
        ) if checks[name]), "no_option_quotes")
    details={
        "signal_detected":True,
        "data_ready":True,
        "settled_cash_usd":round(float(settled),2),
        "cheapest_qualified_contract_usd":cheapest,
        "cheapest_qualified_ask":cheapest_ask,
        "rejection_code":blocking,
        "rejection_counts":checks,
        "quotes_observed":len(quotes),
        "signals_found":len(setups),
        "abs_delta_range":[V5_MIN_ABS_DELTA,V5_MAX_ABS_DELTA],
        "max_iv_rv_ratio":V5_MAX_IV_RV,
        "max_bid_ask_spread_fraction":MAX_PREMIUM_SPREAD,
        "risk_fraction":risk_fraction, "risk_opening_equity":round(opening,2),
        "risk_budget_usd":round(budget,2),
        "asof":now.isoformat(),
    }
    if not choices:
        return None, {
            "state":"NO_EXECUTABLE_DEPTH_OR_FRESH_OPTION_QUOTE",
            "reason":reason_codes.get(blocking,"No current option quotes"),
            "entry_diagnostics":details,
        }
    _,_,_,sig,q,n,debit,score=min(choices,key=lambda x:x[:3])
    return {"symbol":q.option_symbol,"quantity":n,"side":sig.side,
        "quality_score":score,"setup":sig.setup,
        "signal_at":sig_at.isoformat(),"cycle":sig_at.isoformat(),
        "debit_per_contract":debit,
        "underlying_spot":q.underlying_price,
        "sizing_mode":"FULL_CASH_WITH_EOD_DRAWDOWN_THROTTLE",
        "risk_fraction":risk_fraction,
        "risk_opening_equity":round(opening,2),
        "risk_budget_usd":round(budget,2),
        "reason":f"{PROFILE}; {sig.setup}; long0dte; paper-research-only",
        "no_live_order":True}, {"state":"PAPER_RESEARCH_SIGNAL","contracts":n,
                                 "full_premium_risk_usd":round(n*debit,2),
                                 "risk_fraction":risk_fraction,
                                 "risk_budget_usd":round(budget,2),
                                 "entry_diagnostics":details,
                                 "no_live_order":True}


def paper_exit_reason(position,quote,bars,now):
    """Observed 35% hard loss / 25% premium trail / reversal / 18min exit."""
    if quote is None or not 0<=(now-quote.timestamp).total_seconds()<=MAX_SOURCE_DELAY_SECONDS:
        return None
    opened=position["opened_at"]
    if isinstance(opened,str):
        opened=datetime.fromisoformat(opened)
    if opened.tzinfo is None:
        return "invalid_entry_timestamp"
    debit=float(position.get("average_cost",0))
    if debit<=0:
        return "invalid_cost"
    peak=float(position.get("peak_exit_value",debit))
    credit=max(0.,(float(quote.bid)-SLIPPAGE_PER_SHARE)*100-FEE)
    if credit<=debit*.65:
        return "v5_paper_stop_35pct"
    if credit<=max(peak,debit)*.75:
        return "v5_paper_trailing_25pct"
    age=(now-opened).total_seconds()
    if age<600:
        return None
    if not last_spy_bar_is_fresh(bars,now) or len(bars)<5:
        return "v5_missing_complete_m1"
    # The observed option right is authoritative, not symbol-string guessing.
    if getattr(quote,"right",None) not in ("call","put"):
        return "v5_unknown_contract_right"
    direction=1 if quote.right=="call" else -1
    if direction*(bars[-1].close/bars[-4].close-1)<0:
        return "v5_spy_close_reversal"
    if age >= V5_MAX_HOLD_MINUTES * 60:
        return "v5_18min_timeout"
    return None
