from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

OptionRight = Literal["call", "put"]


@dataclass(frozen=True)
class ModelGreeks:
    implied_volatility: float
    delta: float
    gamma: float
    theta_per_day: float
    vega_per_vol_point: float
    model: str = "black_scholes_approximation"


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def black_scholes_price(
    *,
    right: OptionRight,
    spot: float,
    strike: float,
    time_years: float,
    volatility: float,
    risk_free_rate: float = 0.0,
    dividend_yield: float = 0.0,
) -> float:
    if spot <= 0 or strike <= 0:
        raise ValueError("spot and strike must be positive")
    if time_years <= 0:
        return max(0.0, spot - strike) if right == "call" else max(0.0, strike - spot)
    if volatility <= 0:
        raise ValueError("volatility must be positive")

    root_t = math.sqrt(time_years)
    d1 = (
        math.log(spot / strike)
        + (risk_free_rate - dividend_yield + 0.5 * volatility * volatility) * time_years
    ) / (volatility * root_t)
    d2 = d1 - volatility * root_t
    discount_r = math.exp(-risk_free_rate * time_years)
    discount_q = math.exp(-dividend_yield * time_years)

    if right == "call":
        return spot * discount_q * _norm_cdf(d1) - strike * discount_r * _norm_cdf(d2)
    if right == "put":
        return strike * discount_r * _norm_cdf(-d2) - spot * discount_q * _norm_cdf(-d1)
    raise ValueError(f"invalid option right: {right}")


def implied_volatility_bisection(
    *,
    right: OptionRight,
    option_price: float,
    spot: float,
    strike: float,
    time_years: float,
    risk_free_rate: float = 0.0,
    dividend_yield: float = 0.0,
    low: float = 1e-4,
    high: float = 6.0,
    iterations: int = 100,
) -> float:
    if option_price < 0:
        raise ValueError("option price cannot be negative")
    intrinsic = max(0.0, spot - strike) if right == "call" else max(0.0, strike - spot)
    if option_price + 1e-12 < intrinsic:
        raise ValueError("option price is below intrinsic value")
    if time_years <= 0:
        return low

    low_price = black_scholes_price(
        right=right,
        spot=spot,
        strike=strike,
        time_years=time_years,
        volatility=low,
        risk_free_rate=risk_free_rate,
        dividend_yield=dividend_yield,
    )
    high_price = black_scholes_price(
        right=right,
        spot=spot,
        strike=strike,
        time_years=time_years,
        volatility=high,
        risk_free_rate=risk_free_rate,
        dividend_yield=dividend_yield,
    )
    if not (low_price - 1e-12 <= option_price <= high_price + 1e-12):
        raise ValueError("option price is outside the volatility search range")

    lo, hi = low, high
    for _ in range(iterations):
        mid = 0.5 * (lo + hi)
        price = black_scholes_price(
            right=right,
            spot=spot,
            strike=strike,
            time_years=time_years,
            volatility=mid,
            risk_free_rate=risk_free_rate,
            dividend_yield=dividend_yield,
        )
        if price < option_price:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def model_greeks_from_quote(
    *,
    right: OptionRight,
    bid: float,
    ask: float,
    spot: float,
    strike: float,
    minutes_to_expiry: float,
    risk_free_rate: float = 0.0,
    dividend_yield: float = 0.0,
) -> ModelGreeks:
    """Estimate IV/Greeks from an ordinary bid/ask quote with no paid Greeks feed.

    This is a research feature model, not an execution-price model. SPY options
    are American-style; Black-Scholes is used here as a fast approximation and
    can later be replaced by an American model where early-exercise effects are
    material.
    """

    if bid < 0 or ask <= 0 or ask < bid:
        raise ValueError("invalid option quote")
    if minutes_to_expiry <= 0:
        raise ValueError("minutes_to_expiry must be positive")

    mid = 0.5 * (bid + ask)
    time_years = minutes_to_expiry / (365.0 * 24.0 * 60.0)
    sigma = implied_volatility_bisection(
        right=right,
        option_price=mid,
        spot=spot,
        strike=strike,
        time_years=time_years,
        risk_free_rate=risk_free_rate,
        dividend_yield=dividend_yield,
    )

    root_t = math.sqrt(time_years)
    d1 = (
        math.log(spot / strike)
        + (risk_free_rate - dividend_yield + 0.5 * sigma * sigma) * time_years
    ) / (sigma * root_t)
    d2 = d1 - sigma * root_t
    discount_q = math.exp(-dividend_yield * time_years)
    discount_r = math.exp(-risk_free_rate * time_years)
    pdf = _norm_pdf(d1)

    if right == "call":
        delta = discount_q * _norm_cdf(d1)
        theta_year = (
            -(spot * discount_q * pdf * sigma) / (2.0 * root_t)
            - risk_free_rate * strike * discount_r * _norm_cdf(d2)
            + dividend_yield * spot * discount_q * _norm_cdf(d1)
        )
    elif right == "put":
        delta = discount_q * (_norm_cdf(d1) - 1.0)
        theta_year = (
            -(spot * discount_q * pdf * sigma) / (2.0 * root_t)
            + risk_free_rate * strike * discount_r * _norm_cdf(-d2)
            - dividend_yield * spot * discount_q * _norm_cdf(-d1)
        )
    else:
        raise ValueError(f"invalid option right: {right}")

    gamma = discount_q * pdf / (spot * sigma * root_t)
    vega_per_vol_point = spot * discount_q * pdf * root_t / 100.0

    return ModelGreeks(
        implied_volatility=sigma,
        delta=delta,
        gamma=gamma,
        theta_per_day=theta_year / 365.0,
        vega_per_vol_point=vega_per_vol_point,
    )
