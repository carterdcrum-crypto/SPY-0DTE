"""Fail-closed raw event/underlying ingestion for Regime Reclaim forward tests.

Does not fetch, purchase, subscribe to, or relabel sandbox Webull snapshots.
Raw inputs must originate from independently sourced completed SPY 1-minute
OHLCV and true OPRA CMBP-1/TCBBO event receive-timestamped quotes. Files alone
cannot prove licensed provenance: the manifest keeps checksums + limitations.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import statistics
import tempfile
from datetime import date, datetime, timedelta, timezone, time
from pathlib import Path
from zoneinfo import ZoneInfo

from .greeks import model_greeks_from_quote
from .weekly_options_data import Bar, Quote, load_session, available_sessions, audit, aware
from .session_calendar import session_close

NY = ZoneInfo("America/New_York")
SPY_YEAR_MINUTES = 365 * 24 * 60
MAX_INPUT_BYTES = 140 * 1024 * 1024
MAX_EVENT_ROWS = 500_000


def _timestamp(raw: str) -> datetime:
    raw = str(raw).strip()
    if raw.isdecimal() and len(raw) >= 13:
        # Databento CSV often uses nanoseconds since UNIX epoch.
        value = int(raw)
        seconds = value/1e9 if len(raw) >= 18 else value/1e3
        return datetime.fromtimestamp(seconds, timezone.utc)
    return aware(raw)


def _required(reader, columns, name):
    missing = set(columns) - set(reader.fieldnames or ())
    if missing:
        raise ValueError(f"{name}: required source columns absent: {sorted(missing)}")


def _stream_csv(path: Path):
    if not path.is_file():
        raise FileNotFoundError(f"unavailable independently sourced raw feed: {path.name}")
    if path.stat().st_size <= 0 or path.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError(f"{path.name}: empty or beyond ingestion size cap")
    return path.open("r", newline="", encoding="utf-8")


def _digest(path: Path) -> str:
    h=hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda:handle.read(256*1024),b""):
            h.update(chunk)
    return h.hexdigest()


def _bars(path: Path, day: date):
    data = []
    with _stream_csv(path) as handle:
        reader = csv.DictReader(handle)
        _required(reader, ("ts_start","open","high","low","close","volume"),path.name)
        for record in reader:
            bar=Bar(_timestamp(record["ts_start"]),float(record["open"]),
                    float(record["high"]),float(record["low"]),
                    float(record["close"]),int(record["volume"]))
            if bar.start.astimezone(NY).date() != day:
                raise ValueError("SPY bar belongs to another session")
            if bar.start.second or bar.start.microsecond:
                raise ValueError("SPY bar ts_start must be the full one-minute opening boundary")
            if data and bar.start <= data[-1].start:
                raise ValueError("underlying bars not strictly chronological")
            data.append(bar)
    if len(data) < 36:
        raise ValueError("fewer than 36 completed independent SPY OHLCV bars")
    if not any(b.volume>0 for b in data):
        raise ValueError("SPY OHLCV has no genuine volume observations")
    return data


def bar_features(bars: list[Bar]) -> list[dict]:
    """RVOL and realized volatility strictly from completed bars, no presets."""
    output=[]
    closes=[b.close for b in bars]
    for i,b in enumerate(bars):
        prior_volume=[x.volume for x in bars[max(0,i-20):i]]
        rvol=(b.volume / statistics.fmean(prior_volume)
              if len(prior_volume)>=20 and statistics.fmean(prior_volume)>0 else 0.0)
        window=closes[max(0,i-30):i+1]
        ret=[math.log(a/z) for z,a in zip(window,window[1:]) if a>0 and z>0]
        rv=(statistics.stdev(ret)*math.sqrt(252*390) if len(ret)>=20 else 0.0)
        output.append({
            "ts_start":b.start.isoformat(),"open":b.open,"high":b.high,
            "low":b.low,"close":b.close,"volume":b.volume,
            "volume_ratio":round(rvol,9),
            "realized_volatility":round(rv,9),
        })
    return output


def _quote_rows(path: Path, day: date, bars: list[Bar]):
    """Normalize only OPRA observed-time quote updates; never minute CBBO."""
    import bisect
    cutoff=session_close(day)
    if cutoff is None:
        raise ValueError("date has no verified market session")
    close=datetime.combine(day,cutoff,NY).astimezone(timezone.utc)
    ends=[b.available_at for b in bars]
    spot=[b.close for b in bars]
    quotes=[]
    exclusions={}
    seen={}
    with _stream_csv(path) as handle:
        reader=csv.DictReader(handle)
        _required(reader,("ts_recv","symbol","schema","bid","ask",
                          "bid_size","ask_size"),path.name)
        for number,record in enumerate(reader,1):
            if number>MAX_EVENT_ROWS:
                raise ValueError("too many unbounded OPRA event records for 500MB service")
            schema=record["schema"].strip().lower()
            if schema not in {"cmbp-1","tcbbo"}:
                raise ValueError("unsupported raw quote source; cbbo-1m, snapshots, synthetic or delayed market clock forbidden")
            if schema=="cmbp-1" and record.get("publisher_id","").strip()!="30":
                exclusions["not_consolidated_opra_nbbo"]=exclusions.get("not_consolidated_opra_nbbo",0)+1
                continue
            at=_timestamp(record["ts_recv"])
            if at.astimezone(NY).date()!=day or not datetime.combine(day,time(9,30),NY).astimezone(timezone.utc)<=at<=close:
                exclusions["outside_session"]=exclusions.get("outside_session",0)+1
                continue
            from .providers.databento_history import _parse_contract_from_symbol
            contract=_parse_contract_from_symbol(record["symbol"])
            if contract is None or contract.expiration != day or not contract.symbol.startswith("SPY"):
                exclusions["non_spy_zero_dte"]=exclusions.get("non_spy_zero_dte",0)+1
                continue
            idx=bisect.bisect_right(ends,at)-1
            if idx<0:
                exclusions["no_completed_asof_spy_bar"]=exclusions.get("no_completed_asof_spy_bar",0)+1
                continue
            try:
                bid=float(record["bid"]);ask=float(record["ask"])
                sizebid=int(record["bid_size"]);sizeask=int(record["ask_size"])
                if not(0<bid<=ask and sizebid>0 and sizeask>0):
                    raise ValueError("non-executable market")
                minutes=(close-at).total_seconds()/60.
                if minutes<=0:
                    raise ValueError("after option expiry")
                greeks=model_greeks_from_quote(
                    right=contract.right,bid=bid,ask=ask,spot=spot[idx],
                    strike=contract.strike,minutes_to_expiry=minutes)
            except (ValueError,OverflowError,ZeroDivisionError):
                exclusions["unpriced_or_invalid_asof_quote"]=exclusions.get("unpriced_or_invalid_asof_quote",0)+1
                continue
            key=(at,contract.symbol)
            payload=(
                at.isoformat(),contract.symbol,str(day),contract.right,contract.strike,
                bid,ask,greeks.delta,sizebid,sizeask,
                max(0,int(record.get("volume") or 0)),max(0,int(record.get("open_interest") or 0)),
                schema,greeks.implied_volatility,
            )
            if key in seen:
                if seen[key]!=payload:
                    raise ValueError("conflicting event quote at identical timestamp+option symbol")
                continue
            seen[key]=payload
            quotes.append(payload)
    if not quotes:
        raise ValueError("no eligible independent OPRA event quotes with as-of SPY spot")
    return sorted(quotes),exclusions


BAR_FIELDS=("ts_start","open","high","low","close","volume",
            "volume_ratio","realized_volatility")
OPTION_FIELDS=("observed_at","symbol","expiration","right","strike","bid","ask",
               "delta","bid_size","ask_size","volume","open_interest","schema",
               "implied_volatility")


def ingest(raw_dir: Path, dest: Path, day: date, *, overwrite: bool=False) -> dict:
    """Atomically stage paired sessions only after strict schema verification."""
    raw_bars=raw_dir/f"raw_spy_bars_{day}.csv"
    raw_options=raw_dir/f"raw_opra_events_{day}.csv"
    destination_bars=dest/f"spy_bars_{day}.csv"
    destination_quotes=dest/f"spy_option_quotes_{day}.csv"
    if not overwrite and (destination_bars.exists() or destination_quotes.exists()):
        raise FileExistsError("destination session exists; no silent rewrite of forward observations")
    bars=_bars(raw_bars,day)
    output_bars=bar_features(bars)
    options,skips=_quote_rows(raw_options,day,bars)
    dest.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="forward-verify-",dir=dest) as folder:
        stage=Path(folder)
        for filename,fields,rows in (
            (destination_bars.name,BAR_FIELDS,output_bars),
            (destination_quotes.name,OPTION_FIELDS,options),
        ):
            with (stage/filename).open("w",newline="") as handle:
                writer=csv.writer(handle) if filename==destination_quotes.name else csv.DictWriter(handle,fields)
                writer.writerow(fields) if filename==destination_quotes.name else writer.writeheader()
                writer.writerows(rows) if filename==destination_quotes.name else writer.writerows(rows)
        # Reload exact final-format data and fail before publishing partial files.
        checked=load_session(stage,day)
        if len(checked.bars)!=len(output_bars) or len(checked.quotes)!=len(options):
            raise ValueError("roundtrip strict event loader mismatch")
        (stage/destination_bars.name).replace(destination_bars)
        (stage/destination_quotes.name).replace(destination_quotes)
    manifest={
        "trade_date":str(day),
        "raw_underlying_sha256":_digest(raw_bars),
        "raw_opra_events_sha256":_digest(raw_options),
        "completed_spy_ohlcv_bars":len(bars),
        "accepted_true_event_quotes":len(options),
        "excluded_source_rows":skips,
        "derived_features":"as-of 20-previous-volume RVOL; 30-completed-return annualized RV; option bid/ask Black-Scholes approximate IV/delta using last completed SPY bar",
        "source_provenance_warning":"CSV input labels/checksums do not independently authenticate vendor, exchange feed permission or execution quality; OPRA source must be verified out of band.",
        "not_broker_fills":True,"paid_provider_called":False,
        "source_schema":"OPRA cmbp-1 NBBO publisher 30 or tcbbo",
    }
    (dest/f"spy_event_provenance_{day}.json").write_text(json.dumps(manifest,indent=2,sort_keys=True)+"\n")
    return manifest


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--trade-date",required=True,type=date.fromisoformat)
    p.add_argument("--raw-dir",type=Path,default=Path("/data/research/forward_raw"))
    p.add_argument("--event-dir",type=Path,default=Path("/data/research/forward_events"))
    p.add_argument("--overwrite",action="store_true")
    p.add_argument("--compare",action="store_true")
    p.add_argument("--legacy-dir",type=Path,default=Path("/data/research"))
    p.add_argument("--output-dir",type=Path,default=Path("/data/research/regime_v2_comparison"))
    args=p.parse_args()
    try:
        report=ingest(args.raw_dir,args.event_dir,args.trade_date,overwrite=args.overwrite)
        if args.compare:
            from .regime_reclaim_v2 import run_compare
            result=run_compare(args.event_dir,args.legacy_dir,args.output_dir)
            report["comparison_status"]=result["status"]
        print("FORWARD_EVENT_INGEST "+json.dumps(report,sort_keys=True),flush=True)
    except (FileNotFoundError,ValueError,FileExistsError) as exc:
        print("BLOCKED_FORWARD_EVENT_INGEST "+str(exc),flush=True)
        raise SystemExit(2) from exc


if __name__=="__main__":
    main()
