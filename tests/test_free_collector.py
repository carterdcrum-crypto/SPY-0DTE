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
        market_time = datetime(2026, 9, 22, 15, 15, tzinfo=timezone.utc)
        return FakeResponse(
            {
                "data": [
                    {
                        "symbol": "SPY",
                        "last_price": "500.00",
                        "last_trade_time": int(market_time.timestamp() * 1000),
                    }
                ]
            }
        )


class FakeOptionMarketData:
    def get_option_snapshot(self, symbols, category):
        assert category == "US_OPTION"
        requested = set(symbols.split(","))
        rows = []
        if "SPY260922C00500000" in requested:
            # Direct quote timestamp should win over the underlying anchor.
            rows.append(
                {
                    "symbol": "SPY260922C00500000",
                    "bid": "1.00",
                    "ask": "1.20",
                    "bid_size": 12,
                    "ask_size": 15,
                    "volume": 100,
                    "open_interest": 1000,
                    "quote_time": "2026-09-22T15:14:59Z",
                }
            )
        if "SPY260922P00500000" in requested:
            # No option timestamp: provider must use SPY's last-trade timestamp.
            rows.append(
                {
                    "symbol": "SPY260922P00500000",
                    "bid": "1.05",
                    "ask": "1.25",
                    "volume": 80,
                    "open_interest": 900,
                }
            )
        return FakeResponse({"data": rows})


class FakeClient:
    def __init__(self):
        self.instrument = FakeInstrument()
        self.market_data = FakeMarketData()
        self.option_market_data = FakeOptionMarketData()


def test_free_provider_filters_to_zero_dte_computes_greeks_and_preserves_market_time():
    provider = WebullFreeDataProvider(data_client=FakeClient())
    received = datetime(2026, 9, 22, 15, 30, tzinfo=timezone.utc)
    rows = provider.collect_once(date(2026, 9, 22), observed_at=received)

    assert len(rows) == 2
    assert {row.right for row in rows} == {"call", "put"}
    assert all(row.source == "webull_sandbox_delayed" for row in rows)
    assert all(row.greeks is not None for row in rows)
    assert all(row.minutes_to_expiry > 0 for row in rows)
    assert all(row.expiration == date(2026, 9, 22) for row in rows)
    assert all(row.received_at == received for row in rows)

    call = next(row for row in rows if row.right == "call")
    put = next(row for row in rows if row.right == "put")

    assert call.timestamp == datetime(2026, 9, 22, 15, 14, 59, tzinfo=timezone.utc)
    assert call.timestamp_quality == "option_quote_timestamp"
    assert call.feed_delay_seconds == 901
    assert call.bid_size == 12
    assert call.ask_size == 15

    assert put.timestamp == datetime(2026, 9, 22, 15, 15, tzinfo=timezone.utc)
    assert put.timestamp_quality == "underlying_trade_anchor"
    assert put.feed_delay_seconds == 900
    # Expiry is 20:00 UTC in September, so using the delayed market clock gives
    # 285 minutes remaining. Using receive time would incorrectly give 270.
    assert put.minutes_to_expiry == 285


def test_snapshot_store_persists_provenance_and_deduplicates(tmp_path):
    provider = WebullFreeDataProvider(data_client=FakeClient())
    received = datetime(2026, 9, 22, 15, 30, tzinfo=timezone.utc)
    store = SnapshotStore(tmp_path / "spy0dte.sqlite")
    try:
        first = collect_and_store(provider, store, date(2026, 9, 22), observed_at=received)
        second = collect_and_store(provider, store, date(2026, 9, 22), observed_at=received)
        assert len(first) == 2
        assert len(second) == 2
        assert store.count() == 2
        assert store.timestamp_quality_counts() == {
            "option_quote_timestamp": 1,
            "underlying_trade_anchor": 1,
        }

        exported = tmp_path / "snapshots.csv"
        assert store.export_csv(exported) == 2
        text = exported.read_text()
        assert "webull_sandbox_delayed" in text
        assert "SPY260922C00500000" in text
        assert "received_at" in text
        assert "timestamp_quality" in text
        assert "feed_delay_seconds" in text
    finally:
        store.close()
