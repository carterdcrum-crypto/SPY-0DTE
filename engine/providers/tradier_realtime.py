from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

import requests

from ..greeks import ModelGreeks, model_greeks_from_quote
from .webull_free import FreeOptionSnapshot

EASTERN = ZoneInfo("America/New_York")
TRADIER_PRODUCTION_BASE_URL = "https://api.tradier.com/v1"


@dataclass(frozen=True)
class _Contract:
    symbol: str
    strike: float
    right: str
    expiration: date


class TradierRealtimeDataProvider:
    """Real-time SPY options data through a Tradier Brokerage production token.

    Tradier's production Brokerage API provides real-time consolidated US stock
    and option quotes to brokerage account holders. The sandbox feed is delayed,
    so this provider intentionally accepts production tokens only and is used for
    market data only; it contains no order-placement methods.

    The full same-day chain is refreshed periodically. Between refreshes a small
    near-the-money contract set plus SPY is queried in one quote request, keeping
    a 2-second collector loop well below Tradier's production market-data limits.
    """

    source = "tradier_realtime"

    def __init__(
        self,
        token: str,
        *,
        session: requests.Session | Any | None = None,
        active_contract_limit: int | None = None,
        contract_refresh_seconds: int | None = None,
        timeout_seconds: float = 5.0,
    ) -> None:
        token = token.strip()
        if not token:
            raise ValueError("Tradier production token is required")
        self.token = token
        self.session = session or requests.Session()
        self.active_contract_limit = int(
            active_contract_limit
            if active_contract_limit is not None
            else os.environ.get("TRADIER_ACTIVE_CONTRACT_LIMIT", "20")
        )
        self.contract_refresh_seconds = int(
            contract_refresh_seconds
            if contract_refresh_seconds is not None
            else os.environ.get("TRADIER_CONTRACT_REFRESH_SECONDS", "60")
        )
        self.timeout_seconds = float(timeout_seconds)
        if self.active_contract_limit <= 0:
            raise ValueError("active_contract_limit must be positive")
        if self.contract_refresh_seconds <= 0:
            raise ValueError("contract_refresh_seconds must be positive")

        self._contract_cache_date: date | None = None
        self._contract_cache_at: datetime | None = None
        self._contract_cache: tuple[_Contract, ...] = ()

    def _get(self, path: str, params: Mapping[str, object]) -> Mapping[str, Any]:
        response = self.session.get(
            f"{TRADIER_PRODUCTION_BASE_URL}{path}",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/json",
                "User-Agent": "SPY-0DTE/0.5",
            },
            params=params,
            timeout=self.timeout_seconds,
        )
        if int(getattr(response, "status_code", 200)) in {401, 403}:
            raise RuntimeError(
                "Tradier production market-data token is unauthorized; use a Brokerage production token"
            )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, Mapping):
            raise RuntimeError("Tradier returned an invalid JSON payload")
        return payload

    def _quotes(self, symbols: Sequence[str]) -> tuple[Mapping[str, Any], ...]:
        payload = self._get(
            "/markets/quotes",
            {
                "symbols": ",".join(symbols),
                "greeks": "false",
                "includeLotSize": "true",
            },
        )
        return _quote_rows(payload)

    def _refresh_contracts(
        self,
        trade_date: date,
        *,
        spot: float,
        observed_at: datetime,
    ) -> None:
        payload = self._get(
            "/markets/options/chains",
            {
                "symbol": "SPY",
                "expiration": trade_date.isoformat(),
                "greeks": "false",
            },
        )
        rows = _option_rows(payload)
        contracts: list[_Contract] = []
        for row in rows:
            symbol = str(row.get("symbol") or "").strip()
            if not symbol:
                continue
            try:
                expiration = _date_value(row.get("expiration_date") or trade_date)
                strike = float(row.get("strike"))
            except (TypeError, ValueError):
                continue
            if expiration != trade_date or strike <= 0:
                continue
            right = str(row.get("option_type") or "").strip().lower()
            if right not in {"call", "put"}:
                continue
            contracts.append(_Contract(symbol, strike, right, expiration))

        contracts.sort(key=lambda item: (abs(item.strike - spot), item.strike, item.right, item.symbol))
        self._contract_cache = tuple(contracts[: self.active_contract_limit])
        self._contract_cache_date = trade_date
        self._contract_cache_at = observed_at

    def _cache_needs_refresh(self, trade_date: date, observed_at: datetime) -> bool:
        if self._contract_cache_date != trade_date or not self._contract_cache:
            return True
        if self._contract_cache_at is None:
            return True
        return (observed_at - self._contract_cache_at).total_seconds() >= self.contract_refresh_seconds

    def collect_once(
        self,
        trade_date: date,
        *,
        observed_at: datetime | None = None,
    ) -> tuple[FreeOptionSnapshot, ...]:
        received_at = observed_at or datetime.now(timezone.utc)
        if received_at.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")
        received_at = received_at.astimezone(timezone.utc)

        # Bootstrap the active contract cache with one SPY quote. Normal cycles
        # then query SPY + all active options in a single request.
        if not self._contract_cache or self._contract_cache_date != trade_date:
            stock_rows = self._quotes(("SPY",))
            stock_row = _find_symbol(stock_rows, "SPY")
            if stock_row is None:
                raise RuntimeError("Tradier returned no SPY quote")
            spot = _spot_from_quote(stock_row)
            self._refresh_contracts(trade_date, spot=spot, observed_at=received_at)

        symbols = ("SPY", *(contract.symbol for contract in self._contract_cache))
        quote_rows = self._quotes(symbols)
        stock_row = _find_symbol(quote_rows, "SPY")
        if stock_row is None:
            raise RuntimeError("Tradier returned no SPY quote")
        spot = _spot_from_quote(stock_row)
        underlying_market_time = _quote_timestamp(stock_row) or received_at

        if self._cache_needs_refresh(trade_date, received_at):
            self._refresh_contracts(trade_date, spot=spot, observed_at=received_at)

        contract_by_symbol = {item.symbol: item for item in self._contract_cache}
        snapshots: list[FreeOptionSnapshot] = []
        for row in quote_rows:
            symbol = str(row.get("symbol") or "").strip()
            contract = contract_by_symbol.get(symbol)
            if contract is None:
                continue
            try:
                bid = float(row.get("bid") or 0.0)
                ask = float(row.get("ask") or 0.0)
            except (TypeError, ValueError):
                continue
            if bid < 0.0 or ask <= 0.0 or ask < bid:
                continue

            market_time = _quote_timestamp(row) or underlying_market_time
            feed_delay_seconds = max(0.0, (received_at - market_time).total_seconds())
            minutes = _minutes_to_expiry(trade_date, market_time)
            if minutes <= 0.0:
                continue

            greeks: ModelGreeks | None
            try:
                greeks = model_greeks_from_quote(
                    right=contract.right,  # type: ignore[arg-type]
                    bid=bid,
                    ask=ask,
                    spot=spot,
                    strike=contract.strike,
                    minutes_to_expiry=minutes,
                )
            except ValueError:
                greeks = None

            snapshots.append(
                FreeOptionSnapshot(
                    timestamp=market_time,
                    received_at=received_at,
                    timestamp_quality="tradier_nbbo_timestamp",
                    feed_delay_seconds=feed_delay_seconds,
                    option_symbol=symbol,
                    expiration=trade_date,
                    right=contract.right,
                    strike=contract.strike,
                    bid=bid,
                    ask=ask,
                    underlying_price=spot,
                    minutes_to_expiry=minutes,
                    bid_size=_int_value(row.get("bidsize")),
                    ask_size=_int_value(row.get("asksize")),
                    volume=_int_value(row.get("volume")),
                    open_interest=_int_value(row.get("open_interest")),
                    greeks=greeks,
                    source=self.source,
                )
            )

        return tuple(sorted(snapshots, key=lambda item: (item.strike, item.right, item.option_symbol)))


def _quote_rows(payload: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    quotes = payload.get("quotes")
    if isinstance(quotes, Mapping):
        value = quotes.get("quote")
        if isinstance(value, Mapping):
            return (value,)
        if isinstance(value, list):
            return tuple(row for row in value if isinstance(row, Mapping))
    return ()


def _option_rows(payload: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    options = payload.get("options")
    if isinstance(options, Mapping):
        value = options.get("option")
        if isinstance(value, Mapping):
            return (value,)
        if isinstance(value, list):
            return tuple(row for row in value if isinstance(row, Mapping))
    return ()


def _find_symbol(rows: Sequence[Mapping[str, Any]], symbol: str) -> Mapping[str, Any] | None:
    for row in rows:
        if str(row.get("symbol") or "").strip().upper() == symbol.upper():
            return row
    return None


def _spot_from_quote(row: Mapping[str, Any]) -> float:
    bid = _float_value(row.get("bid"))
    ask = _float_value(row.get("ask"))
    if bid > 0.0 and ask >= bid:
        return (bid + ask) / 2.0
    last = _float_value(row.get("last"))
    if last > 0.0:
        return last
    raise RuntimeError("Tradier SPY quote has no valid price")


def _quote_timestamp(row: Mapping[str, Any]) -> datetime | None:
    parsed = [
        item
        for item in (
            _timestamp_value(row.get("bid_date")),
            _timestamp_value(row.get("ask_date")),
            _timestamp_value(row.get("trade_date")),
        )
        if item is not None
    ]
    return max(parsed) if parsed else None


def _timestamp_value(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    try:
        number = float(value)  # Tradier quote dates are Unix milliseconds.
    except (TypeError, ValueError):
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    seconds = number / 1000.0 if abs(number) >= 100_000_000_000 else number
    try:
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _minutes_to_expiry(trade_date: date, market_time: datetime) -> float:
    close = datetime.combine(trade_date, time(16, 0), tzinfo=EASTERN).astimezone(timezone.utc)
    return max(0.0, (close - market_time.astimezone(timezone.utc)).total_seconds() / 60.0)


def _date_value(value: object) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return date.fromisoformat(str(value)[:10])


def _float_value(value: object) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _int_value(value: object) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0
