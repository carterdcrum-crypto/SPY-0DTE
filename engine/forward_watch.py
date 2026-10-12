"""Long-running ZERO-PURCHASE V1/V2 forward comparison on real event CSVs.

Designed for the existing isolated /data/research Railway volume. It watches
for paired, independently sourced raw SPY OHLCV + OPRA event CSVs, validates
them and recomputes the same unseen-day V1/V2 comparisons on each ingest.
Never polls Webull, invokes Databento, or places broker orders.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

from .forward_event_ingest import ingest
from .frozen_regime_forward import latest_legacy_day
from .regime_reclaim_v3 import run_v3_comparison


def process_once(root: Path) -> dict:
    root.mkdir(parents=True,exist_ok=True)
    raw=root/"forward_raw"
    paired=root/"forward_events"
    output=root/"regime_v2_comparison"
    output.mkdir(parents=True,exist_ok=True)
    failures={}
    converted=[]
    try:
        cutoff=latest_legacy_day(root)
    except ValueError as e:
        status={
            "status":"BLOCKED_NO_INSPECTED_ARCHIVE_CUTOFF",
            "detail":str(e),"imported_days":[],
            "raw_feed_missing":True,"broker_orders":0,
            "checked_at":datetime.now(timezone.utc).isoformat(),
        }
        (output/"watch_status.json").write_text(json.dumps(status,indent=2)+"\n")
        return status
    for bar in sorted(raw.glob("raw_spy_bars_????-??-??.csv")):
        name=bar.name
        try:
            day=date.fromisoformat(name[13:23])
        except ValueError:
            continue
        if day<=cutoff:
            continue
        quotes=raw/f"raw_opra_events_{day}.csv"
        if not quotes.exists():
            failures[str(day)]="MISSING_TRUE_EVENT_OPRA_QUOTE_FILE"
            continue
        target_bars=paired/f"spy_bars_{day}.csv"
        target_opts=paired/f"spy_option_quotes_{day}.csv"
        if target_bars.exists() and target_opts.exists():
            continue
        if target_bars.exists() or target_opts.exists():
            failures[str(day)]="INCOMPLETE_PREVIOUS_PAIRED_OUTPUT"
            continue
        try:
            ingest(raw,paired,day)
            converted.append(str(day))
        except (ValueError,FileNotFoundError,OSError) as exc:
            failures[str(day)]=f"{type(exc).__name__}: {exc}"
    try:
        result=run_v3_comparison(paired,root,output)
        outcome=result["status"]
        processed=len(result["processed"])
    except (ValueError,FileNotFoundError,KeyError) as exc:
        outcome="BLOCKED_FORWARD_REPLAY_VALIDATION"
        processed=0
        failures["comparison"]=f"{type(exc).__name__}: {exc}"
    status={
        "status":outcome,
        "checked_at":datetime.now(timezone.utc).isoformat(),
        "archive_last_inspected_date":str(cutoff),
        "imported_days":converted,"ingestion_errors":failures,
        "processed_sessions":processed,
        "raw_feed_missing":not bool(list(raw.glob("raw_opra_events_????-??-??.csv"))),
        "data_purchases":0,"broker_orders":0,
    }
    (output/"watch_status.json").write_text(json.dumps(status,indent=2,sort_keys=True)+"\n")
    return status


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--data-dir",type=Path,default=Path(os.environ.get("RESEARCH_DATA_DIR","/data/research")))
    p.add_argument("--once",action="store_true")
    x=p.parse_args()
    lock=x.data_dir/".forward-watch.lock"
    x.data_dir.mkdir(parents=True,exist_ok=True)
    with lock.open("a") as h:
        try:
            fcntl.flock(h,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("A forward data writer is already running for this research volume")
        seconds=max(60,int(os.environ.get("FORWARD_WATCH_POLL_SECONDS","300")))
        while True:
            try:
                state=process_once(x.data_dir)
                print("FORWARD_WATCH "+json.dumps(state,sort_keys=True),flush=True)
            except Exception as exc:
                print("FORWARD_WATCH_FAIL_CLOSED "+type(exc).__name__+": "+str(exc),
                      file=sys.stderr,flush=True)
                if x.once:
                    raise
            if x.once:
                return
            time.sleep(seconds)


if __name__=="__main__":
    main()
