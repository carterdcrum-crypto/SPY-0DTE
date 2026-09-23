from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MarketCadencePolicy:
    """Timing contract for second-resolution trading decisions.

    The decision/risk loop runs every second. Market-data refresh cadences are
    kept separate so the engine can honor provider rate limits instead of
    pretending every input is freshly quoted every second.
    """

    decision_tick_seconds: float = 1.0
    sandbox_active_option_refresh_seconds: float = 2.0
    production_active_option_refresh_seconds: float = 1.0
    full_chain_refresh_seconds: float = 60.0
    max_active_option_symbols: int = 20
    maximum_live_quote_age_seconds: float = 3.0

    def __post_init__(self) -> None:
        numeric = (
            self.decision_tick_seconds,
            self.sandbox_active_option_refresh_seconds,
            self.production_active_option_refresh_seconds,
            self.full_chain_refresh_seconds,
            self.maximum_live_quote_age_seconds,
        )
        if any(value <= 0 for value in numeric):
            raise ValueError("all cadence values must be positive")
        if self.max_active_option_symbols <= 0 or self.max_active_option_symbols > 20:
            raise ValueError("max_active_option_symbols must be in 1..20")

        # Current Webull option snapshot limits: sandbox 30/min, production
        # 60/min. A single active-universe request can therefore refresh no
        # faster than every 2s in sandbox and every 1s in production.
        if self.sandbox_active_option_refresh_seconds < 2.0:
            raise ValueError("sandbox option refresh would exceed 30 requests/minute")
        if self.production_active_option_refresh_seconds < 1.0:
            raise ValueError("production option refresh would exceed 60 requests/minute")


@dataclass(frozen=True)
class CadenceDecision:
    evaluate_engine: bool
    refresh_active_options: bool
    refresh_full_chain: bool


def _due(now_seconds: float, last_seconds: float | None, interval_seconds: float) -> bool:
    if last_seconds is None:
        return True
    return (now_seconds - last_seconds) >= interval_seconds


def cadence_decision(
    now_seconds: float,
    *,
    last_engine_tick: float | None,
    last_active_option_refresh: float | None,
    last_full_chain_refresh: float | None,
    sandbox: bool,
    policy: MarketCadencePolicy = MarketCadencePolicy(),
) -> CadenceDecision:
    active_interval = (
        policy.sandbox_active_option_refresh_seconds
        if sandbox
        else policy.production_active_option_refresh_seconds
    )
    return CadenceDecision(
        evaluate_engine=_due(now_seconds, last_engine_tick, policy.decision_tick_seconds),
        refresh_active_options=_due(now_seconds, last_active_option_refresh, active_interval),
        refresh_full_chain=_due(now_seconds, last_full_chain_refresh, policy.full_chain_refresh_seconds),
    )


def quote_is_fresh(age_seconds: float, policy: MarketCadencePolicy = MarketCadencePolicy()) -> bool:
    """Live execution must fail closed when the latest quote is too old."""

    return 0.0 <= float(age_seconds) <= policy.maximum_live_quote_age_seconds
