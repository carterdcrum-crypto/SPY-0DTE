from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


OptionRight = Literal["call", "put"]


@dataclass(frozen=True)
class MarketSnapshot:
    """Normalized SPY market state used by the research engine."""

    spot: float
    bid: float
    ask: float
    realized_volatility: float
    implied_volatility: float
    volume_ratio: float
    minutes_to_close: float
    data_age_seconds: float = 0.0
    ood_score: float = 0.0

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0


@dataclass(frozen=True)
class OptionQuote:
    """Normalized 0DTE quote.

    Greeks are expected in conventional units: theta per calendar day and vega
    per 1.00 absolute IV change. Prices are quoted per share; one contract has a
    100-share multiplier.
    """

    symbol: str
    right: OptionRight
    strike: float
    bid: float
    ask: float
    delta: float
    gamma: float
    theta: float
    vega: float
    implied_volatility: float
    volume: int
    open_interest: int
    underlying_price: float
    minutes_to_expiry: float

    @property
    def mid(self) -> float:
        return max(0.0, (self.bid + self.ask) / 2.0)

    @property
    def spread(self) -> float:
        return max(0.0, self.ask - self.bid)

    @property
    def spread_fraction(self) -> float:
        mid = self.mid
        if mid <= 0:
            return 1.0
        return self.spread / mid

    @property
    def liquidity_score(self) -> float:
        """Conservative 0..1 heuristic from spread, volume, and open interest."""

        spread_component = max(0.0, min(1.0, 1.0 - self.spread_fraction / 0.20))
        volume_component = min(1.0, max(0.0, self.volume) / 500.0)
        oi_component = min(1.0, max(0.0, self.open_interest) / 2000.0)
        return 0.55 * spread_component + 0.25 * volume_component + 0.20 * oi_component
