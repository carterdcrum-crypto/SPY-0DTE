from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from engine.providers.tradier_realtime import TradierRealtimeDataProvider


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, now_ms: int):
        self.now_ms = now_ms
        self.calls = []

    def get(self, url, *, headers, params, timeout):
        self.calls.append((url, dict(params)))
        if url.endswith("/markets/options/chains"):
            return FakeResponse(
                {
                    "options": {
                        "option": [
                            {
                                "symbol": "SPY260924C00767000",
                                "expiration_date": "2026-09-24",
                                "strike": 767.0,
                                "option_type": "call",
                            },
                            {
                                "symbol": "SPY260924P00767000",
                                "expiration_date": "2026-09-24",
                                "strike": 767.0,
                                "option_type": "put",
                            },
                        ]
                    }
                }
            )
        symbols = str(params["symbols"]).split(",")
        rows = []
        for symbol in symbols:
            if symbol == "SPY":
                rows.append(
                    {
                        "symbol": "SPY",
                        "bid": 766.99,
                        "ask": 767.01,
                        "bid_date": self.now_ms - 250,
                        "ask_date": self.now_ms - 200,
                        "trade_date": self.now_ms - 300,
                        "volume": 1000000,
                    }
                )
            elif "C00767000" in symbol:
                rows.append(
                    {
                        "symbol": symbol,
                        "bid": 0.48,
                        "ask": 0.50,
                        "bidsize": 25,
                        "asksize": 30,
                        "bid_date": self.now_ms - 300,
                        "ask_date": self.now_ms - 250,
                        "volume": 2500,
                        "open_interest": 8000,
                    }
                )
            else:
                rows.append(
                    {
                        "symbol": symbol,
                        "bid": 0.45,
                        "ask": 0.47,
                        "bidsize": 20,
                        "asksize": 20,
                        "bid_date": self.now_ms - 350,
                        "ask_date": self.now_ms - 300,
                        "volume": 2200,
                        "open_interest": 7500,
                    }
                )
        value = rows[0] if len(rows) == 1 else rows
        return FakeResponse({"quotes": {"quote": value}})


def test_tradier_provider_emits_realtime_timestamped_snapshots():
    now = datetime(2026, 9, 24, 15, 0, tzinfo=timezone.utc)
    session = FakeSession(int(now.timestamp() * 1000))
    provider = TradierRealtimeDataProvider(
        "prod-token",
        session=session,
        active_contract_limit=2,
        contract_refresh_seconds=60,
    )

    snapshots = provider.collect_once(date(2026, 9, 24), observed_at=now)

    assert len(snapshots) == 2
    assert all(item.source == "tradier_realtime" for item in snapshots)
    assert all(item.timestamp_quality == "tradier_nbbo_timestamp" for item in snapshots)
    assert max(item.feed_delay_seconds for item in snapshots) < 1.0
    assert all(item.bid_size > 0 and item.ask_size > 0 for item in snapshots)
    assert any(item.right == "call" for item in snapshots)
    assert any(item.right == "put" for item in snapshots)
    assert len(session.calls) == 3  # bootstrap SPY quote, chain, then batched SPY+options


def test_tradier_provider_rejects_unauthorized_production_token():
    class UnauthorizedSession:
        def get(self, *args, **kwargs):
            return FakeResponse({}, status_code=401)

    provider = TradierRealtimeDataProvider("bad-token", session=UnauthorizedSession())
    now = datetime(2026, 9, 24, 15, 0, tzinfo=timezone.utc)
    with pytest.raises(RuntimeError, match="unauthorized"):
        provider.collect_once(date(2026, 9, 24), observed_at=now)
