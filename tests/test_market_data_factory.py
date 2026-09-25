from __future__ import annotations

from engine.market_data_factory import provider_from_env
from engine.providers.tradier_realtime import TradierRealtimeDataProvider


def test_auto_uses_primary_tradier_access_token(monkeypatch):
    monkeypatch.setenv("MARKET_DATA_PROVIDER", "auto")
    monkeypatch.setenv("TRADIER_ACCESS_TOKEN", "production-token")
    monkeypatch.delenv("TRADIER_MARKET_DATA_TOKEN", raising=False)

    provider = provider_from_env()

    assert isinstance(provider, TradierRealtimeDataProvider)
    assert provider.token == "production-token"


def test_market_data_specific_token_overrides_primary_token(monkeypatch):
    monkeypatch.setenv("MARKET_DATA_PROVIDER", "tradier")
    monkeypatch.setenv("TRADIER_ACCESS_TOKEN", "account-token")
    monkeypatch.setenv("TRADIER_MARKET_DATA_TOKEN", "market-token")

    provider = provider_from_env()

    assert isinstance(provider, TradierRealtimeDataProvider)
    assert provider.token == "market-token"


def test_tradier_mode_fails_closed_without_production_token(monkeypatch):
    monkeypatch.setenv("MARKET_DATA_PROVIDER", "tradier")
    monkeypatch.delenv("TRADIER_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("TRADIER_MARKET_DATA_TOKEN", raising=False)

    try:
        provider_from_env()
    except RuntimeError as exc:
        assert "TRADIER_ACCESS_TOKEN" in str(exc)
    else:
        raise AssertionError("missing Tradier token should fail closed")
