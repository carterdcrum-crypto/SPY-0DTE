from __future__ import annotations

import math
import os
import re
import statistics
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any, Iterable, Mapping, Sequence, Tuple
from zoneinfo import ZoneInfo

from ..data import HistoricalFrame
from ..greeks import model_greeks_from_quote
from ..market import MarketSnapshot, OptionQuote


EASTERN = ZoneInfo("America/New_York")
_OCC = re.compile(r"^(?P<root>[A-Z.]+)(?P<ymd>\d{6})(?P<right>[CP])(?P<strike>\d{8})$")


@dataclass(frozen=True)
class DatabentoHistoryConfig:
    """Historical source configuration for our QuantConnect-free research path."""

    option_dataset: str = "OPRA.PILLAR"
    underlying_dataset: str = "EQUS.MINI"
    option_schema: str = "cbbo-1m"
    underlying_schema: str = "ohlcv-1m"
    option_parent: str = "SPY.OPT"
    underlying_symbol: str = "SPY"
    max_strike_distance_pct: float | None = 0.08
    risk_free_rate: float = 0.0
    dividend_yield: float = 0.0


@dataclass(frozen=True)
class _Contract:
    symbol: str
    expiration: date
    right: str
    strike: float


def _compact_symbol(value: object) -> str:
    return "".join(str(value).split()).upper()


def _timestamp(value: object) -> datetime:
    if isinstance(value, datetime):
        dt = value
    elif hasattr(value, "to_pydatetime"):
        dt = value.to_pydatetime()
    else:
        text = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        raise ValueError("Databento timestamps must be timezone-aware")
    return dt


def _row_timestamp(row: Mapping[str, Any]) -> datetime:
    for key in ("ts_event", "ts_recv", "timestamp", "index"):
        value = row.get(key)
        if value not in (None, ""):
            return _timestamp(value)
    raise ValueError("row has no timestamp")


def _minute(value: datetime) -> datetime:
    return value.replace(second=0, microsecond=0)


def _parse_expiration(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if hasattr(value, "date"):
        return value.date()
    return date.fromisoformat(str(value)[:10])


def _parse_contract_from_symbol(symbol: str) -> _Contract | None:
    compact = _compact_symbol(symbol)
    match = _OCC.match(compact)
    if match is None:
        return None
    ymd = match.group("ymd")
    expiration = date(2000 + int(ymd[:2]), int(ymd[2:4]), int(ymd[4:6]))
    right = "call" if match.group("right") == "C" else "put"
    strike = int(match.group("strike")) / 1000.0
    return _Contract(compact, expiration, right, strike)


def _definition_contract(row: Mapping[str, Any]) -> _Contract | None:
    symbol = row.get("raw_symbol") or row.get("symbol")
    if not symbol:
        return None
    parsed = _parse_contract_from_symbol(str(symbol))

    expiration_raw = row.get("expiration")
    strike_raw = row.get("strike_price")
    right_raw = row.get("instrument_class") or row.get("right") or row.get("option_type")

    expiration = _parse_expiration(expiration_raw) if expiration_raw not in (None, "") else None
    strike = float(strike_raw) if strike_raw not in (None, "") else None
    right = None
    if right_raw not in (None, ""):
        text = str(right_raw).strip().upper()
        if text.startswith("C"):
            right = "call"
        elif text.startswith("P"):
            right = "put"

    if parsed is not None:
        expiration = expiration or parsed.expiration
        strike = strike if strike is not None else parsed.strike
        right = right or parsed.right

    if expiration is None or strike is None or right is None:
        return None
    return _Contract(_compact_symbol(symbol), expiration, right, strike)


def _rows(frame: Any) -> list[dict[str, Any]]:
    """Convert a Databento response/DataFrame into ordinary dictionaries."""

    if hasattr(frame, "to_df"):
        frame = frame.to_df()
    if hasattr(frame, "reset_index") and hasattr(frame, "to_dict"):
        return list(frame.reset_index().to_dict("records"))
    if isinstance(frame, Sequence):
        return [dict(item) for item in frame]
    raise TypeError("unsupported Databento response type")


def _annualized_realized_vol(closes: Sequence[float]) -> float:
    if len(closes) < 3:
        return 0.20
    returns = [
        math.log(cur / prev)
        for prev, cur in zip(closes, closes[1:])
        if prev > 0 and cur > 0
    ]
    if len(returns) < 2:
        return 0.20
    return max(1e-4, statistics.stdev(returns) * math.sqrt(252.0 * 390.0))


def build_frames_from_databento_rows(
    *,
    trade_date: date,
    definition_rows: Iterable[Mapping[str, Any]],
    option_rows: Iterable[Mapping[str, Any]],
    underlying_rows: Iterable[Mapping[str, Any]],
    config: DatabentoHistoryConfig = DatabentoHistoryConfig(),
) -> Tuple[HistoricalFrame, ...]:
    """Normalize Databento OPRA CBBO-1m + SPY OHLCV-1m into our engine format.

    This function is deliberately independent of the Databento SDK so tests can
    validate normalization without network access. Option execution is based on
    historical bid/ask. The underlying OHLCV close is used as the SPY feature
    price; option fills never use that approximation.
    """

    contracts: dict[str, _Contract] = {}
    for row in definition_rows:
        contract = _definition_contract(row)
        if contract is not None and contract.expiration == trade_date:
            contracts[contract.symbol] = contract

    stock_by_minute: dict[datetime, tuple[float, float]] = {}
    for row in underlying_rows:
        ts = _minute(_row_timestamp(row))
        close = row.get("close")
        if close in (None, ""):
            close = row.get("price")
        if close in (None, ""):
            continue
        volume = float(row.get("volume") or 0.0)
        stock_by_minute[ts] = (float(close), volume)

    option_by_minute: dict[datetime, list[Mapping[str, Any]]] = {}
    for row in option_rows:
        symbol = _compact_symbol(row.get("symbol") or row.get("raw_symbol") or "")
        contract = contracts.get(symbol) or _parse_contract_from_symbol(symbol)
        if contract is None or contract.expiration != trade_date:
            continue
        ts = _minute(_row_timestamp(row))
        option_by_minute.setdefault(ts, []).append(row)
        contracts.setdefault(contract.symbol, contract)

    frames: list[HistoricalFrame] = []
    close_history: list[float] = []
    volume_history: list[float] = []

    for ts in sorted(set(stock_by_minute).intersection(option_by_minute)):
        spot, stock_volume = stock_by_minute[ts]
        close_history.append(spot)
        volume_history.append(stock_volume)

        rolling_closes = close_history[-31:]
        realized_vol = _annualized_realized_vol(rolling_closes)
        prior_volumes = volume_history[-21:-1]
        avg_volume = statistics.fmean(prior_volumes) if prior_volumes else max(stock_volume, 1.0)
        volume_ratio = stock_volume / avg_volume if avg_volume > 0 else 1.0

        local = ts.astimezone(EASTERN)
        expiry_dt = datetime.combine(trade_date, time(16, 0), tzinfo=EASTERN)
        minutes_to_close = max(0.0, (expiry_dt - local).total_seconds() / 60.0)

        quotes: list[OptionQuote] = []
        for row in option_by_minute[ts]:
            symbol = _compact_symbol(row.get("symbol") or row.get("raw_symbol") or "")
            contract = contracts.get(symbol)
            if contract is None:
                continue
            bid_raw = row.get("bid_px_00")
            ask_raw = row.get("ask_px_00")
            if bid_raw in (None, "") or ask_raw in (None, ""):
                bid_raw = row.get("bid")
                ask_raw = row.get("ask")
            if bid_raw in (None, "") or ask_raw in (None, ""):
                continue
            bid, ask = float(bid_raw), float(ask_raw)
            if bid < 0 or ask <= 0 or ask < bid:
                continue

            if config.max_strike_distance_pct is not None and spot > 0:
                distance = abs(contract.strike - spot) / spot
                if distance > max(0.0, config.max_strike_distance_pct):
                    continue

            minutes_to_expiry = max(0.25, minutes_to_close)
            try:
                greeks = model_greeks_from_quote(
                    right=contract.right,  # type: ignore[arg-type]
                    bid=bid,
                    ask=ask,
                    spot=spot,
                    strike=contract.strike,
                    minutes_to_expiry=minutes_to_expiry,
                    risk_free_rate=config.risk_free_rate,
                    dividend_yield=config.dividend_yield,
                )
            except ValueError:
                continue

            quotes.append(
                OptionQuote(
                    symbol=contract.symbol,
                    right=contract.right,  # type: ignore[arg-type]
                    strike=contract.strike,
                    bid=bid,
                    ask=ask,
                    delta=greeks.delta,
                    gamma=greeks.gamma,
                    theta=greeks.theta_per_day,
                    vega=greeks.vega_per_vol_point,
                    implied_volatility=greeks.implied_volatility,
                    volume=int(float(row.get("volume") or 0)),
                    open_interest=int(float(row.get("open_interest") or 0)),
                    underlying_price=spot,
                    minutes_to_expiry=minutes_to_expiry,
                )
            )

        if not quotes:
            continue
        market_iv = statistics.median(q.implied_volatility for q in quotes)
        frames.append(
            HistoricalFrame(
                timestamp=ts,
                market=MarketSnapshot(
                    spot=spot,
                    bid=spot,
                    ask=spot,
                    realized_volatility=realized_vol,
                    implied_volatility=market_iv,
                    volume_ratio=volume_ratio,
                    minutes_to_close=minutes_to_close,
                ),
                options=tuple(sorted(quotes, key=lambda quote: quote.symbol)),
            )
        )

    return tuple(frames)


class DatabentoHistoryProvider:
    """Fetch SPY 0DTE historical NBBO data directly, with no QuantConnect dependency."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        config: DatabentoHistoryConfig = DatabentoHistoryConfig(),
        client: Any | None = None,
    ) -> None:
        self.config = config
        if client is not None:
            self.client = client
            return

        try:
            import databento as db
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise RuntimeError(
                "Databento support is optional; install with pip install -e '.[databento]'"
            ) from exc

        key = api_key or os.getenv("DATABENTO_API_KEY")
        self.client = db.Historical(key) if key else db.Historical()

    def fetch_day(self, trade_date: date) -> Tuple[HistoricalFrame, ...]:
        open_et = datetime.combine(trade_date, time(9, 30), tzinfo=EASTERN)
        close_et = datetime.combine(trade_date, time(16, 0), tzinfo=EASTERN)
        start, end = open_et.isoformat(), close_et.isoformat()

        underlying = self.client.timeseries.get_range(
            dataset=self.config.underlying_dataset,
            schema=self.config.underlying_schema,
            symbols=[self.config.underlying_symbol],
            start=start,
            end=end,
        )
        stock_rows = _rows(underlying)
        if not stock_rows:
            return ()

        # Databento definition snapshots must begin at UTC midnight. Asking
        # only for the regular-session window can omit instruments that were
        # already effective before the open and produces an empty 0DTE chain.
        definitions = self.client.timeseries.get_range(
            dataset=self.config.option_dataset,
            schema="definition",
            symbols=self.config.option_parent,
            stype_in="parent",
            start=trade_date,
        )
        definition_rows = _rows(definitions)
        zero_dte = [
            contract
            for row in definition_rows
            if (contract := _definition_contract(row)) is not None
            and contract.expiration == trade_date
        ]
        if not zero_dte:
            return ()

        first_spot = float(stock_rows[0].get("close") or stock_rows[0].get("price") or 0.0)
        symbols = []
        for contract in zero_dte:
            if (
                self.config.max_strike_distance_pct is not None
                and first_spot > 0
                and abs(contract.strike - first_spot) / first_spot
                > max(0.0, self.config.max_strike_distance_pct)
            ):
                continue
            symbols.append(contract.symbol)

        if not symbols:
            return ()

        options = self.client.timeseries.get_range(
            dataset=self.config.option_dataset,
            schema=self.config.option_schema,
            symbols=symbols,
            stype_in="raw_symbol",
            start=start,
            end=end,
        )
        return build_frames_from_databento_rows(
            trade_date=trade_date,
            definition_rows=definition_rows,
            option_rows=_rows(options),
            underlying_rows=stock_rows,
            config=self.config,
        )
