"""Cost-gated Databento HISTORICAL OPRA CMBP-1 acquisition.

Not invoked from normal app startup or deployment. Requires explicit
--execute, --max-usd and an authorized Databento API key; no live purchase,
subscription or Webull order. Fails closed on license or symbology gaps.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import statistics
import tempfile
from datetime import date, datetime, timedelta, time
from pathlib import Path
from zoneinfo import ZoneInfo

from .forward_event_ingest import ingest, _bars, _quote_rows, MAX_EVENT_ROWS
from .providers.databento_history import _definition_contract, _rows, _timestamp
from .session_calendar import session_close

NY=ZoneInfo("America/New_York")
OPTION_DATASET="OPRA.PILLAR"
UNDERLYING_DATASET="EQUS.MINI"
MAX_SYMBOLS=160


def _cost(client,**kw):
    cost=float(client.metadata.get_cost(**kw))
    if not math.isfinite(cost) or cost<0:
        raise ValueError("invalid vendor cost estimate")
    return cost


def _save(path,columns,rows):
    with path.open("w",newline="") as f:
        writer=csv.DictWriter(f,columns)
        writer.writeheader()
        writer.writerows(rows)


def acquire_one_day(client,day:date,raw_dir:Path,*,max_usd:float)->dict:
    """Fetch one event day; estimate cost before each paid data download."""
    if not math.isfinite(max_usd) or max_usd<=0:
        raise ValueError("positive explicitly authorized budget required")
    session_end=session_close(day)
    if session_end is None:
        raise ValueError("non-trading date")
    if (raw_dir/f"raw_spy_bars_{day}.csv").exists() or (raw_dir/f"raw_opra_events_{day}.csv").exists():
        raise FileExistsError("raw event feed exists; do not repurchase or overwrite")
    start=datetime.combine(day,time(9,30),NY).isoformat()
    end=datetime.combine(day,session_end,NY).isoformat()
    tomorrow=(day+timedelta(days=1)).isoformat()
    base_args=dict(dataset=UNDERLYING_DATASET,schema="ohlcv-1m",
                   symbols=["SPY"],start=start,end=end)
    def_args=dict(dataset=OPTION_DATASET,schema="definition",
                  symbols="SPY.OPT",stype_in="parent",
                  start=day.isoformat(),end=tomorrow)
    base_cost=_cost(client,**base_args)
    def_cost=_cost(client,**def_args)
    if base_cost+def_cost>max_usd:
        raise ValueError("underlying/definition cost estimate exceeds budget; nothing downloaded")
    underlying=_rows(client.timeseries.get_range(**base_args))
    definitions=_rows(client.timeseries.get_range(**def_args))
    closes=[float(x["close"]) for x in underlying if x.get("close") not in (None,"")]
    if len(closes)<36 or not all(math.isfinite(p) and p>0 for p in closes):
        raise ValueError("source has insufficient completed underlying observations")
    # Uses SPY close range only for post-session data acquisition coverage.
    # Never selects option symbols using future returns, Greeks or P&L.
    margin=max(5.0,statistics.median(closes)*.01)
    contracts={}
    for row in definitions:
        contract=_definition_contract(row)
        if contract and contract.expiration==day and contract.symbol.startswith("SPY"):
            if min(closes)-margin <= contract.strike <= max(closes)+margin:
                symbol=row.get("raw_symbol") or row.get("symbol")
                if symbol:
                    contracts[contract.symbol]=str(symbol)
    if not contracts or len(contracts)>MAX_SYMBOLS:
        raise ValueError("no eligible 0DTE contracts or symbol coverage exceeds cap")
    options_args=dict(dataset=OPTION_DATASET,schema="cmbp-1",
                      symbols=list(contracts.values()),stype_in="raw_symbol",
                      start=start,end=end)
    event_cost=_cost(client,**options_args)
    total=base_cost+def_cost+event_cost
    if total>max_usd:
        raise ValueError("OPRA CMBP-1 cost exceeds remaining budget; option download blocked (underlying/definitions may be billed)")
    events=_rows(client.timeseries.get_range(**options_args))
    bars=[]
    for r in underlying:
        ts=r.get("ts_event",r.get("ts_recv"))
        if ts is None:
            raise ValueError("underlying bar lacks source timestamp")
        bars.append({"ts_start":_timestamp(ts).isoformat(),
                     "open":r["open"],"high":r["high"],"low":r["low"],
                     "close":r["close"],"volume":int(r["volume"])})
    options=[]
    for r in events:
        symbol=str(r.get("symbol") or r.get("raw_symbol") or "").replace(" ","")
        if symbol not in contracts:
            raise ValueError("OPRA event has no original OCC symbol map or unexpected contract")
        if r.get("ts_recv") is None:
            raise ValueError("event lacks actual receive timestamp")
        if len(options)>=MAX_EVENT_ROWS:
            raise ValueError("OPRA event day exceeds size/record safety limit")
        options.append({
            "ts_recv":_timestamp(r["ts_recv"]).isoformat(),"symbol":symbol,
            "schema":"cmbp-1","publisher_id":r.get("publisher_id"),
            "bid_px_00":r.get("bid_px_00"),"ask_px_00":r.get("ask_px_00"),
            "bid_sz_00":r.get("bid_sz_00"),"ask_sz_00":r.get("ask_sz_00"),
        })
    if not options:
        raise ValueError("OPRA returned no true event observations")
    raw_dir.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="event-verified-",dir=raw_dir) as directory:
        stage=Path(directory)
        a=stage/f"raw_spy_bars_{day}.csv"
        b=stage/f"raw_opra_events_{day}.csv"
        _save(a,("ts_start","open","high","low","close","volume"),bars)
        _save(b,("ts_recv","symbol","schema","publisher_id","bid_px_00",
                 "ask_px_00","bid_sz_00","ask_sz_00"),options)
        completed=_bars(a,day)
        parsed,_=_quote_rows(b,day,completed)
        if not parsed:
            raise ValueError("no usable 0DTE NBBO quotes; raw vendor data not published")
        a.replace(raw_dir/a.name)
        b.replace(raw_dir/b.name)
    receipt={
        "day":str(day),"vendor":OPTION_DATASET,"schema":"cmbp-1",
        "estimated_cost_usd":round(total,5),
        "approved_ceiling_usd":max_usd,
        "source_symbols":len(contracts),
        "stock_bars":len(bars),"raw_quote_events":len(options),
        "usable_event_quotes":len(parsed),
        "cost_warning":"Estimate only. Check actual vendor bill; underlying/definitions may incur charges before option quote estimation.",
        "no_live_orders":True,
    }
    (raw_dir/f"vendor_acquisition_{day}.json").write_text(json.dumps(receipt,indent=2)+"\n")
    return receipt


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--day",required=True,type=date.fromisoformat)
    p.add_argument("--max-usd",type=float,default=0)
    p.add_argument("--execute",action="store_true")
    p.add_argument("--raw-dir",type=Path,default=Path("/data/research/forward_raw"))
    p.add_argument("--event-dir",type=Path,default=Path("/data/research/forward_events"))
    p.add_argument("--legacy-dir",type=Path,default=Path("/data/research"))
    p.add_argument("--output-dir",type=Path,default=Path("/data/research/regime_v2_comparison"))
    args=p.parse_args()
    if not args.execute or not args.max_usd>0:
        print("BLOCKED_UNAUTHORIZED_DATA_PURCHASE: requires explicit --execute and --max-usd; no vendor calls",flush=True)
        return
    key=os.getenv("DATABENTO_API_KEY","").strip()
    if not key:
        print("BLOCKED_DATABENTO_KEY_MISSING",flush=True)
        return
    import databento as db
    client=db.Historical(key)
    receipt=acquire_one_day(client,args.day,args.raw_dir,max_usd=args.max_usd)
    verified=ingest(args.raw_dir,args.event_dir,args.day)
    from .regime_reclaim_v2 import run_compare
    report=run_compare(args.event_dir,args.legacy_dir,args.output_dir)
    print("PROSPECTIVE_DATA_PIPELINE "+json.dumps({
        "status":report["status"],"estimated_cost":receipt["estimated_cost_usd"],
        "paired_event_quotes":verified["accepted_true_event_quotes"],
        "no_broker_orders":True,
    },sort_keys=True),flush=True)


if __name__=="__main__":
    main()
