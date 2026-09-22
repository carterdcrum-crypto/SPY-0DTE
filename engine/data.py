from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Mapping, TextIO, Tuple

from .market import MarketSnapshot, OptionQuote


@dataclass(frozen=True)
class HistoricalFrame:
    """One timestamp of information that would have been observable then."""

    timestamp: datetime
    market: MarketSnapshot
    options: Tuple[OptionQuote, ...]

    def option(self, symbol: str) -> OptionQuote | None:
        for quote in self.options:
            if quote.symbol == symbol:
                return quote
        return None


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("historical timestamps must include a timezone")
    return parsed.astimezone(timezone.utc)


def _float(row: Mapping[str, str], key: str, default: float | None = None) -> float:
    raw = row.get(key)
    if raw in (None, ""):
        if default is None:
            raise ValueError(f"missing required field: {key}")
        return default
    return float(raw)


def _int(row: Mapping[str, str], key: str, default: int = 0) -> int:
    raw = row.get(key)
    if raw in (None, ""):
        return default
    return int(float(raw))


def load_canonical_rows(rows: Iterable[Mapping[str, str]]) -> Tuple[HistoricalFrame, ...]:
    """Load the provider-neutral canonical quote format.

    One CSV row represents one option quote. SPY market fields are repeated for
    every option at the same timestamp. Rows are grouped into HistoricalFrame
    objects and returned strictly sorted by UTC timestamp.
    """

    grouped: dict[datetime, list[Mapping[str, str]]] = {}
    for row in rows:
        ts = _timestamp(str(row["timestamp"]))
        grouped.setdefault(ts, []).append(row)

    frames: list[HistoricalFrame] = []
    for ts in sorted(grouped):
        bucket = grouped[ts]
        first = bucket[0]
        market = MarketSnapshot(
            spot=_float(first, "spot"),
            bid=_float(first, "spy_bid"),
            ask=_float(first, "spy_ask"),
            realized_volatility=_float(first, "realized_volatility"),
            implied_volatility=_float(first, "market_implied_volatility"),
            volume_ratio=_float(first, "volume_ratio"),
            minutes_to_close=_float(first, "minutes_to_close"),
            data_age_seconds=_float(first, "data_age_seconds", 0.0),
            ood_score=_float(first, "ood_score", 0.0),
        )

        quotes: list[OptionQuote] = []
        for row in bucket:
            repeated = (
                _float(row, "spot"),
                _float(row, "spy_bid"),
                _float(row, "spy_ask"),
            )
            if repeated != (market.spot, market.bid, market.ask):
                raise ValueError(f"inconsistent market fields at {ts.isoformat()}")

            right = str(row["right"]).lower()
            if right not in {"call", "put"}:
                raise ValueError(f"invalid option right: {right}")

            quotes.append(
                OptionQuote(
                    symbol=str(row["option_symbol"]),
                    right=right,  # type: ignore[arg-type]
                    strike=_float(row, "strike"),
                    bid=_float(row, "option_bid"),
                    ask=_float(row, "option_ask"),
                    delta=_float(row, "delta"),
                    gamma=_float(row, "gamma"),
                    theta=_float(row, "theta"),
                    vega=_float(row, "vega"),
                    implied_volatility=_float(row, "option_implied_volatility"),
                    volume=_int(row, "volume"),
                    open_interest=_int(row, "open_interest"),
                    underlying_price=_float(row, "underlying_price", market.spot),
                    minutes_to_expiry=_float(row, "minutes_to_expiry"),
                )
            )

        frames.append(
            HistoricalFrame(
                timestamp=ts,
                market=market,
                options=tuple(sorted(quotes, key=lambda q: q.symbol)),
            )
        )

    return tuple(frames)


def load_canonical_csv(handle: TextIO) -> Tuple[HistoricalFrame, ...]:
    return load_canonical_rows(csv.DictReader(handle))
