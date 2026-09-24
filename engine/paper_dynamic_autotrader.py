from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .dynamic_risk import (
    DynamicRiskConfig,
    DynamicRiskRequest,
    choose_dynamic_long_option_size,
)
from .paper_account import PaperAccountSnapshot, PaperAccountStore
from .paper_autotrader import (
    EASTERN,
    MarketCycle,
    PaperAutoSettings,
    PaperAutoTrader,
    PaperSignal,
    SnapshotReader,
    _paper_db_path,
    _paper_starting_cash,
    _parse_datetime,
    _publish,
)

log = logging.getLogger("spy0dte.paper.dynamic")
SIGNAL_STRATEGY = "bootstrap_momentum_v1"
RISK_PROFILE = "dynamic_posterior_proxy_v1"
POSITION_STRATEGY = f"{SIGNAL_STRATEGY}+dynamic_risk_v1"


def _clip(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, float(value)))


def _env_float(
    name: str,
    default: float,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    value = float(os.environ.get(name, str(default)))
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} must be <= {maximum}")
    return value


def _env_int(
    name: str,
    default: int,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    value = int(os.environ.get(name, str(default)))
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} must be <= {maximum}")
    return value


def risk_config_from_env() -> DynamicRiskConfig:
    """Versioned paper-only dynamic risk profile.

    These defaults are intentionally the micro-account learning profile. A future
    LIVE profile must be separate and stricter; the model cannot switch profiles.
    """
    return DynamicRiskConfig(
        kelly_fraction=_env_float("PAPER_KELLY_FRACTION", 0.20, minimum=0.0, maximum=1.0),
        minimum_positive_edge_probability=_env_float(
            "PAPER_MIN_POSITIVE_EDGE_PROBABILITY", 0.55, minimum=0.50, maximum=0.99
        ),
        maximum_contracts=_env_int("PAPER_DYNAMIC_MAX_CONTRACTS", 10, minimum=1, maximum=100),
        trade_es99_fraction=_env_float(
            "PAPER_TRADE_ES99_FRACTION", 0.30, minimum=0.001, maximum=1.0
        ),
        aggregate_es99_fraction=_env_float(
            "PAPER_AGGREGATE_ES99_FRACTION", 0.35, minimum=0.001, maximum=1.0
        ),
        maximum_premium_loss_fraction=_env_float(
            "PAPER_MAX_PREMIUM_LOSS_FRACTION", 0.35, minimum=0.001, maximum=1.0
        ),
        maximum_capital_fraction=_env_float(
            "PAPER_DYNAMIC_MAX_CAPITAL_FRACTION", 0.90, minimum=0.01, maximum=1.0
        ),
        cvar_confidence_level=_env_float(
            "PAPER_ES_CONFIDENCE", 0.99, minimum=0.90, maximum=0.9999
        ),
        drawdown_soft_start=_env_float(
            "PAPER_DRAWDOWN_SOFT_START", 0.08, minimum=0.0, maximum=0.95
        ),
        drawdown_zero_level=_env_float(
            "PAPER_DRAWDOWN_ZERO_LEVEL", 0.25, minimum=0.01, maximum=0.99
        ),
        drawdown_exponent=_env_float(
            "PAPER_DRAWDOWN_EXPONENT", 1.5, minimum=0.1, maximum=10.0
        ),
        maximum_daily_loss_fraction=_env_float(
            "PAPER_DAILY_LOSS_HALT_FRACTION", 0.20, minimum=0.001, maximum=1.0
        ),
        minimum_single_contract_multiplier=_env_float(
            "PAPER_MIN_SINGLE_CONTRACT_MULTIPLIER", 0.60, minimum=0.0, maximum=1.0
        ),
        maximum_quote_age_seconds=_env_float(
            "PAPER_RISK_MAX_QUOTE_AGE_SECONDS", 4.0, minimum=0.25, maximum=60.0
        ),
        maximum_spread_fraction=_env_float(
            "PAPER_RISK_MAX_SPREAD_FRACTION", 0.10, minimum=0.001, maximum=1.0
        ),
        minimum_liquidity_score=_env_float(
            "PAPER_RISK_MIN_LIQUIDITY_SCORE", 0.60, minimum=0.0, maximum=1.0
        ),
        minimum_minutes_to_expiry=_env_float(
            "PAPER_MIN_ENTRY_MINUTES_TO_EXPIRY", 20.0, minimum=1.0, maximum=240.0
        ),
        liquidity_participation_fraction=_env_float(
            "PAPER_LIQUIDITY_PARTICIPATION_FRACTION", 0.25, minimum=0.01, maximum=1.0
        ),
        allow_minimum_viable_contract=os.environ.get(
            "PAPER_ALLOW_MINIMUM_VIABLE_CONTRACT", "true"
        ).strip().lower() in {"1", "true", "yes", "on"},
    )


def bootstrap_posterior_proxy(
    signal: PaperSignal,
    settings: PaperAutoSettings,
) -> tuple[float, tuple[float, ...], tuple[float, ...], float]:
    """Turn the bootstrap signal into a conservative paper-only P&L distribution.

    This is deliberately named a proxy: it is not presented as a trained Bayesian
    posterior. It gives the dynamic risk engine a full return distribution today,
    while the proper calibrated forecasting stack is promoted later.
    """
    spot_strength = min(1.0, abs(signal.spot_move_fraction) / 0.0020)
    premium_strength = min(1.0, max(0.0, signal.premium_move_fraction) / 0.15)
    edge_strength = min(1.0, max(0.0, signal.edge_proxy) / 0.10)
    spread_penalty = min(
        1.0,
        signal.spread_fraction / max(1e-9, settings.maximum_spread_fraction),
    )

    positive_edge_probability = _clip(
        0.52
        + 0.08 * spot_strength
        + 0.06 * premium_strength
        + 0.06 * edge_strength
        + 0.05 * signal.liquidity_score
        - 0.03 * spread_penalty,
        0.50,
        0.70,
    )

    # Edge proxy already penalizes the quoted spread. Add only a modest residual
    # implementation-cost drag for fees / adverse execution uncertainty.
    fee_drag = min(0.04, settings.fee_per_contract / max(signal.contract_cost, 1e-9))
    execution_drag = min(0.06, fee_drag + 0.25 * signal.spread_fraction)

    loss_probability = 1.0 - positive_edge_probability
    moderate_win = max(
        0.05,
        min(0.30, signal.premium_move_fraction * 1.5) - execution_drag,
    )
    target_win = max(moderate_win, settings.take_profit_fraction - execution_drag)
    stop_loss = max(-0.95, -settings.stop_loss_fraction - execution_drag)
    mild_loss = max(-0.75, -0.15 - execution_drag)

    scenario_returns = (
        -1.0,
        stop_loss,
        mild_loss,
        moderate_win,
        target_win,
    )
    scenario_probabilities = (
        loss_probability * 0.05,
        loss_probability * 0.50,
        loss_probability * 0.45,
        positive_edge_probability * 0.60,
        positive_edge_probability * 0.40,
    )

    model_health = _clip(
        0.65
        + 0.25 * signal.liquidity_score
        - 0.15 * spread_penalty,
        0.50,
        0.95,
    )
    return (
        positive_edge_probability,
        scenario_returns,
        scenario_probabilities,
        model_health,
    )


def _account_risk_state(
    store: PaperAccountStore,
    account: PaperAccountSnapshot,
    now: datetime,
) -> tuple[float, float, float, float]:
    """Return equity, high-watermark, daily-start equity, daily realized P&L."""
    eastern_day = now.astimezone(EASTERN).date()
    realized_before_today = 0.0
    daily_realized = 0.0
    cumulative = 0.0
    high_watermark = account.starting_cash

    sells = [trade for trade in store.recent_trades(100) if str(trade.get("side")) == "SELL"]
    sells.sort(key=lambda trade: str(trade.get("timestamp") or ""))

    for trade in sells:
        try:
            timestamp = _parse_datetime(str(trade["timestamp"]))
            pnl = float(trade.get("realized_pnl") or 0.0)
        except (KeyError, TypeError, ValueError):
            continue
        trade_day = timestamp.astimezone(EASTERN).date()
        if trade_day > eastern_day:
            continue
        cumulative += pnl
        high_watermark = max(high_watermark, account.starting_cash + cumulative)
        if trade_day < eastern_day:
            realized_before_today += pnl
        elif trade_day == eastern_day:
            daily_realized += pnl

    daily_start_equity = account.starting_cash + realized_before_today
    # No new entry is evaluated while a position is open, so settled + unsettled
    # is the account equity relevant to the next-entry sizing decision.
    equity = account.settled_cash + account.unsettled_cash
    high_watermark = max(high_watermark, account.starting_cash, equity)
    return equity, high_watermark, daily_start_equity, daily_realized


class DynamicPaperAutoTrader(PaperAutoTrader):
    """Paper trader whose contract count is decided by the dynamic risk service."""

    def __init__(
        self,
        *,
        mode_getter: Callable[[], str],
        market_reader: SnapshotReader,
        account_store: PaperAccountStore,
        settings: PaperAutoSettings,
        risk_config: DynamicRiskConfig,
        event_multiplier_getter: Callable[[], float] | None = None,
    ) -> None:
        super().__init__(
            mode_getter=mode_getter,
            market_reader=market_reader,
            account_store=account_store,
            settings=settings,
        )
        self.risk_config = risk_config
        self.event_multiplier_getter = event_multiplier_getter or (
            lambda: _env_float("PAPER_EVENT_RISK_MULTIPLIER", 1.0, minimum=0.0, maximum=1.0)
        )

    def tick(self, now: datetime | None = None) -> dict[str, object]:
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        eastern = current.astimezone(EASTERN)
        mode = self.mode_getter().strip().upper()
        released = self.account_store.settle_due(eastern.date())
        positions = self.account_store.positions()

        if positions:
            result = self._manage_position(positions[0], current, mode)
            result["settled_released"] = released
            return self._finish(current, result)

        if mode not in {"PAPER", "SHADOW"}:
            return self._finish(
                current,
                {
                    "state": "IDLE",
                    "reason": f"paper automation idle while mode={mode}",
                    "mode": mode,
                    "last_signal": None,
                    "last_action": None,
                    "settled_released": released,
                    "risk": None,
                },
            )

        if not self._market_session(eastern):
            return self._finish(
                current,
                {
                    "state": "MARKET_CLOSED",
                    "reason": "waiting for the configured SPY 0DTE session",
                    "mode": mode,
                    "last_signal": None,
                    "last_action": None,
                    "settled_released": released,
                    "risk": None,
                },
            )

        cycles = self.market_reader.latest_cycles(2)
        if len(cycles) < 2:
            return self._finish(
                current,
                {
                    "state": "WAITING_HISTORY",
                    "reason": "need two completed option-chain snapshots before evaluating a paper entry",
                    "mode": mode,
                    "last_signal": None,
                    "last_action": None,
                    "settled_released": released,
                    "risk": None,
                },
            )

        latest, previous = cycles[0], cycles[1]
        age = max(0.0, (current - latest.received_at).total_seconds())
        if age > self.settings.max_data_age_seconds:
            return self._finish(
                current,
                {
                    "state": "WAITING_FRESH_DATA",
                    "reason": f"latest chain snapshot is {age:.1f}s old",
                    "mode": mode,
                    "data_age_seconds": age,
                    "last_signal": None,
                    "last_action": None,
                    "settled_released": released,
                    "risk": None,
                },
            )

        account = self.account_store.snapshot()
        if account.settled_cash <= 0:
            return self._finish(
                current,
                {
                    "state": "NO_SETTLED_CASH",
                    "reason": "paper account has no settled buying power",
                    "mode": mode,
                    "last_signal": None,
                    "last_action": None,
                    "settled_released": released,
                    "risk": None,
                },
            )

        signal = self._select_signal(latest, previous, account.settled_cash)
        if signal is None:
            return self._finish(
                current,
                {
                    "state": "NO_SIGNAL",
                    "reason": "no affordable liquid contract passed the bootstrap signal gates",
                    "mode": mode,
                    "last_signal": None,
                    "last_action": None,
                    "settled_released": released,
                    "risk": None,
                },
            )

        if self._in_cooldown(current):
            return self._finish(
                current,
                {
                    "state": "COOLDOWN",
                    "reason": "recent paper execution is still inside the re-entry cooldown",
                    "mode": mode,
                    "last_signal": signal.as_dict(),
                    "last_action": None,
                    "settled_released": released,
                    "risk": None,
                },
            )

        edge_probability, returns, probabilities, model_health = bootstrap_posterior_proxy(
            signal,
            self.settings,
        )
        equity, high_watermark, daily_start, daily_realized = _account_risk_state(
            self.account_store,
            account,
            current,
        )
        minutes_to_expiry = max(0.0, self._minutes_to_close(eastern))
        selected_quote = next((row for row in latest.rows if row.symbol == signal.symbol), None)
        feed_delay = None if selected_quote is None else selected_quote.feed_delay_seconds

        risk_decision = choose_dynamic_long_option_size(
            DynamicRiskRequest(
                account_equity=equity,
                high_watermark=high_watermark,
                settled_cash=account.settled_cash,
                daily_start_equity=daily_start,
                daily_realized_pnl=daily_realized,
                entry_cost_per_contract=signal.contract_cost,
                scenario_returns=returns,
                scenario_probabilities=probabilities,
                positive_edge_probability=edge_probability,
                liquidity_score=signal.liquidity_score,
                spread_fraction=signal.spread_fraction,
                quote_age_seconds=age,
                minutes_to_expiry=minutes_to_expiry,
                ask_size=0,
                existing_portfolio_es99_dollars=0.0,
                event_multiplier=self.event_multiplier_getter(),
                model_health_multiplier=model_health,
            ),
            self.risk_config,
        )
        risk_payload = {
            **risk_decision.as_dict(),
            "profile": RISK_PROFILE,
            "signal_model": SIGNAL_STRATEGY,
            "source_feed_delay_seconds": feed_delay,
            "scenario_returns": list(returns),
            "scenario_probabilities": list(probabilities),
        }

        if not risk_decision.allowed:
            state = "SHADOW_RISK_REJECTED" if mode == "SHADOW" else "RISK_REJECTED"
            return self._finish(
                current,
                {
                    "state": state,
                    "reason": f"dynamic risk rejected signal: {risk_decision.reason}",
                    "mode": mode,
                    "last_signal": signal.as_dict(),
                    "last_action": None,
                    "settled_released": released,
                    "risk": risk_payload,
                },
            )

        if mode == "SHADOW":
            return self._finish(
                current,
                {
                    "state": "SHADOW_SIGNAL",
                    "reason": (
                        f"qualified signal and risk approval for {risk_decision.contracts} contract(s); "
                        "SHADOW records no simulated position"
                    ),
                    "mode": mode,
                    "last_signal": signal.as_dict(),
                    "last_action": "WOULD_BUY",
                    "settled_released": released,
                    "risk": risk_payload,
                },
            )

        contracts = risk_decision.contracts
        stop = max(0.0, signal.contract_cost * (1.0 - self.settings.stop_loss_fraction))
        target = signal.contract_cost * (1.0 + self.settings.take_profit_fraction)
        risk_reason = (
            f"risk_profile={RISK_PROFILE} size={contracts} mode={risk_decision.sizing_mode} "
            f"ev=${risk_decision.expected_net_ev_per_contract:.2f}/contract "
            f"es99=${risk_decision.es99_per_contract:.2f}/contract"
        )
        position = self.account_store.open_long(
            symbol=signal.symbol,
            quantity=contracts,
            fill_price=signal.contract_cost,
            opened_at=current,
            entry_spot=signal.spot,
            strategy=POSITION_STRATEGY,
            stop_price=stop,
            target_price=target,
            max_hold_seconds=self.settings.max_hold_seconds,
            reason=f"{signal.reason}; {risk_reason}",
        )
        log.info(
            "paper dynamic buy symbol=%s quantity=%d cost=%.2f spot=%.2f sizing=%s es99=%.2f",
            signal.symbol,
            contracts,
            signal.contract_cost,
            signal.spot,
            risk_decision.sizing_mode,
            risk_decision.es99_per_contract,
        )
        return self._finish(
            current,
            {
                "state": "POSITION_OPENED",
                "reason": f"{signal.reason}; {risk_reason}",
                "mode": mode,
                "last_signal": signal.as_dict(),
                "last_action": "BUY",
                "position": position,
                "settled_released": released,
                "risk": risk_payload,
            },
        )

    @staticmethod
    def _finish(now: datetime, values: dict[str, object]) -> dict[str, object]:
        payload = {
            "enabled": True,
            "strategy": SIGNAL_STRATEGY,
            "risk_profile": RISK_PROFILE,
            "last_tick": now.isoformat(),
            **values,
        }
        _publish(**payload)
        return payload


def run_forever(mode_getter: Callable[[], str]) -> None:
    """Run dynamic autonomous SHADOW/PAPER sizing. There is no LIVE order path."""
    settings = PaperAutoSettings.from_env()
    risk_config = risk_config_from_env()
    market_path = Path(os.environ.get("COLLECTOR_DB_PATH", "/data/spy_0dte.sqlite"))
    account = PaperAccountStore(
        _paper_db_path(),
        default_starting_cash=_paper_starting_cash(),
    )
    trader = DynamicPaperAutoTrader(
        mode_getter=mode_getter,
        market_reader=SnapshotReader(market_path),
        account_store=account,
        settings=settings,
        risk_config=risk_config,
    )
    _publish(
        enabled=True,
        state="STARTING",
        reason="dynamic autonomous SHADOW/PAPER loop starting",
        strategy=SIGNAL_STRATEGY,
        risk_profile=RISK_PROFILE,
    )
    log.info(
        "paper dynamic autotrader started signal=%s risk=%s tick=%.2fs market_db=%s",
        SIGNAL_STRATEGY,
        RISK_PROFILE,
        settings.tick_seconds,
        market_path,
    )

    while True:
        started = time.monotonic()
        try:
            trader.tick()
        except Exception as exc:
            log.exception("dynamic paper automation tick failed")
            _publish(
                enabled=True,
                state="ERROR",
                reason=f"{type(exc).__name__}: {exc}",
                last_tick=datetime.now(timezone.utc).isoformat(),
            )
        elapsed = time.monotonic() - started
        time.sleep(max(0.05, settings.tick_seconds - elapsed))
