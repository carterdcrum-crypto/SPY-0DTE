"""Data sourcing must never accidentally consume Databento billable data."""
from datetime import date
from pathlib import Path

from engine.spy_event_data_preflight import execute, metadata_costs, present_files, _to_opra_symbol


class FakeMetadata:
    def __init__(self):
        self.calls = []

    def get_cost(self, **params):
        self.calls.append(params)
        return 4.1234


class NoDataDownloads:
    def __init__(self):
        self.metadata = FakeMetadata()
        self.timeseries = None


def test_preflight_empty_archive_does_not_download(tmp_path):
    stub = NoDataDownloads()
    report = execute(tmp_path, tmp_path / "report.json", client=stub)
    assert report["data_purchased_usd"] == 0.0
    assert report["data_downloaded_bytes"] == 0
    assert report["ready_for_full_five_factor_backtest"] is False
    assert stub.metadata.calls == []
    assert (tmp_path / "report.json").exists()
    assert "NO_EVENT_OBSERVED_OCC_NBBO_FILES" in report["readiness_blockers"]


def test_cost_quote_requests_use_only_free_metadata(monkeypatch, tmp_path):
    stub = NoDataDownloads()
    monkeypatch.setattr("engine.spy_event_data_preflight._candidate_contracts",
                        lambda root, day: [f"SPY26C{i:08d}" for i in range(60)])
    quotes = metadata_costs(tmp_path, stub, [date(2026, 10, 6)])
    assert quotes[0]["pricing_estimates"]["full_spy_chain_trades_for_asof_pcr"]["estimated_usd"] == 4.1234
    assert len(stub.metadata.calls) == 11
    assert all(x["dataset"] in ("OPRA.PILLAR", "EQUS.MINI") for x in stub.metadata.calls)
    assert all(x["schema"] in ("cmbp-1", "ohlcv-1m", "trades", "tcbbo") for x in stub.metadata.calls)
    assert _to_opra_symbol("SPY260401C00654000")=="SPY   260401C00654000"
    assert stub.metadata.calls[-1]["symbols"][0].startswith("SPY   ")
