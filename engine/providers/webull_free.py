from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from typing import Any, Mapping, Tuple
from zoneinfo import ZoneInfo

from ..greeks import ModelGreeks, model_greeks_from_quote
from ..webull import SANDBOX_API_HOST

EASTERN = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class FreeOptionSnapshot:
    # timestamp is the best estimate of the market time represented by the quote,
    # not the collector wall clock. received_at records when our process saw it.
    timestamp: datetime
    received_at: datetime
    timestamp_quality: str
    feed_delay_seconds: float
    option_symbol: str
    expiration: date
    right: str
    strike: float
    bid: float
    ask: float
    underlying_price: float
    minutes_to_expiry: float
    bid_size: int = 0
    ask_size: int = 0
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

    Timestamp integrity is deliberately conservative. If an option snapshot
    exposes its own quote/update timestamp, that timestamp is used. Otherwise we
    anchor the option snapshot to SPY's last-trade timestamp from the same delayed
    feed. SPY is sufficiently active for that timestamp to be a useful delayed
    market clock. If neither timestamp exists, the row is retained but marked
    receive_time_only so research code can exclude it.
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
        received_at = observed_at or datetime.now(timezone.utc)
        if received_at.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")
        received_at = received_at.astimezone(timezone.utc)

        stock = self.client.market_data.get_snapshot("SPY", "US_STOCK")
        stock_rows = _response_rows(stock)
        if not stock_rows:
            raise RuntimeError("Webull returned no SPY snapshot")
        stock_row = stock_rows[0]
        spot = _float_any(stock_row, "last_price", "last", "price", "close", "latest_price")
        if spot <= 0:
            raise ValueError("invalid SPY price")

        # Webull documents stock snapshot last_trade_time as a Unix millisecond
        # timestamp. We prefer any quote/update timestamp if present, then fall
        # back to last_trade_time as the delayed-feed market clock.
        underlying_market_time = _timestamp_any(
            stock_row,
            "timestamp",
            "quote_time",
            "quoteTime",
            "update_time",
            "updateTime",
            "last_trade_time",
            "lastTradeTime",
        )

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

                option_market_time = _timestamp_any(
                    row,
                    "timestamp",
                    "quote_time",
                    "quoteTime",
                    "update_time",
                    "updateTime",
                )
                if option_market_time is not None:
                    market_time = option_market_time
                    timestamp_quality = "option_quote_timestamp"
                elif underlying_market_time is not None:
                    market_time = underlying_market_time
                    timestamp_quality = "underlying_trade_anchor"
                else:
                    # Fail visibly rather than silently pretending delayed data
                    # were current. Downstream research must exclude these rows.
                    market_time = received_at
                    timestamp_quality = "receive_time_only"

                feed_delay_seconds = max(0.0, (received_at - market_time).total_seconds())

                strike = _float_any(contract, "strike_price", "strike", "strikePrice")
                right = _right(_first(contract, "option_type", "right", "optionType"))
                minutes = _minutes_to_expiry(trade_date, market_time)
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
                        timestamp=market_time,
                        received_at=received_at,
                        timestamp_quality=timestamp_quality,
                        feed_delay_seconds=feed_delay_seconds,
                        option_symbol=symbol,
                        expiration=trade_date,
                        right=right,
                        strike=strike,
                        bid=bid,
                        ask=ask,
                        underlying_price=spot,
                        minutes_to_expiry=minutes,
                        bid_size=_int_any(row, "bid_size", "bidSize", "best_bid_size", default=0),
                        ask_size=_int_any(row, "ask_size", "askSize", "best_ask_size", default=0),
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


def _timestamp_any(row: Mapping[str, Any], *keys: str) -> datetime | None:
    value = _first(row, *keys, default=None)
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    if isinstance(value, (int, float)) or (isinstance(value, str) and value.strip().replace(".", "", 1).isdigit()):
        number = float(value)
        # Webull response timestamps are documented as Unix milliseconds. Keep
        # seconds support for injected/future providers as well.
        seconds = number / 1000.0 if abs(number) >= 100_000_000_000 else number
        return datetime.fromtimestamp(seconds, tz=timezone.utc)

    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


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


def _minutes_to_expiry(expiration: date, market_time: datetime) -> float:
    expiry = datetime.combine(expiration, time(16, 0), tzinfo=EASTERN).astimezone(timezone.utc)
    return max(0.0, (expiry - market_time.astimezone(timezone.utc)).total_seconds() / 60.0)
