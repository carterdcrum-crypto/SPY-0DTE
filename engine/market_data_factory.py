from __future__ import annotations

import os
from dataclasses import replace
from typing import Any

from .providers.tradier_realtime import TradierRealtimeDataProvider
from .providers.webull_free import WebullFreeDataProvider
from .webull import PRODUCTION_API_HOST


class _SourceOverrideProvider:
    """Preserve an existing provider while making its data provenance explicit."""

    def __init__(self, provider: Any, source: str) -> None:
        self.provider = provider
        self.source = source

    def collect_once(self, trade_date, *, observed_at=None):  # noqa: ANN001
        snapshots = self.provider.collect_once(trade_date, observed_at=observed_at)
        return tuple(replace(item, source=self.source) for item in snapshots)


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"missing required market-data credential: {name}")
    return value


def _tradier_token() -> str:
    """Use one production Tradier token for both market data and account reads.

    TRADIER_MARKET_DATA_TOKEN remains supported as an optional market-data-only
    override, but ordinary personal deployments only need TRADIER_ACCESS_TOKEN.
    """

    return (
        os.environ.get("TRADIER_MARKET_DATA_TOKEN", "").strip()
        or os.environ.get("TRADIER_ACCESS_TOKEN", "").strip()
    )


def _webull_provider(*, production: bool) -> Any:
    app_key = _required("WEBULL_APP_KEY")
    app_secret = _required("WEBULL_APP_SECRET")
    if not production:
        return WebullFreeDataProvider(app_key=app_key, app_secret=app_secret)

    try:
        from webull.core.client import ApiClient
        from webull.data.data_client import DataClient
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("Install Webull SDK with `pip install -e '.[webull]'`") from exc

    api_client = ApiClient(app_key, app_secret, "us")
    api_client.add_endpoint("us", PRODUCTION_API_HOST)
    provider = WebullFreeDataProvider(
        data_client=DataClient(api_client),
        active_contract_limit=int(os.environ.get("WEBULL_ACTIVE_CONTRACT_LIMIT", "20")),
        contract_refresh_seconds=int(os.environ.get("WEBULL_CONTRACT_REFRESH_SECONDS", "60")),
    )
    return _SourceOverrideProvider(provider, "webull_production")


def provider_from_env() -> Any:
    """Build the configured market-data-only provider.

    MARKET_DATA_PROVIDER values:
      auto              Prefer Tradier production real-time when a production
                        Tradier token exists, otherwise use Webull sandbox.
      tradier           Require a Tradier Brokerage production token.
      webull-production Try Webull's production OpenAPI market-data host. Real-time
                        options still require the user's OpenAPI OPRA entitlement.
      webull-sandbox    Explicit delayed sandbox fallback.

    The normal Tradier setup uses TRADIER_ACCESS_TOKEN for both account access and
    production market data. TRADIER_MARKET_DATA_TOKEN can optionally override it.
    This factory exposes market data only. It never creates a broker order client.
    """

    requested = os.environ.get("MARKET_DATA_PROVIDER", "auto").strip().lower()
    if requested == "auto":
        requested = "tradier" if _tradier_token() else "webull-sandbox"

    if requested == "tradier":
        token = _tradier_token()
        if not token:
            raise RuntimeError(
                "missing required market-data credential: TRADIER_ACCESS_TOKEN"
            )
        return TradierRealtimeDataProvider(token)
    if requested in {"webull-production", "webull_production"}:
        return _webull_provider(production=True)
    if requested in {"webull-sandbox", "webull_sandbox", "sandbox"}:
        return _webull_provider(production=False)
    raise RuntimeError(
        "MARKET_DATA_PROVIDER must be one of auto, tradier, webull-production, webull-sandbox"
    )


def provider_source(provider: Any) -> str:
    source = getattr(provider, "source", None)
    if isinstance(source, str) and source.strip():
        return source.strip()
    if isinstance(provider, WebullFreeDataProvider):
        return "webull_sandbox_delayed"
    return provider.__class__.__name__
