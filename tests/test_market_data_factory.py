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



def test_webull_production_feed_requires_live_not_sandbox_credentials(monkeypatch):
    monkeypatch.setenv("MARKET_DATA_PROVIDER", "webull-production")
    monkeypatch.setenv("WEBULL_APP_KEY", "sandbox-key")
    monkeypatch.setenv("WEBULL_APP_SECRET", "sandbox-secret")
    monkeypatch.delenv("WEBULL_LIVE_APP_KEY", raising=False)
    monkeypatch.delenv("WEBULL_LIVE_APP_SECRET", raising=False)
    try:
        provider_from_env()
    except RuntimeError as exc:
        assert "WEBULL_LIVE_APP_KEY" in str(exc)
        assert "sandbox" not in str(exc)
    else:
        raise AssertionError("Production feed must not silently fall back to sandbox credentials")


def test_webull_production_market_data_uses_existing_live_api_keys(monkeypatch, tmp_path):
    import sys
    from types import ModuleType, SimpleNamespace
    from engine.market_data_factory import provider_source

    captured = {}
    class MockApiClient:
        def __init__(self, key, secret, region):
            captured["credentials"] = (key, secret, region)
        def set_stream_logger(self, **kwargs):
            pass
        def add_endpoint(self, region, host):
            captured["endpoint"] = (region, host)
        def set_token_dir(self, path):
            captured["token_dir"] = path

    class MockDataClient:
        def __init__(self, client):
            self.instrument = SimpleNamespace()
            captured["sdk_client"] = client

    monkeypatch.setitem(sys.modules, "webull", ModuleType("webull"))
    monkeypatch.setitem(sys.modules, "webull.core", ModuleType("webull.core"))
    monkeypatch.setitem(sys.modules, "webull.data", ModuleType("webull.data"))
    core = ModuleType("webull.core.client")
    core.ApiClient = MockApiClient
    data = ModuleType("webull.data.data_client")
    data.DataClient = MockDataClient
    monkeypatch.setitem(sys.modules, "webull.core.client", core)
    monkeypatch.setitem(sys.modules, "webull.data.data_client", data)

    monkeypatch.setenv("MARKET_DATA_PROVIDER", "webull-production")
    monkeypatch.setenv("WEBULL_APP_KEY", "sandbox-key")
    monkeypatch.setenv("WEBULL_APP_SECRET", "sandbox-secret")
    monkeypatch.setenv("WEBULL_LIVE_APP_KEY", "live-key")
    monkeypatch.setenv("WEBULL_LIVE_APP_SECRET", "live-secret")
    monkeypatch.setenv("WEBULL_OPENAPI_TOKEN_DIR", str(tmp_path / "token-cache"))

    collector = provider_from_env()
    assert provider_source(collector) == "webull_production"
    assert captured["credentials"] == ("live-key", "live-secret", "us")
    assert captured["endpoint"] == ("us", "api.webull.com")
    assert captured["token_dir"] == str(tmp_path / "token-cache")
    assert (tmp_path / "token-cache").is_dir()
