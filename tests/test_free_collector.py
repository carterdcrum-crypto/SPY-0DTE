from datetime import date, datetime, timezone

from engine.collector import SnapshotStore, collect_and_store
from engine.providers.webull_free import WebullFreeDataProvider


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class FakeInstrument:
    def list_option_contracts(self, **kwargs):
        target = kwargs["start_date"]
        return FakeResponse(
            {
                "data": [
                    {"symbol": "SPY260922C00500000", "expiration_date": target, "option_type": "CALL", "strike_price": "500"},
                    {"symbol": "SPY260922P00500000", "expiration_date": target, "option_type": "PUT", "strike_price": "500"},
                    {"symbol": "SPY260923C00500000", "expiration_date": "2026-09-23", "option_type": "CALL", "strike_price": "500"},
                ]
            }
        )


class FakeMarketData:
    def get_snapshot(self, symbols, category):
        assert symbols == "SPY"
        assert category == "US_STOCK"
        return FakeResponse({"data": [{"symbol": "SPY", "last_price": "500.00"}]})


class FakeOptionMarketData:
    def get_option_snapshot(self, symbols, category):
        assert category == "US_OPTION"
        requested = set(symbols.split(","))
        rows = []
        if "SPY260922C00500000" in requested:
            rows.append({"symbol": "SPY260922C00500000", "bid": "1.00", "ask": "1.20", "volume": 100, "open_interest": 1000})
        if "SPY260922P00500000" in requested:
            rows.append({"symbol": "SPY260922P00500000", "bid": "1.05", "ask": "1.25", "volume": 80, "open_interest": 900})
        return FakeResponse({"data": rows})


class FakeClient:
    def __init__(self):
        self.instrument = FakeInstrument()
        self.market_data = FakeMarketData()
        self.option_market_data = FakeOptionMarketData()


def test_free_provider_filters_to_zero_dte_and_computes_greeks():
    provider = WebullFreeDataProvider(data_client=FakeClient())
    observed = datetime(2026, 9, 22, 15, 30, tzinfo=timezone.utc)
    rows = provider.collect_once(date(2026, 9, 22), observed_at=observed)

    assert len(rows) == 2
    assert {row.right for row in rows} == {"call", "put"}
    assert all(row.source == "webull_sandbox_delayed" for row in rows)
    assert all(row.greeks is not None for row in rows)
    assert all(row.minutes_to_expiry > 0 for row in rows)
    assert all(row.expiration == date(2026, 9, 22) for row in rows)


def test_snapshot_store_persists_provenance_and_deduplicates(tmp_path):
    provider = WebullFreeDataProvider(data_client=FakeClient())
    observed = datetime(2026, 9, 22, 15, 30, tzinfo=timezone.utc)
    store = SnapshotStore(tmp_path / "spy0dte.sqlite")
    try:
        first = collect_and_store(provider, store, date(2026, 9, 22), observed_at=observed)
        second = collect_and_store(provider, store, date(2026, 9, 22), observed_at=observed)
        assert len(first) == 2
        assert len(second) == 2
        assert store.count() == 2

        exported = tmp_path / "snapshots.csv"
        assert store.export_csv(exported) == 2
        text = exported.read_text()
        assert "webull_sandbox_delayed" in text
        assert "SPY260922C00500000" in text
    finally:
        store.close()
