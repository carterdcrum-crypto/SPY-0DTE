"""Auto-watch ingest on the research volume is read-only except report/approved data."""
import json
from engine.forward_watch import process_once


def test_forward_watch_blocks_when_archive_missing(tmp_path):
    x=process_once(tmp_path)
    assert x["status"]=="BLOCKED_NO_INSPECTED_ARCHIVE_CUTOFF"
    assert x["broker_orders"]==0
    assert (tmp_path/"regime_v2_comparison"/"watch_status.json").is_file()


def test_watch_with_archive_but_no_true_quotes_blocks_no_fake_results(tmp_path):
    (tmp_path/"spy_0dte_2026-10-08.csv.gz").write_bytes(b"historical inspected")
    x=process_once(tmp_path)
    assert x["status"]=="BLOCKED_NO_NEW_EVENT_SESSIONS"
    assert x["processed_sessions"]==0
    assert x["raw_feed_missing"]
    manifest=json.loads((tmp_path/"regime_v2_comparison"/"comparison_manifest.json").read_text())
    assert manifest["results"]["v1_frozen"]["win_rate_pct"] is None
    assert manifest["results"]["v2_router_adaptive_exit"]["win_rate_pct"] is None


def test_partially_arrived_vendor_day_not_falsely_published(tmp_path):
    (tmp_path/"spy_0dte_2026-10-08.csv.gz").write_bytes(b"historical inspected")
    raw=tmp_path/"forward_raw"
    raw.mkdir()
    (raw/"raw_spy_bars_2026-10-12.csv").write_text("incomplete")
    x=process_once(tmp_path)
    assert x["status"]=="BLOCKED_NO_NEW_EVENT_SESSIONS"
    assert x["ingestion_errors"]["2026-10-12"]=="MISSING_TRUE_EVENT_OPRA_QUOTE_FILE"
    assert not (tmp_path/"forward_events"/"spy_bars_2026-10-12.csv").exists()
