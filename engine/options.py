from __future__ import annotations

import math

from .market import OptionQuote
from .scenario import ReturnScenario


def intrinsic_value(quote: OptionQuote, future_spot: float) -> float:
    if quote.right == "call":
        return max(0.0, future_spot - quote.strike)
    return max(0.0, quote.strike - future_spot)


def reprice_from_greeks(
    quote: OptionQuote,
    scenario: ReturnScenario,
    *,
    horizon_minutes: float,
) -> float:
    """Approximate future option value using local Greeks with hard arbitrage floor.

    This is intentionally a research-layer repricer. It is fast enough for broad
    candidate screening and can later be replaced by a surface-aware model. The
    returned value is never allowed below intrinsic value or zero.
    """

    horizon = max(0.0, min(float(horizon_minutes), quote.minutes_to_expiry))
    future_spot = quote.underlying_price * math.exp(scenario.log_return)
    delta_s = future_spot - quote.underlying_price
    elapsed_days = horizon / 1440.0

    estimate = (
        quote.mid
        + quote.delta * delta_s
        + 0.5 * quote.gamma * delta_s * delta_s
        + quote.theta * elapsed_days
        + quote.vega * scenario.iv_change
    )

    return max(0.0, intrinsic_value(quote, future_spot), estimate)
