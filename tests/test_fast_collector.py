from datetime import date, datetime, timezone

from engine.providers.webull_free import WebullFreeDataProvider


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class CountingInstrument:
    def __init__(self):
        self.calls = 0

    def list_option_contracts(self, **kwargs):
        self.calls += 1
        target = kwargs["start_date"]
        rows = []
        for strike in range(490, 506):
            for right, code in (("CALL", "C"), ("PUT", "P")):
                rows.append(
                    {
                        "symbol": f"SPY260924{code}{strike:05d}000",
                        "expiration_date": target,
                        "option_type": right,
                        "strike_price": str(strike),
                    }
                )
        return FakeResponse({"data": rows})


class FakeMarketData:
    def get_snapshot(self, symbols, category):
        assert symbols == "SPY"
        assert category == "US_STOCK"
        market_time = datetime(2026, 9, 24, 15, 0, tzinfo=timezone.utc)
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


class CountingOptionMarketData:
    def __init__(self):
        self.requests = []

    def get_option_snapshot(self, symbols, category):
        assert category == "US_OPTION"
        requested = symbols.split(",")
        self.requests.append(requested)
        return FakeResponse({"data": []})


class FakeClient:
    def __init__(self):
        self.instrument = CountingInstrument()
        self.market_data = FakeMarketData()
        self.option_market_data = CountingOptionMarketData()


def test_fast_collector_uses_single_near_atm_batch_and_caches_chain():
    client = FakeClient()
    provider = WebullFreeDataProvider(
        data_client=client,
        active_contract_limit=20,
        contract_refresh_seconds=60,
    )
    observed = datetime(2026, 9, 24, 15, 15, tzinfo=timezone.utc)

    provider.collect_once(date(2026, 9, 24), observed_at=observed)
    provider.collect_once(date(2026, 9, 24), observed_at=observed)

    assert client.instrument.calls == 1
    assert len(client.option_market_data.requests) == 2
    assert all(len(request) == 20 for request in client.option_market_data.requests)

    # The selected set should stay near the current SPY price rather than
    # repeatedly snapshotting the entire 0DTE chain.
    requested_symbols = set(client.option_market_data.requests[0])
    assert any("00500" in symbol for symbol in requested_symbols)
    assert not any("00490" in symbol for symbol in requested_symbols)
