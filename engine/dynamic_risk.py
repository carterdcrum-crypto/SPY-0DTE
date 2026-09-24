from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Sequence

from .risk import optimal_kelly_fraction, weighted_cvar


def _clip(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, float(value)))


def _normalize(probabilities: Sequence[float]) -> tuple[float, ...]:
    if not probabilities:
        raise ValueError("probabilities cannot be empty")
    if any(float(p) < 0.0 for p in probabilities):
        raise ValueError("probabilities cannot be negative")
    total = sum(float(p) for p in probabilities)
    if total <= 0.0:
        raise ValueError("probabilities must sum to a positive value")
    return tuple(float(p) / total for p in probabilities)


def expected_return(
    returns: Sequence[float],
    probabilities: Sequence[float],
) -> float:
    if len(returns) != len(probabilities) or not returns:
        raise ValueError("returns and probabilities must be non-empty and aligned")
    probs = _normalize(probabilities)
    return sum(float(value) * probability for value, probability in zip(returns, probs))


def integer_expected_log_utility(
    *,
    account_equity: float,
    entry_cost_per_contract: float,
    returns: Sequence[float],
    probabilities: Sequence[float],
    contracts: int,
) -> float:
    """Expected log-wealth change for an integer contract quantity.

    `returns` are net decimal returns on premium paid. This operates on total
    account wealth, so it naturally captures the coarse 0-vs-1 contract
    granularity of small accounts.
    """
    if account_equity <= 0.0:
        return -math.inf
    if entry_cost_per_contract <= 0.0:
        return -math.inf
    if contracts < 0:
        raise ValueError("contracts cannot be negative")
    if len(returns) != len(probabilities) or not returns:
        raise ValueError("returns and probabilities must be non-empty and aligned")

    probs = _normalize(probabilities)
    total = 0.0
    for scenario_return, probability in zip(returns, probs):
        wealth = account_equity + contracts * entry_cost_per_contract * float(scenario_return)
        if wealth <= 0.0:
            return -math.inf
        total += probability * math.log(wealth / account_equity)
    return total


def integer_utility_optimum(
    *,
    account_equity: float,
    entry_cost_per_contract: float,
    returns: Sequence[float],
    probabilities: Sequence[float],
    maximum_contracts: int,
) -> tuple[int, float]:
    if maximum_contracts <= 0:
        return 0, 0.0

    best_contracts = 0
    best_utility = 0.0
    for contracts in range(1, int(maximum_contracts) + 1):
        utility = integer_expected_log_utility(
            account_equity=account_equity,
            entry_cost_per_contract=entry_cost_per_contract,
            returns=returns,
            probabilities=probabilities,
            contracts=contracts,
        )
        if utility > best_utility:
            best_contracts = contracts
            best_utility = utility
    return best_contracts, best_utility


def smooth_drawdown_multiplier(
    drawdown_fraction: float,
    *,
    soft_start: float,
    zero_level: float,
    exponent: float = 1.5,
) -> float:
    """Smoothly throttle risk between a soft-start and zero-risk drawdown."""
    drawdown = max(0.0, float(drawdown_fraction))
    if zero_level <= soft_start:
        raise ValueError("zero_level must be greater than soft_start")
    if exponent <= 0.0:
        raise ValueError("exponent must be positive")
    if drawdown <= soft_start:
        return 1.0
    if drawdown >= zero_level:
        return 0.0
    span = (zero_level - drawdown) / (zero_level - soft_start)
    return _clip(span ** exponent, 0.0, 1.0)


@dataclass(frozen=True)
class DynamicRiskConfig:
    """Paper-first dynamic risk profile.

    The defaults intentionally reflect a micro-account paper-learning profile,
    not a live-money profile. With a $115 balance, normal SPY contracts make the
    conservative sub-1% research budgets mathematically incapable of producing
    even one contract. LIVE must use a separately versioned, stricter config.
    """

    kelly_fraction: float = 0.20
    minimum_positive_edge_probability: float = 0.55
    maximum_contracts: int = 10

    trade_es99_fraction: float = 0.30
    aggregate_es99_fraction: float = 0.35
    maximum_premium_loss_fraction: float = 0.35
    maximum_capital_fraction: float = 0.90
    cvar_confidence_level: float = 0.99

    drawdown_soft_start: float = 0.08
    drawdown_zero_level: float = 0.25
    drawdown_exponent: float = 1.5
    maximum_daily_loss_fraction: float = 0.20
    minimum_single_contract_multiplier: float = 0.60

    maximum_quote_age_seconds: float = 4.0
    maximum_spread_fraction: float = 0.10
    minimum_liquidity_score: float = 0.60
    minimum_minutes_to_expiry: float = 20.0
    liquidity_participation_fraction: float = 0.25

    # Paper-only granularity policy: fractional Kelly can mathematically round to
    # zero in a tiny account even when one contract has positive log utility.
    # Future conservative LIVE config must set this false.
    allow_minimum_viable_contract: bool = True


@dataclass(frozen=True)
class DynamicRiskRequest:
    account_equity: float
    high_watermark: float
    settled_cash: float
    daily_start_equity: float
    daily_realized_pnl: float

    entry_cost_per_contract: float
    scenario_returns: tuple[float, ...]
    scenario_probabilities: tuple[float, ...]
    positive_edge_probability: float

    liquidity_score: float
    spread_fraction: float
    quote_age_seconds: float
    minutes_to_expiry: float
    ask_size: int = 0

    existing_portfolio_es99_dollars: float = 0.0
    event_multiplier: float = 1.0
    model_health_multiplier: float = 1.0


@dataclass(frozen=True)
class DynamicRiskDecision:
    allowed: bool
    reason: str
    contracts: int = 0
    sizing_mode: str = "none"

    expected_net_return: float = 0.0
    expected_net_ev_per_contract: float = 0.0
    positive_edge_probability: float = 0.0

    raw_kelly_fraction: float = 0.0
    fractional_kelly_fraction: float = 0.0
    full_utility_contracts: int = 0
    fractional_kelly_contracts: int = 0
    expected_log_utility: float = 0.0

    es99_per_contract: float = 0.0
    trade_es99_budget: float = 0.0
    aggregate_es99_budget: float = 0.0
    es_limited_contracts: int = 0
    portfolio_limited_contracts: int = 0
    cash_limited_contracts: int = 0
    capital_limited_contracts: int = 0
    premium_limited_contracts: int = 0
    liquidity_limited_contracts: int = 0
    hard_limited_contracts: int = 0

    drawdown_fraction: float = 0.0
    daily_loss_fraction: float = 0.0
    drawdown_multiplier: float = 1.0
    event_multiplier: float = 1.0
    model_health_multiplier: float = 1.0
    adaptive_multiplier: float = 1.0

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _blocked(reason: str, **values: object) -> DynamicRiskDecision:
    return DynamicRiskDecision(allowed=False, reason=reason, **values)


def choose_dynamic_long_option_size(
    request: DynamicRiskRequest,
    config: DynamicRiskConfig = DynamicRiskConfig(),
) -> DynamicRiskDecision:
    """Choose integer long-option size using utility + ES + hard rails.

    Desired size comes from expected log utility / Kelly economics. Permitted
    size comes from cash, premium-at-risk, ES99, aggregate ES, and displayed
    liquidity limits. Drawdown, event, and model-health state can only reduce
    risk; none can raise a hard ceiling.
    """
    if request.account_equity <= 0.0:
        return _blocked("no_equity")
    if request.entry_cost_per_contract <= 0.0:
        return _blocked("invalid_entry_cost")
    if len(request.scenario_returns) != len(request.scenario_probabilities) or not request.scenario_returns:
        return _blocked("invalid_scenarios")
    if any(float(value) < -1.0 for value in request.scenario_returns):
        return _blocked("invalid_long_option_return")

    probabilities = _normalize(request.scenario_probabilities)
    edge_probability = _clip(request.positive_edge_probability, 0.0, 1.0)

    drawdown = (
        1.0
        if request.high_watermark <= 0.0
        else max(0.0, 1.0 - request.account_equity / request.high_watermark)
    )
    daily_loss = (
        1.0
        if request.daily_start_equity <= 0.0
        else max(0.0, -request.daily_realized_pnl / request.daily_start_equity)
    )
    drawdown_scale = smooth_drawdown_multiplier(
        drawdown,
        soft_start=config.drawdown_soft_start,
        zero_level=config.drawdown_zero_level,
        exponent=config.drawdown_exponent,
    )
    event_scale = _clip(request.event_multiplier, 0.0, 1.0)
    health_scale = _clip(request.model_health_multiplier, 0.0, 1.0)
    adaptive_scale = drawdown_scale * event_scale * health_scale

    common = dict(
        positive_edge_probability=edge_probability,
        drawdown_fraction=drawdown,
        daily_loss_fraction=daily_loss,
        drawdown_multiplier=drawdown_scale,
        event_multiplier=event_scale,
        model_health_multiplier=health_scale,
        adaptive_multiplier=adaptive_scale,
    )

    # Independent hard rails: model output cannot override these.
    if request.quote_age_seconds > config.maximum_quote_age_seconds:
        return _blocked("stale_market_data", **common)
    if request.spread_fraction > config.maximum_spread_fraction:
        return _blocked("spread_too_wide", **common)
    if request.liquidity_score < config.minimum_liquidity_score:
        return _blocked("insufficient_liquidity", **common)
    if request.minutes_to_expiry < config.minimum_minutes_to_expiry:
        return _blocked("expiry_window_block", **common)
    if drawdown >= config.drawdown_zero_level:
        return _blocked("drawdown_halt", **common)
    if daily_loss >= config.maximum_daily_loss_fraction:
        return _blocked("daily_loss_halt", **common)
    if request.settled_cash < request.entry_cost_per_contract:
        return _blocked("insufficient_settled_cash", **common)
    if edge_probability < config.minimum_positive_edge_probability:
        return _blocked("insufficient_edge_probability", **common)

    net_return = expected_return(request.scenario_returns, probabilities)
    net_ev = net_return * request.entry_cost_per_contract
    common.update(
        expected_net_return=net_return,
        expected_net_ev_per_contract=net_ev,
    )
    if net_return <= 0.0:
        return _blocked("non_positive_net_ev", **common)

    entry = request.entry_cost_per_contract
    equity = request.account_equity
    scenario_losses = tuple(max(0.0, -float(value) * entry) for value in request.scenario_returns)
    es99 = weighted_cvar(
        scenario_losses,
        probabilities,
        config.cvar_confidence_level,
    )

    q_cash = int(request.settled_cash // entry)
    q_capital = int((equity * max(0.0, config.maximum_capital_fraction)) // entry)
    q_premium = int((equity * max(0.0, config.maximum_premium_loss_fraction)) // entry)

    trade_es_budget = equity * max(0.0, config.trade_es99_fraction)
    aggregate_es_budget = equity * max(0.0, config.aggregate_es99_fraction)
    remaining_aggregate_es = max(
        0.0,
        aggregate_es_budget - max(0.0, request.existing_portfolio_es99_dollars),
    )

    if es99 <= 1e-12:
        q_es = config.maximum_contracts
        q_portfolio = config.maximum_contracts
    else:
        q_es = int(trade_es_budget // es99)
        q_portfolio = int(remaining_aggregate_es // es99)

    if request.ask_size > 0:
        q_liquidity = max(
            1,
            int(request.ask_size * max(0.0, config.liquidity_participation_fraction)),
        )
    else:
        # Some retail/sandbox feeds omit displayed depth. Unknown depth does not
        # imply infinite size; fall back to the configured global contract cap.
        q_liquidity = config.maximum_contracts

    hard_limit = max(
        0,
        min(
            config.maximum_contracts,
            q_cash,
            q_capital,
            q_premium,
            q_es,
            q_portfolio,
            q_liquidity,
        ),
    )

    limits = dict(
        es99_per_contract=es99,
        trade_es99_budget=trade_es_budget,
        aggregate_es99_budget=aggregate_es_budget,
        es_limited_contracts=q_es,
        portfolio_limited_contracts=q_portfolio,
        cash_limited_contracts=q_cash,
        capital_limited_contracts=q_capital,
        premium_limited_contracts=q_premium,
        liquidity_limited_contracts=q_liquidity,
        hard_limited_contracts=hard_limit,
    )
    common.update(limits)

    if hard_limit < 1:
        if q_premium < 1:
            reason = "hard_premium_loss_limit"
        elif q_es < 1:
            reason = "trade_es99_budget_exhausted"
        elif q_portfolio < 1:
            reason = "aggregate_es99_budget_exhausted"
        elif q_capital < 1:
            reason = "capital_budget_below_one_contract"
        else:
            reason = "position_below_one_contract"
        return _blocked(reason, **common)

    full_utility_contracts, utility = integer_utility_optimum(
        account_equity=equity,
        entry_cost_per_contract=entry,
        returns=request.scenario_returns,
        probabilities=probabilities,
        maximum_contracts=hard_limit,
    )

    raw_kelly = optimal_kelly_fraction(
        request.scenario_returns,
        probabilities,
        maximum_fraction=min(1.0, max(0.0, config.maximum_premium_loss_fraction)),
    )
    fractional_kelly = raw_kelly * _clip(config.kelly_fraction, 0.0, 1.0)
    q_fractional_kelly = int((equity * fractional_kelly) // entry)

    common.update(
        raw_kelly_fraction=raw_kelly,
        fractional_kelly_fraction=fractional_kelly,
        full_utility_contracts=full_utility_contracts,
        fractional_kelly_contracts=q_fractional_kelly,
        expected_log_utility=utility,
    )

    if full_utility_contracts < 1 or utility <= 0.0:
        return _blocked("non_positive_expected_utility", **common)

    sizing_mode = "fractional_kelly"
    desired = min(full_utility_contracts, q_fractional_kelly)
    if desired < 1:
        if not config.allow_minimum_viable_contract:
            return _blocked("fractional_kelly_below_one_contract", **common)
        desired = 1
        sizing_mode = "minimum_viable_contract"

    if desired == 1:
        final_contracts = 1 if adaptive_scale >= config.minimum_single_contract_multiplier else 0
    else:
        final_contracts = int(math.floor(desired * adaptive_scale))

    final_contracts = max(0, min(final_contracts, hard_limit))
    if final_contracts < 1:
        return _blocked(
            "adaptive_throttle_below_one_contract",
            sizing_mode=sizing_mode,
            **common,
        )

    return DynamicRiskDecision(
        allowed=True,
        reason="approved",
        contracts=final_contracts,
        sizing_mode=sizing_mode,
        **common,
    )
