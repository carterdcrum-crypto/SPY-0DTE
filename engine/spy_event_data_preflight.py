"""Zero-dollar, metadata-only readiness and vendor price audit for SPY 0DTE.

Never call Databento timeseries, batch.submit_job, or create an order.
No monetary amount is authorized by this script. Cost quotes are NOT billing.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from .burst_research import _research_files
from .data import load_canonical_path
from .weekly_options_data import audit as event_audit

NY = ZoneInfo("America/New_York")
RAW_SUFFIXES = (".dbn", ".dbn.zst", ".dbn.zstd", ".parquet", ".jsonl", ".feather")


def present_files(root: Path) -> dict:
    """Inventory data without altering or transmitting licensed vendor bytes."""
    paths = sorted(p for p in root.rglob("*") if p.is_file())
    raw = [p for p in paths if p.name.lower().endswith(RAW_SUFFIXES)]
    seen = [p for p in paths if p.name.startswith((
        "spy_bars_", "spy_option_quotes_", "spy_pcr_",
    )) and p.suffix in (".gz", ".csv")]
    return {
        "legacy_canonical_files": len(_research_files(root)),
        "raw_vendor_artifact_count": len(raw),
        "raw_vendor_artifacts": [
            {"relative_path": str(p.relative_to(root)), "bytes": p.stat().st_size}
            for p in raw[:50]
        ],
        "direct_event_bar_pcr_files": sorted(str(p.relative_to(root)) for p in seen[:100]),
        "strict_event_data_readiness": event_audit(root),
    }


def _sample_dates(root: Path) -> list[date]:
    all_days = []
    for path in _research_files(root):
        try:
            all_days.append(date.fromisoformat(path.name[9:19]))
        except ValueError:
            continue
    if not all_days:
        return []
    # Earliest, median and latest are NOT untouched holdout test periods.
    days = sorted(set(all_days))
    return sorted(set((days[0], days[len(days)//2], days[-1])))


def _candidate_contracts(root: Path, day: date) -> list[str]:
    path = next((p for p in _research_files(root)
                 if day.isoformat() in p.name), None)
    if path is None:
        return []
    frames = load_canonical_path(path)
    if not frames:
        return []
    # The archival list selects representative quote budget scopes, NEVER
    # backtests non-causally selected symbols; it is for pricing only.
    seen = {}
    for frame in frames[:45]:
        for q in frame.options:
            if (q.bid > 0 and q.ask > q.bid and
                    abs(abs(q.delta)-.5) <= .2 and q.spread_fraction <= .2):
                symbol=q.symbol.replace(" ","")
                if symbol.startswith("SPY"):
                    seen.setdefault(symbol, (abs(abs(q.delta)-.5),
                                            q.spread_fraction, q.ask))
    return [s for s,_ in sorted(seen.items(),key=lambda x: (x[1],x[0]))]


def metadata_costs(root: Path, client, sample_days: list[date]) -> list[dict]:
    """Read ONLY free historical metadata pricing endpoints."""
    result=[]
    for day in sample_days:
        start=datetime.combine(day,time(9,30),NY).isoformat()
        end=datetime.combine(day,time(16,0),NY).isoformat()
        symbols=_candidate_contracts(root,day)
        quotes={
            "spy_ohlcv_1m":dict(dataset="EQUS.MINI",schema="ohlcv-1m",
                                 symbols=["SPY"],start=start,end=end),
            "full_spy_chain_trades_for_asof_pcr":dict(dataset="OPRA.PILLAR",
                schema="trades",symbols="SPY.OPT",stype_in="parent",start=start,end=end),
            "full_spy_chain_event_nbbo":dict(dataset="OPRA.PILLAR",
                schema="cmbp-1",symbols="SPY.OPT",stype_in="parent",start=start,end=end),
        }
        for limit in (12,24,48):
            if len(symbols) >= limit:
                quotes[f"event_nbbo_{limit}_listed_options"]=dict(
                    dataset="OPRA.PILLAR",schema="cmbp-1",
                    symbols=symbols[:limit],stype_in="raw_symbol",start=start,end=end)
        prices={}
        for name,params in quotes.items():
            try:
                quoted=float(client.metadata.get_cost(**params))
                prices[name]={"estimated_usd":round(quoted,4),"symbols_priced":
                              len(params["symbols"]) if isinstance(params["symbols"],list)
                              else params["symbols"]}
            except Exception as exc:
                prices[name]={"error_type":type(exc).__name__,
                              "error_detail":str(exc)[:280]}
        result.append({"day":day.isoformat(),"listed_liquid_symbol_candidates":len(symbols),
                      "pricing_estimates":prices})
    return result


def execute(root: Path, output: Path, client=None) -> dict:
    data=present_files(root)
    days=_sample_dates(root)
    report={
        "study":"SPY 0DTE event-observed data + full-chain as-of PCR acquisition",
        "actions":"free metadata + local inventory ONLY",
        "data_purchased_usd":0.0, "data_downloaded_bytes":0,
        "no_orders_or_live_changes":True,
        "existing_archive":data,
        "sampled_cost_dates":[d.isoformat() for d in days],
        "sampled_price_quotes":None,
        "ready_for_full_five_factor_backtest":False,
        "readiness_blockers":[],
        "historical_vendor_cost_estimate_not_spend":True,
    }
    if client is None and os.getenv("DATABENTO_API_KEY"):
        try:
            import databento
            client=databento.Historical(os.environ["DATABENTO_API_KEY"])
        except Exception as exc:
            report["metadata_client_error"]=f"{type(exc).__name__}:{str(exc)[:200]}"
    if client is not None:
        report["sampled_price_quotes"]=metadata_costs(root,client,days)
    else:
        report["readiness_blockers"].append("DATABENTO_API_KEY_NOT_AVAILABLE_FOR_FREE_METADATA_QUOTES")
    readiness=data["strict_event_data_readiness"]
    if not readiness.get("timestamped_option_quote_files"):
        report["readiness_blockers"].append("NO_EVENT_OBSERVED_OCC_NBBO_FILES")
    if not list(root.glob("spy_pcr_*.csv*")):
        report["readiness_blockers"].append("NO_POINT_IN_TIME_COMPLETE_SPY_CHAIN_VOLUME_PCR")
    if not list(root.glob("spy_bars_*.csv*")):
        report["readiness_blockers"].append("NO_GENUINE_FULL_SPY_1M_OHLCV_FILES")
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,indent=2,sort_keys=True)+"\n",encoding="utf8")
    return report


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument("--data-dir",type=Path,default=Path("/data/research"))
    parser.add_argument("--output",type=Path,default=Path("/data/research/event_nbbo_price_audit.json"))
    args=parser.parse_args()
    result=execute(args.data_dir,args.output)
    summarized={
        k:result[k] for k in (
            "data_purchased_usd","data_downloaded_bytes",
            "sampled_cost_dates","sampled_price_quotes",
            "readiness_blockers","ready_for_full_five_factor_backtest")
    }
    summarized["existing_archive_summary"]={
        "legacy_canonical_files":result["existing_archive"]["legacy_canonical_files"],
        "raw_vendor_artifact_count":result["existing_archive"]["raw_vendor_artifact_count"],
        "direct_event_bar_pcr_files":len(result["existing_archive"]["direct_event_bar_pcr_files"]),
        "strict_event_data_readiness":result["existing_archive"]["strict_event_data_readiness"],
    }
    print("SPY EVENT DATA COST/PREFLIGHT "+json.dumps(summarized,sort_keys=True),flush=True)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
