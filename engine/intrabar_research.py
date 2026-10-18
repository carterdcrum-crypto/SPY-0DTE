"""Read-only, event-time intrabar ignition experiment on collector snapshots.

RESEARCH ONLY: no broker API, no Webull reads, no paper/live account writes,
no changes to V5. The collector records sampled stock LAST prices alongside
option NBBO, *not* true tick-volume or a full OPRA book. This is a price-only
proxy study; it is not an exact reconstruction of V5 or executable fills.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import statistics
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .session_calendar import session_close

ET = ZoneInfo("America/New_York")
ARMS = ("early_5s", "early_10s", "early_20s", "hybrid_10s", "after_candle_close")
FEE_PER_SIDE = .65
SLIPPAGE_PER_SHARE = .01
MAX_QUOTE_AGE = 3.0
MAX_ROWS_PER_DAY = 350000


@dataclass(frozen=True)
class Quote:
    symbol: str
    right: str
    bid: float
    ask: float
    bid_size: int
    ask_size: int
    delta: float


@dataclass(frozen=True)
class Cycle:
    at: datetime
    spot: float
    quotes: dict[str, Quote]


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("untrusted timestamp without timezone")
    return parsed.astimezone(timezone.utc)


def _tradable(q: Quote) -> bool:
    mid = (q.ask + q.bid) / 2
    return (q.ask > q.bid > 0 and q.bid_size >= 1 and q.ask_size >= 1
            and mid > 0 and (q.ask - q.bid) / mid <= .03
            and math.isfinite(q.delta) and .40 <= abs(q.delta) <= .60)


def _cycles(path: str | Path, day: date):
    """Load a bounded day of genuinely timestamped production option snapshots."""
    if not Path(path).exists():
        return [], {"status": "BLOCKED_MISSING_COLLECTOR_SQLITE"}
    uri = f"file:{Path(path).resolve()}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True, timeout=3.0) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                """SELECT received_at,timestamp,underlying_timestamp,
                          option_symbol,expiration,right,bid,ask,bid_size,
                          ask_size,delta,underlying_price,timestamp_quality
                   FROM option_snapshots
                   WHERE received_at >= ? AND received_at < ?
                     AND source = 'webull_production'
                   ORDER BY received_at ASC LIMIT ?""",
                (day.isoformat(), (day+timedelta(days=1)).isoformat(),
                 MAX_ROWS_PER_DAY+1),
            ).fetchall()
    except sqlite3.Error:
        return [], {"status": "BLOCKED_COLLECTOR_DATABASE_UNREADABLE"}
    if len(rows) > MAX_ROWS_PER_DAY:
        return [], {"status": "BLOCKED_DAY_EXCEEDS_BOUNDED_RESEARCH_ROWS",
                    "row_limit": MAX_ROWS_PER_DAY}
    bundles = defaultdict(list)
    for r in rows:
        bundles[r["received_at"]].append(r)
    cycles = []
    invalid = 0
    for timestamp, batch in sorted(bundles.items()):
        try:
            at = _utc(timestamp)
            if at.astimezone(ET).date() != day:
                continue
            # Verify quote *and* underlying source timestamps independently;
            # receive time by itself is not a market-data freshness proof.
            valid = []
            for r in batch:
                try:
                    if (r["timestamp_quality"] != "option_quote_timestamp"
                        or not r["underlying_timestamp"]
                        or r["expiration"] != day.isoformat()):
                        continue
                    option_at, stock_at = _utc(r["timestamp"]), _utc(r["underlying_timestamp"])
                    if not all(-.25 <= (at-t).total_seconds() <= MAX_QUOTE_AGE
                               for t in (option_at, stock_at)):
                        continue
                    spot = float(r["underlying_price"])
                    q = Quote(
                        str(r["option_symbol"]), str(r["right"]).lower(),
                        float(r["bid"]), float(r["ask"]),
                        int(r["bid_size"]), int(r["ask_size"]),
                        float(r["delta"]),
                    )
                    if not math.isfinite(spot) or spot <= 0 or not _tradable(q):
                        continue
                    valid.append((spot, q))
                except (ValueError, TypeError, OverflowError):
                    invalid += 1
            if not valid:
                continue
            # One SPY stock snapshot is common to the whole batch.
            spot = valid[0][0]
            cycles.append(Cycle(at, spot, {q.symbol:q for _,q in valid}))
        except (TypeError, ValueError):
            invalid += 1
    return cycles, {"status":"LOADED","db_rows":len(rows),
                    "fresh_usable_cycles":len(cycles),
                    "untrusted_or_invalid_rows":invalid}


def _minute(at: datetime):
    return at.replace(second=0, microsecond=0)


def _completed_closes(cycles: list[Cycle]) -> dict[datetime, float]:
    """A minute is 'closed' only if last observed spot was within 5s of end."""
    latest = {}
    for c in cycles:
        minute = _minute(c.at)
        if (_minute(c.at)+timedelta(minutes=1)-c.at).total_seconds() <= 5:
            latest[minute] = c.spot
    return latest


def _candidate_events(cycles: list[Cycle]):
    """Causal 12-minute breakout and 3-minute trend using prices ONLY.

    This is deliberately NOT a volume-confirmed V5 momentum ignition. Never
    label the result as an exact V5 backtest or forward-tested live edge.
    """
    closed = _completed_closes(cycles)
    events=[]
    last_event=None
    recent_by_minute=defaultdict(list)
    for c in cycles:
        recent_by_minute[_minute(c.at)].append(c)
    for c in cycles:
        m=_minute(c.at)
        local=c.at.astimezone(ET)
        if not (local.hour >= 9 and (local.hour > 9 or local.minute >= 52)
                and local.hour < 15):
            continue
        if last_event is not None and (c.at-last_event).total_seconds() < 600:
            continue
        past=[closed.get(m-timedelta(minutes=k)) for k in range(1,13)]
        if any(v is None for v in past):
            continue
        old_3 = past[2]
        if not old_3 or old_3<=0:
            continue
        up=(c.spot>max(past)*1.0002 and c.spot/old_3-1>=.0003)
        down=(c.spot<min(past)*.9998 and c.spot/old_3-1<=-.0003)
        if not (up or down):
            continue
        side="call" if up else "put"
        eligible=[q for q in c.quotes.values() if q.right==side]
        if not eligible:
            continue
        q=min(eligible,key=lambda row:(abs(abs(row.delta)-.5), row.ask-row.bid, row.symbol))
        events.append((c.at,side,q.symbol,m, past))
        last_event=c.at
        if len(events)>=40:
            break
    return events


def _fill(cycles: list[Cycle], symbol: str, start: datetime, end: datetime):
    for c in cycles:
        if c.at < start:
            continue
        if c.at > end:
            return None
        q=c.quotes.get(symbol)
        if q is not None and _tradable(q):
            return c,q
    return None


def _simulate(cycles, event, arm):
    at,side,symbol,minute,past=event
    if arm=="after_candle_close":
        begin=minute+timedelta(minutes=1)
    else:
        begin=at+timedelta(seconds=int(arm.split("_")[1][:-1]))
    if arm=="hybrid_10s":
        # Require price to keep extending beyond event (observed only as-of
        # the +10s decision point) before the hypothetical entry.
        prior = [c for c in cycles if at+timedelta(seconds=7) <= c.at <= begin]
        if not prior:
            return None,"no_hybrid_confirmation_sample"
        progress=(prior[-1].spot/next(c.spot for c in cycles if c.at==at)-1)
        if progress*(1 if side=="call" else -1) < .0001:
            return None,"hybrid_momentum_not_confirmed"
    entry=_fill(cycles,symbol,begin,begin+timedelta(seconds=3))
    if entry is None:
        return None,"no_fresh_entry_quote"
    entry_cycle,entry_quote=entry
    debit=(entry_quote.ask+SLIPPAGE_PER_SHARE)*100+FEE_PER_SIDE
    peak=0.
    expiry=entry_cycle.at+timedelta(minutes=5)
    cutoff=expiry
    local=entry_cycle.at.astimezone(ET)
    close=session_close(local.date())
    if close is not None:
        safe_end=datetime.combine(local.date(), close, ET)-timedelta(minutes=15)
        cutoff=min(cutoff,safe_end.astimezone(timezone.utc))
    if cutoff<=entry_cycle.at:
        return None,"insufficient_hold_window"
    seen=False
    previous=entry_cycle.at
    for c in cycles:
        if c.at<=entry_cycle.at:
            continue
        if c.at>cutoff+timedelta(seconds=3):
            break
        q=c.quotes.get(symbol)
        if q is None or not _tradable(q):
            continue
        if (c.at-previous).total_seconds()>15:
            return None,"unobserved_quote_gap"
        previous=c.at
        seen=True
        credit=max(0,(q.bid-SLIPPAGE_PER_SHARE)*100-FEE_PER_SIDE)
        peak=max(peak,credit)
        reason=None
        if credit<=debit*.65:
            reason="hard_stop_35pct"
        elif credit<=max(peak,debit)*.75:
            reason="premium_trail_25pct"
        elif c.at>=cutoff:
            reason="five_minute_or_session_exit"
        if reason is not None:
            return {"entry_at":entry_cycle.at.isoformat(),
                    "exit_at":c.at.isoformat(),"side":side,"symbol":symbol,
                    "net_pnl_usd":round(credit-debit,2),"reason":reason,
                    "entry_debit_usd":round(debit,2)},None
    return None,"no_observed_exit"


def research_day(path: str | Path, day: date) -> dict:
    """One-contract matched-event comparison; no portfolio-compounding claim."""
    if session_close(day) is None:
        return {"status":"MARKET_CLOSED","date":day.isoformat(),"broker_orders":0}
    cycles, quality=_cycles(path,day)
    base={"date":day.isoformat(),"dataset":"sampled_Webull_production_stock_last_and_0DTE_NBBO",
          "study":"PRICE_ONLY_INTRABAR_PROXY_NOT_ORIGINAL_V5",
          "no_stock_intrabar_volume":True,"not_actual_broker_fills":True,
          "broker_orders":0,"live_v5_unchanged":True,"data_quality":quality}
    if len(cycles)<50:
        return {**base,"status":"BLOCKED_INSUFFICIENT_TIMESTAMP_VERIFIED_CYCLES",
                "candidate_events":0,"results":{}}
    events=_candidate_events(cycles)
    results={a:[] for a in ARMS}
    skips={a:defaultdict(int) for a in ARMS}
    complete_pairs=0
    for event in events:
        outcomes={}
        for arm in ARMS:
            result,reason=_simulate(cycles,event,arm)
            if result is None:
                skips[arm][reason]+=1
            else:
                outcomes[arm]=result
        if len(outcomes)==len(ARMS):
            complete_pairs+=1
        for arm,row in outcomes.items():
            results[arm].append(row)
    report={}
    for arm, trades in results.items():
        profits=[r["net_pnl_usd"] for r in trades]
        report[arm]={"scorable_events":len(trades),
                     "wins":sum(x>0 for x in profits),
                     "win_rate_pct":None if not profits else round(100*sum(x>0 for x in profits)/len(profits),2),
                     "mean_net_pnl_per_one_contract_usd":None if not profits else round(statistics.mean(profits),2),
                     "total_hypothetical_one_contract_pnl_usd":round(sum(profits),2),
                     "unscorable_reasons":dict(skips[arm]),
                     "sample_trades":trades[:5]}
    return {**base,"status":("RESEARCH_SAMPLE_AVAILABLE" if complete_pairs>=5
                            else "INSUFFICIENT_MATCHED_EVENTS_NO_EDGE_CLAIM"),
            "candidate_events":len(events),"all_arm_matched_events":complete_pairs,
            "results":report}


def run_once(path: str | Path, output: str | Path, *, days: int = 5,
             now: datetime | None = None):
    """Never trade. Re-run on most recent complete trading sessions, idempotently."""
    now=(now or datetime.now(timezone.utc)).astimezone(ET)
    previous=now.date()
    # Do not read/evaluate an incomplete session until AFTER regular close.
    if session_close(previous) is not None:
        close=datetime.combine(previous,session_close(previous),ET)
        if now<close+timedelta(minutes=20):
            previous-=timedelta(days=1)
    days_to_run=[]
    for i in range(30):
        d=previous-timedelta(days=i)
        if session_close(d) is not None:
            days_to_run.append(d)
        if len(days_to_run)>=max(1,min(days,10)):
            break
    payload={"created_at":datetime.now(timezone.utc).isoformat(),
             "status":"RESEARCH_ONLY",
             "live_v5_unchanged":True,"broker_orders":0,
             "results":[research_day(path,d) for d in days_to_run]}
    dest=Path(output)
    dest.parent.mkdir(parents=True,exist_ok=True)
    temp=dest.with_suffix(".tmp")
    temp.write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n")
    temp.replace(dest)
    return payload


def run_forever(context):
    """Off-session, read-only task; no SDK, market API or broker interaction."""
    from .collector_runner import is_collection_window, settings_from_env
    from .runtime import WorkerContext
    settings=settings_from_env()
    output=os.environ.get("INTRABAR_RESEARCH_REPORT","/data/intrabar_research/report.json")
    last_run=None
    while not context.stop.is_set():
        now=datetime.now(timezone.utc)
        # Even off-session analysis must not compete with live at market open.
        if is_collection_window(now,settings):
            context.heartbeat(state="IDLE",last_error=None)
            context.wait(300)
            continue
        today_et=now.astimezone(ET)
        latest=today_et.date()
        close=session_close(latest)
        if close is None or today_et < datetime.combine(latest,close,ET)+timedelta(minutes=20):
            latest-=timedelta(days=1)
        while session_close(latest) is None:
            latest-=timedelta(days=1)
        key=latest.isoformat()
        # A run at midnight must not suppress the new completed session
        # after 16:20 ET that same day.
        if last_run != key:
            try:
                payload=run_once(settings.db_path,output,now=now)
                last_run=key
                from logging import getLogger
                getLogger("spy0dte.intrabar").info(
                    "research finished session=%s status=%s valid_cycles=%s candidate_events=%s matched_events=%s orders=0",
                    key,
                    payload["results"][0].get("status"),
                    payload["results"][0].get("data_quality",{}).get("fresh_usable_cycles",0),
                    payload["results"][0].get("candidate_events",0),
                    payload["results"][0].get("all_arm_matched_events",0),
                )
                context.heartbeat(state="RUNNING",last_error=None,
                                  latest_researched_session=key,
                                  complete_days=len(payload["results"]))
            except (OSError,ValueError,sqlite3.Error) as exc:
                context.heartbeat(state="RECOVERING",last_error=type(exc).__name__)
        context.wait(600)
