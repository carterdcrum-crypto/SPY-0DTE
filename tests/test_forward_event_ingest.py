"""Strict forward event capture: no CBBO-minute substitution."""
from datetime import date, datetime, timedelta, timezone
import csv
import json
import pytest

from engine.forward_event_ingest import ingest, _timestamp
from engine.weekly_options_data import load_session

D=date(2026,10,12)
T=datetime(2026,10,12,13,30,tzinfo=timezone.utc)


def inputs(tmp_path,schema="cmbp-1",publisher="30"):
    root=tmp_path/"raw"
    root.mkdir()
    with (root/f"raw_spy_bars_{D}.csv").open("w",newline="") as h:
        w=csv.writer(h)
        w.writerow(("ts_start","open","high","low","close","volume"))
        for i in range(50):
            w.writerow(((T+timedelta(minutes=i)).isoformat(),600,600,600,600,1000))
    with (root/f"raw_opra_events_{D}.csv").open("w",newline="") as h:
        w=csv.writer(h)
        w.writerow(("ts_recv","symbol","schema","publisher_id","bid_px_00","ask_px_00","bid_sz_00","ask_sz_00"))
        w.writerow(((T+timedelta(minutes=40,seconds=17)).isoformat(),
                    "SPY261012C00600000",schema,publisher,1.0,1.02,40,50))
    return root


def test_real_event_format_only_can_populate_paired_csv(tmp_path):
    raw=inputs(tmp_path)
    dest=tmp_path/"events"
    report=ingest(raw,dest,D)
    assert report["accepted_true_event_quotes"]==1
    assert report["completed_spy_ohlcv_bars"]==50
    assert report["paid_provider_called"] is False
    assert len(report["raw_opra_events_sha256"])==64
    session=load_session(dest,D)
    assert len(session.bars)==50
    assert len(session.quotes)==1
    assert session.quotes[0].symbol=="SPY261012C00600000"
    with (dest/f"spy_option_quotes_{D}.csv").open() as h:
        opts=list(csv.DictReader(h))
    assert opts[0]["schema"]=="cmbp-1"
    assert float(opts[0]["implied_volatility"])>0
    assert 0 < float(opts[0]["delta"]) < 1
    with (dest/f"spy_bars_{D}.csv").open() as h:
        bars=list(csv.DictReader(h))
    assert float(bars[19]["volume_ratio"])==0.0
    assert float(bars[20]["volume_ratio"])==1.0
    assert float(bars[35]["realized_volatility"])==0.0
    assert json.loads((dest/f"spy_event_provenance_{D}.json").read_text())["trade_date"]==str(D)
    with pytest.raises(FileExistsError):
        ingest(raw,dest,D)


def test_cbbo_minute_bucket_rejected(tmp_path):
    raw=inputs(tmp_path,schema="cbbo-1m")
    with pytest.raises(ValueError,match="cbbo-1m"):
        ingest(raw,tmp_path/"event",D)


def test_nonconsolidated_publisher_rejected(tmp_path):
    raw=inputs(tmp_path,publisher="20")
    with pytest.raises(ValueError,match="no eligible independent OPRA"):
        ingest(raw,tmp_path/"event",D)


def test_timestamp_requires_true_aware_event_clock():
    assert _timestamp("1791812400000000000")==datetime.fromtimestamp(1791812400,timezone.utc)
    with pytest.raises(ValueError,match="timezone"):
        _timestamp("2026-10-12T14:10:12")
