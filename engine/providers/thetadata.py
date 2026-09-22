from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from typing import Any, Mapping, Sequence, Tuple
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class ThetaDataConfig:
    symbol: str = "SPY"
    interval: str = "1m"
    strike_range: int = 20
    start_time: time = time(9, 30)
    end_time: time = time(16, 0)


@dataclass(frozen=True)
class ThetaOptionObservation:
    timestamp: datetime
    option_symbol: str
    expiration: date
    right: str
    strike: float
    bid: float
    ask: float
    delta: float
    gamma: float
    theta: float
    vega: float
    implied_volatility: float
    underlying_price: float

    @property
    def spread(self) -> float:
        return max(0.0, self.ask - self.bid)


class ThetaDataProvider:
    """ThetaData Pro adapter for same-day SPY option-chain history.

    The provider is intentionally isolated from the strategy engine. It returns
    normalized observations only; feature engineering, opportunity scoring, and
    trade decisions happen elsewhere.

    A client may be injected for tests. When omitted, the official `thetadata`
    Python package is imported lazily and authenticates from THETADATA_API_KEY.
    """

    def __init__(
        self,
        *,
        client: Any | None = None,
        api_key: str | None = None,
        config: ThetaDataConfig | None = None,
    ) -> None:
        self.config = config or ThetaDataConfig()
        if self.config.symbol.upper() != "SPY":
            raise ValueError("this provider is intentionally restricted to SPY")
        if self.config.strike_range < 1:
            raise ValueError("strike_range must be positive")

        if client is None:
            try:
                from thetadata import ThetaClient  # type: ignore
            except ImportError as exc:  # pragma: no cover - depends on optional extra
                raise RuntimeError(
                    "ThetaData support is optional; install with `pip install -e '.[thetadata]'`"
                ) from exc
            kwargs: dict[str, Any] = {"dataframe_type": "pandas"}
            if api_key:
                kwargs["api_key"] = api_key
            client = ThetaClient(**kwargs)
        self.client = client

    def fetch_zero_dte(self, trade_date: date) -> Tuple[ThetaOptionObservation, ...]:
        """Fetch the observable same-day SPY chain at one-minute resolution.

        ThetaData Pro's all-greeks history includes NBBO, implied volatility,
        first/second-order Greeks, and the contemporaneous underlying midpoint.
        `version="latest"` is requested so 0DTE calculations use real time-to-
        expiration rather than the legacy fixed-DTE approximation.
        """

        payload = self.client.option_history_greeks_all(
            symbol="SPY",
            expiration=trade_date,
            date=trade_date,
            strike="*",
            right="both",
            start_time=self.config.start_time,
            end_time=self.config.end_time,
            interval=self.config.interval,
            strike_range=self.config.strike_range,
            version="latest",
        )
        return self.normalize(payload, trade_date=trade_date)

    def normalize(
        self,
        payload: Any,
        *,
        trade_date: date,
    ) -> Tuple[ThetaOptionObservation, ...]:
        records = _records(payload)
        out: list[ThetaOptionObservation] = []
        for row in records:
            expiration = _as_date(row.get("expiration", trade_date))
            if expiration != trade_date:
                raise ValueError("ThetaData returned a non-0DTE expiration")

            right = _right(row.get("right"))
            strike = _required_float(row, "strike")
            bid = _required_float(row, "bid")
            ask = _required_float(row, "ask")
            if bid < 0 or ask < 0 or ask < bid:
                raise ValueError("invalid or crossed option quote")

            timestamp = _timestamp_utc(row.get("timestamp"))
            observation = ThetaOptionObservation(
                timestamp=timestamp,
                option_symbol=_occ_symbol("SPY", expiration, right, strike),
                expiration=expiration,
                right=right,
                strike=strike,
                bid=bid,
                ask=ask,
                delta=_required_float(row, "delta"),
                gamma=_required_float(row, "gamma"),
                theta=_required_float(row, "theta"),
                vega=_required_float(row, "vega"),
                implied_volatility=_required_float(row, "implied_vol"),
                underlying_price=_required_float(row, "underlying_price"),
            )
            if observation.underlying_price <= 0:
                raise ValueError("underlying price must be positive")
            if observation.implied_volatility < 0:
                raise ValueError("implied volatility cannot be negative")
            out.append(observation)

        return tuple(
            sorted(
                out,
                key=lambda item: (
                    item.timestamp,
                    item.strike,
                    0 if item.right == "call" else 1,
                ),
            )
        )


def _records(payload: Any) -> Tuple[Mapping[str, Any], ...]:
    if payload is None:
        return ()
    if isinstance(payload, Mapping):
        return (payload,)
    if isinstance(payload, (list, tuple)):
        rows = payload
    elif hasattr(payload, "to_dicts"):
        rows = payload.to_dicts()
    elif hasattr(payload, "to_dict"):
        try:
            rows = payload.to_dict(orient="records")
        except TypeError as exc:
            raise TypeError("unsupported dataframe response from ThetaData") from exc
    else:
        raise TypeError("unsupported ThetaData response type")

    normalized: list[Mapping[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise TypeError("ThetaData rows must be mappings")
        normalized.append(row)
    return tuple(normalized)


def _required_float(row: Mapping[str, Any], key: str) -> float:
    value = row.get(key)
    if value is None or value == "":
        raise ValueError(f"missing ThetaData field: {key}")
    return float(value)


def _as_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None:
        raise ValueError("missing expiration")
    return date.fromisoformat(str(value))


def _timestamp_utc(value: Any) -> datetime:
    if value is None:
        raise ValueError("missing ThetaData timestamp")
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        # ThetaData option-history clock times are exchange/Eastern timestamps.
        parsed = parsed.replace(tzinfo=EASTERN)
    return parsed.astimezone(timezone.utc)


def _right(value: Any) -> str:
    normalized = str(value).strip().lower()
    if normalized in {"c", "call"}:
        return "call"
    if normalized in {"p", "put"}:
        return "put"
    raise ValueError(f"invalid option right from ThetaData: {value!r}")


def _occ_symbol(underlying: str, expiration: date, right: str, strike: float) -> str:
    if strike < 0:
        raise ValueError("strike cannot be negative")
    strike_code = int(round(strike * 1000.0))
    return (
        f"O:{underlying.upper()}"
        f"{expiration.strftime('%y%m%d')}"
        f"{'C' if right == 'call' else 'P'}"
        f"{strike_code:08d}"
    )
