import math

import pytest

from engine.capabilities import FREE_FIRST_POLICY, RuntimePolicy, ServiceKind
from engine.greeks import black_scholes_price, implied_volatility_bisection, model_greeks_from_quote


def test_free_first_policy_blocks_paid_services_by_default():
    FREE_FIRST_POLICY.authorize_service(service_name="free-feed", kind=ServiceKind.FREE)
    with pytest.raises(PermissionError):
        FREE_FIRST_POLICY.authorize_service(
            service_name="paid-feed",
            kind=ServiceKind.PAID,
            estimated_monthly_cost_usd=40.0,
        )


def test_paid_service_requires_opt_in_and_budget():
    policy = RuntimePolicy(allow_paid_services=True, monthly_external_budget_usd=25.0)
    with pytest.raises(PermissionError):
        policy.authorize_service(
            service_name="too-expensive",
            kind=ServiceKind.PAID,
            estimated_monthly_cost_usd=40.0,
        )
    policy.authorize_service(
        service_name="within-budget",
        kind=ServiceKind.PAID,
        estimated_monthly_cost_usd=20.0,
    )


def test_iv_solver_recovers_known_volatility():
    sigma = 0.35
    price = black_scholes_price(
        right="call",
        spot=500.0,
        strike=500.0,
        time_years=1.0 / 365.0,
        volatility=sigma,
    )
    solved = implied_volatility_bisection(
        right="call",
        option_price=price,
        spot=500.0,
        strike=500.0,
        time_years=1.0 / 365.0,
    )
    assert solved == pytest.approx(sigma, rel=1e-5)


def test_greeks_can_be_estimated_from_plain_quote():
    theoretical = black_scholes_price(
        right="call",
        spot=500.0,
        strike=500.0,
        time_years=60.0 / (365.0 * 24.0 * 60.0),
        volatility=0.45,
    )
    result = model_greeks_from_quote(
        right="call",
        bid=max(0.01, theoretical - 0.01),
        ask=theoretical + 0.01,
        spot=500.0,
        strike=500.0,
        minutes_to_expiry=60.0,
    )
    assert 0.0 < result.delta < 1.0
    assert result.gamma > 0.0
    assert result.vega_per_vol_point > 0.0
    assert result.theta_per_day < 0.0
    assert math.isfinite(result.implied_volatility)
