from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from typing import Any, Iterable, Mapping, Sequence, Tuple
from zoneinfo import ZoneInfo

from ..greeks import ModelGreeks, model_greeks_from_quote
from ..webull import SANDBOX_API_HOST

EASTERN = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class FreeOptionSnapshot:
    timestamp: datetime
    option_symbol: str
    expiration: date
    right: str
    strike: float
    bid: float
    ask: float
    underlying_price: float
    minutes_to_expiry: float
    volume: int = 0
    open_interest: int = 0
    greeks: ModelGreeks | None = None
    source: str = "webull_sandbox_delayed"


class WebullFreeDataProvider:
    """Zero-cost SPY 0DTE collector using Webull's sandbox market-data surface.

    The provider intentionally does not require a paid options feed. It discovers
    today's listed SPY contracts, snapshots the underlying and options, then
    derives IV/Greeks locally from bid/ask quotes. Sandbox/delayed observations
    are tagged by source and must not be treated as live execution data.
    """

    def __init__(self, app_key: str | None = None, app_secret: str | None = None, *, data_client: Any | None = None) -> None:
        if data_client is None:
            if not app_key or not app_secret:
                raise ValueError("app_key and app_secret are required when data_client is not injected")
            try:
                from webull.core.client import ApiClient
                from webull.data.data_client import DataClient
            except ImportError as exc:  # pragma: no cover - optional dependency
                raise RuntimeError("Install Webull SDK with `pip install -e '.[webull]'`") from exc
            api_client = ApiClient(app_key, app_secret, "us")
            api_client.add_endpoint("us", SANDBOX_API_HOST)
            data_client = DataClient(api_client)
        self.client = data_client

    def list_zero_dte_contracts(self, trade_date: date) -> Tuple[Mapping[str, Any], ...]:
        response = self.client.instrument.list_option_contracts(
            category="US_OPTION",
            underlying_symbols="SPY",
            status="LISTING",
            start_date=trade_date.isoformat(),
            end_date=trade_date.isoformat(),
        )
        rows = _response_rows(response)
        out = []
        for row in rows:
            symbol = _first(row, "symbol", "option_symbol", "optionSymbol")
            expiration = _date_value(_first(row, "expiration_date", "expire_date", "expirationDate", default=trade_date))
            if not symbol or expiration != trade_date:
                continue
            out.append(row)
        return tuple(out)

    def collect_once(self, trade_date: date, *, observed_at: datetime | None = None) -> Tuple[FreeOptionSnapshot, ...]:
        now = observed_at or datetime.now(timezone.utc)
        if now.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")
        now = now.astimezone(timezone.utc)

        stock = self.client.market_data.get_snapshot("SPY", "US_STOCK")
        stock_rows = _response_rows(stock)
        if not stock_rows:
            raise RuntimeError("Webull returned no SPY snapshot")
        spot = _float_any(stock_rows[0], "last_price", "last", "price", "close", "latest_price")
        if spot <= 0:
            raise ValueError("invalid SPY price")

        contracts = self.list_zero_dte_contracts(trade_date)
        symbols = [str(_first(row, "symbol", "option_symbol", "optionSymbol")) for row in contracts]
        if not symbols:
            return ()

        contract_by_symbol = {str(_first(row, "symbol", "option_symbol", "optionSymbol")): row for row in contracts}
        snapshots: list[FreeOptionSnapshot] = []

        for start in range(0, len(symbols), 20):
            batch = symbols[start : start + 20]
            response = self.client.option_market_data.get_option_snapshot(",".join(batch), "US_OPTION")
            for row in _response_rows(response):
                symbol = str(_first(row, "symbol", "option_symbol", "optionSymbol", default=""))
                contract = contract_by_symbol.get(symbol)
                if contract is None:
                    continue

                bid = _float_any(row, "bid", "bid_price", "bidPrice")
                ask = _float_any(row, "ask", "ask_price", "askPrice")
                if bid < 0 or ask <= 0 or ask < bid:
                    continue

                strike = _float_any(contract, "strike_price", "strike", "strikePrice")
                right = _right(_first(contract, "option_type", "right", "optionType"))
                minutes = _minutes_to_expiry(trade_date, now)
                if minutes <= 0:
                    continue

                greeks: ModelGreeks | None
                try:
                    greeks = model_greeks_from_quote(
                        right=right,  # type: ignore[arg-type]
                        bid=bid,
                        ask=ask,
                        spot=spot,
                        strike=strike,
                        minutes_to_expiry=minutes,
                    )
                except ValueError:
                    # Keep the observed quote even if a numerical IV solution is
                    # unavailable; downstream quality filters can reject it.
                    greeks = None

                snapshots.append(
                    FreeOptionSnapshot(
                        timestamp=now,
                        option_symbol=symbol,
                        expiration=trade_date,
                        right=right,
                        strike=strike,
                        bid=bid,
                        ask=ask,
                        underlying_price=spot,
                        minutes_to_expiry=minutes,
                        volume=_int_any(row, "volume", "trade_volume", default=0),
                        open_interest=_int_any(row, "open_interest", "openInterest", default=0),
                        greeks=greeks,
                    )
                )

        return tuple(sorted(snapshots, key=lambda item: (item.strike, item.right, item.option_symbol)))


def _response_rows(response: Any) -> Tuple[Mapping[str, Any], ...]:
    if hasattr(response, "json"):
        response = response.json()
    if response is None:
        return ()
    if isinstance(response, list):
        return tuple(row for row in response if isinstance(row, Mapping))
    if not isinstance(response, Mapping):
        raise TypeError("unsupported Webull response type")

    for key in ("data", "items", "list", "results", "contracts", "snapshots"):
        value = response.get(key)
        if isinstance(value, list):
            return tuple(row for row in value if isinstance(row, Mapping))
        if isinstance(value, Mapping):
            for nested in ("items", "list", "results", "contracts", "snapshots"):
                rows = value.get(nested)
                if isinstance(rows, list):
                    return tuple(row for row in rows if isinstance(row, Mapping))
    return (response,)


def _first(row: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return default


def _float_any(row: Mapping[str, Any], *keys: str, default: float | None = None) -> float:
    value = _first(row, *keys, default=default)
    if value is None:
        raise ValueError(f"missing numeric field: {keys}")
    return float(value)


def _int_any(row: Mapping[str, Any], *keys: str, default: int = 0) -> int:
    value = _first(row, *keys, default=default)
    return int(float(value))


def _date_value(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _right(value: Any) -> str:
    text = str(value).strip().upper()
    if text in {"CALL", "C"}:
        return "call"
    if text in {"PUT", "P"}:
        return "put"
    raise ValueError(f"invalid option right: {value!r}")


def _minutes_to_expiry(expiration: date, observed_at: datetime) -> float:
    expiry = datetime.combine(expiration, time(16, 0), tzinfo=EASTERN).astimezone(timezone.utc)
    return max(0.0, (expiry - observed_at.astimezone(timezone.utc)).total_seconds() / 60.0)
