from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .greeks import model_greeks_from_quote
from .market import MarketSnapshot, OptionQuote
from .opportunity import evaluate_option_candidate
from .paper_account import PaperAccountStore
from .paper_autotrader import (
    EASTERN,
    PaperAutoSettings,
    SnapshotReader,
    _paper_db_path,
    _paper_starting_cash,
    _parse_datetime,
    _publish,
)
from .paper_dynamic_autotrader import _env_float, _env_int, risk_config_from_env
from .paper_ensemble_autotrader import (
    RISK_PROFILE,
    SIGNAL_STRATEGY,
    EnsemblePaperAutoTrader,
    _feed_delay_limit,
    _positive_return_probability,
    build_market_ensemble,
)
from .scenario import generate_return_scenarios

log = logging.getLogger("spy0dte.paper.dynamic_exit")
EXIT_PROFILE = "ensemble_dynamic_exit_v1"


def _minimum_hold_seconds() -> float:
    return _env_float("PAPER_DYNAMIC_EXIT_MIN_HOLD_SECONDS", 30.0, minimum=0.0, maximum=600.0)


def _confirmation_ticks() -> int:
    return _env_int("PAPER_DYNAMIC_EXIT_CONFIRM_TICKS", 3, minimum=1, maximum=30)


def _exit_horizon_minutes() -> float:
    return _env_float("PAPER_DYNAMIC_EXIT_HORIZON_MINUTES", 2.0, minimum=0.25, maximum=15.0)


def _reversal_support_threshold() -> float:
    return _env_float("PAPER_DYNAMIC_EXIT_REVERSAL_SUPPORT", 0.47, minimum=0.10, maximum=0.50)


def _minimum_positive_probability() -> float:
    return _env_float("PAPER_DYNAMIC_EXIT_MIN_POSITIVE_PROB", 0.48, minimum=0.10, maximum=0.90)


def _minimum_lower_edge() -> float:
    return _env_float("PAPER_DYNAMIC_EXIT_MIN_LOWER_EDGE", -0.002, minimum=-1.0, maximum=1.0)


def _minimum_expected_return() -> float:
    return _env_float("PAPER_DYNAMIC_EXIT_MIN_EXPECTED_RETURN", 0.0, minimum=-1.0, maximum=1.0)


@dataclass(frozen=True)
class DynamicExitMetrics:
    direction_support: float
    expected_return: float
    lower_confidence_edge: float
    positive_return_probability: float
    forecast_probability_up: float
    agreement_score: float
    calibration_score: float
    regime_match_score: float
    horizon_minutes: float

    def as_dict(self) -> dict[str, float]:
        return {
            "direction_support": self.direction_support,
            "expected_return": self.expected_return,
            "lower_confidence_edge": self.lower_confidence_edge,
            "positive_return_probability": self.positive_return_probability,
            "forecast_probability_up": self.forecast_probability_up,
            "agreement_score": self.agreement_score,
            "calibration_score": self.calibration_score,
            "regime_match_score": self.regime_match_score,
            "horizon_minutes": self.horizon_minutes,
        }


def classify_dynamic_exit(metrics: DynamicExitMetrics) -> str | None:
    """Return a soft exit reason when the remaining edge no longer justifies holding."""
    if metrics.direction_support < _reversal_support_threshold():
        return "direction_reversal"
    edge_failed = (
        metrics.expected_return <= _minimum_expected_return()
        and metrics.lower_confidence_edge <= _minimum_lower_edge()
        and metrics.positive_return_probability < _minimum_positive_probability()
    )
    return "edge_decay" if edge_failed else None


class DynamicExitEnsemblePaperAutoTrader(EnsemblePaperAutoTrader):
    """Ensemble paper trader with continuous model-driven exit decisions.

    Stop, target, session-close and maximum-hold remain independent hard rails.
    The former fixed max-hold behavior is now only a ceiling: before that ceiling,
    the ensemble repeatedly asks whether holding the contract still has positive,
    execution-aware edge and can exit early when that edge decays or reverses.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._exit_streaks: dict[str, tuple[str, int]] = {}

    def _remaining_edge(self, symbol: str, now: datetime) -> DynamicExitMetrics | None:
        cycles = self.market_reader.latest_cycles(90)
        if len(cycles) < 8:
            return None
        latest = cycles[0]
        row = next((item for item in latest.rows if item.symbol == symbol), None)
        if row is None or row.bid <= 0.0 or row.ask <= 0.0:
            row = self.market_reader.latest_quote(symbol)
        if row is None or row.bid <= 0.0 or row.ask <= 0.0:
            return None

        eastern = now.astimezone(EASTERN)
        minutes_to_expiry = max(0.0, self._minutes_to_close(eastern))
        horizon = min(_exit_horizon_minutes(), max(0.25, minutes_to_expiry - 0.5))
        forecast = build_market_ensemble(cycles, horizon)
        try:
            greeks = model_greeks_from_quote(
                right=row.right,  # type: ignore[arg-type]
                bid=row.bid,
                ask=row.ask,
                spot=latest.spot,
                strike=row.strike,
                minutes_to_expiry=minutes_to_expiry,
            )
        except ValueError:
            return None

        quote = OptionQuote(
            symbol=row.symbol,
            right=row.right,  # type: ignore[arg-type]
            strike=row.strike,
            bid=row.bid,
            ask=row.ask,
            delta=greeks.delta,
            gamma=greeks.gamma,
            theta=greeks.theta_per_day,
            vega=greeks.vega_per_vol_point,
            implied_volatility=greeks.implied_volatility,
            volume=row.volume,
            open_interest=row.open_interest,
            underlying_price=latest.spot,
            minutes_to_expiry=minutes_to_expiry,
        )
        receive_age = max(0.0, (now - row.cycle).total_seconds())
        market = MarketSnapshot(
            spot=latest.spot,
            bid=latest.spot,
            ask=latest.spot,
            realized_volatility=forecast.volatility,
            implied_volatility=greeks.implied_volatility,
            volume_ratio=1.0,
            minutes_to_close=minutes_to_expiry,
            data_age_seconds=max(receive_age, float(row.feed_delay_seconds or 0.0)),
            ood_score=1.0 - forecast.regime_match_score,
        )
        scenarios = generate_return_scenarios(forecast, points=31, tail_scale=1.20)
        opportunity = evaluate_option_candidate(
            quote,
            scenarios,
            forecast,
            market,
            horizon_minutes=horizon,
            fee_per_contract=self.settings.fee_per_contract,
            exit_slippage_spread_fraction=0.35,
            uncertainty_aversion=0.90,
        )
        support = forecast.probability_up if row.right == "call" else 1.0 - forecast.probability_up
        return DynamicExitMetrics(
            direction_support=support,
            expected_return=opportunity.expected_return,
            lower_confidence_edge=opportunity.candidate.lower_confidence_edge,
            positive_return_probability=_positive_return_probability(opportunity),
            forecast_probability_up=forecast.probability_up,
            agreement_score=forecast.agreement_score,
            calibration_score=forecast.calibration_score,
            regime_match_score=forecast.regime_match_score,
            horizon_minutes=horizon,
        )

    def _close_position(
        self,
        *,
        position: dict[str, object],
        quote,
        now: datetime,
        reason: str,
    ) -> dict[str, object]:
        symbol = str(position["symbol"])
        quantity = int(position["quantity"])
        exit_value = max(0.0, quote.bid * 100.0 - self.settings.fee_per_contract)
        closed = self.account_store.close_long(
            symbol=symbol,
            quantity=quantity,
            fill_price=exit_value,
            closed_at=now,
            trade_date=now.astimezone(EASTERN).date(),
            reason=reason,
        )
        self._exit_streaks.pop(symbol, None)
        log.info(
            "paper dynamic exit symbol=%s quantity=%d value=%.2f pnl=%.2f reason=%s",
            symbol,
            quantity,
            exit_value,
            float(closed["realized_pnl"]),
            reason,
        )
        return {
            "state": "POSITION_CLOSED",
            "reason": reason,
            "last_signal": None,
            "last_action": "SELL",
            "closed_trade": closed,
            "exit_profile": EXIT_PROFILE,
        }

    def _manage_position(
        self,
        position: dict[str, object],
        now: datetime,
        mode: str,
    ) -> dict[str, object]:
        symbol = str(position["symbol"])
        quote = self.market_reader.latest_quote(symbol)
        if quote is None:
            return {
                "state": "POSITION_WAITING_QUOTE",
                "reason": f"waiting for a quote for open paper position {symbol}",
                "mode": mode,
                "last_signal": None,
                "last_action": None,
                "position": position,
                "exit_profile": EXIT_PROFILE,
            }

        receive_age = max(0.0, (now - quote.cycle).total_seconds())
        feed_delay = quote.feed_delay_seconds
        limit = _feed_delay_limit()
        if feed_delay is None or feed_delay > limit or receive_age > self.settings.max_data_age_seconds:
            return {
                "state": "POSITION_FEED_DELAYED",
                "reason": (
                    f"refusing to mark/exit {symbol} from stale market data "
                    f"(receive_age={receive_age:.1f}s feed_delay="
                    f"{'unknown' if feed_delay is None else f'{feed_delay:.1f}s'})"
                ),
                "mode": mode,
                "last_signal": None,
                "last_action": None,
                "position": position,
                "exit_profile": EXIT_PROFILE,
            }

        quantity = int(position["quantity"])
        average_cost = float(position["average_cost"])
        stop_price = float(position.get("stop_price") or 0.0)
        target_price = float(position.get("target_price") or float("inf"))
        opened_text = position.get("opened_at")
        opened_at = _parse_datetime(str(opened_text)) if opened_text else now
        hard_max_hold = int(position.get("max_hold_seconds") or self.settings.max_hold_seconds)
        held_seconds = max(0.0, (now - opened_at).total_seconds())
        exit_value = max(0.0, quote.bid * 100.0 - self.settings.fee_per_contract)
        unrealized = (exit_value - average_cost) * quantity
        minutes_to_close = self._minutes_to_close(now.astimezone(EASTERN))

        hard_reason: str | None = None
        if exit_value <= stop_price:
            hard_reason = "stop_loss"
        elif exit_value >= target_price:
            hard_reason = "take_profit"
        elif held_seconds >= hard_max_hold:
            hard_reason = "hard_max_hold"
        elif minutes_to_close <= self.settings.force_exit_minutes_before_close:
            hard_reason = "session_close"
        if hard_reason is not None:
            result = self._close_position(position=position, quote=quote, now=now, reason=hard_reason)
            result["mode"] = mode
            return result

        dynamic_metrics: DynamicExitMetrics | None = None
        soft_reason: str | None = None
        if held_seconds >= _minimum_hold_seconds():
            dynamic_metrics = self._remaining_edge(symbol, now)
            if dynamic_metrics is not None:
                soft_reason = classify_dynamic_exit(dynamic_metrics)

        if soft_reason is None:
            self._exit_streaks.pop(symbol, None)
            streak = 0
        else:
            previous_reason, previous_count = self._exit_streaks.get(symbol, ("", 0))
            streak = previous_count + 1 if previous_reason == soft_reason else 1
            self._exit_streaks[symbol] = (soft_reason, streak)

        if soft_reason is not None and streak >= _confirmation_ticks():
            result = self._close_position(position=position, quote=quote, now=now, reason=soft_reason)
            result["mode"] = mode
            result["dynamic_exit"] = {
                **(dynamic_metrics.as_dict() if dynamic_metrics else {}),
                "confirmation_ticks": streak,
                "hard_max_hold_seconds": hard_max_hold,
            }
            return result

        if dynamic_metrics is None:
            dynamic_reason = (
                "minimum hold window" if held_seconds < _minimum_hold_seconds()
                else "waiting for enough model history to reassess remaining edge"
            )
        elif soft_reason is None:
            dynamic_reason = "remaining execution-aware edge still supports holding"
        else:
            dynamic_reason = f"{soft_reason} confirmation {streak}/{_confirmation_ticks()}"

        return {
            "state": "POSITION_OPEN",
            "reason": dynamic_reason,
            "mode": mode,
            "last_signal": None,
            "last_action": None,
            "position": {
                **position,
                "mark_value": exit_value,
                "unrealized_pnl": unrealized,
                "held_seconds": held_seconds,
                "quote_received_at": quote.cycle.isoformat(),
                "hard_max_hold_seconds": hard_max_hold,
                "dynamic_exit": {
                    **(dynamic_metrics.as_dict() if dynamic_metrics else {}),
                    "candidate_reason": soft_reason,
                    "confirmation_ticks": streak,
                    "required_confirmation_ticks": _confirmation_ticks(),
                },
            },
            "exit_profile": EXIT_PROFILE,
        }


def run_forever(mode_getter: Callable[[], str]) -> None:
    settings = PaperAutoSettings.from_env()
    risk_config = risk_config_from_env()
    market_path = Path(__import__("os").environ.get("COLLECTOR_DB_PATH", "/data/spy_0dte.sqlite"))
    account = PaperAccountStore(
        _paper_db_path(),
        default_starting_cash=_paper_starting_cash(),
    )
    trader = DynamicExitEnsemblePaperAutoTrader(
        mode_getter=mode_getter,
        market_reader=SnapshotReader(market_path),
        account_store=account,
        settings=settings,
        risk_config=risk_config,
    )
    _publish(
        enabled=True,
        state="STARTING",
        reason="ensemble paper loop starting with dynamic remaining-edge exits",
        strategy=SIGNAL_STRATEGY,
        risk_profile=RISK_PROFILE,
        exit_profile=EXIT_PROFILE,
    )
    log.info(
        "dynamic-exit paper autotrader started signal=%s risk=%s exit=%s tick=%.2fs market_db=%s",
        SIGNAL_STRATEGY,
        RISK_PROFILE,
        EXIT_PROFILE,
        settings.tick_seconds,
        market_path,
    )
    while True:
        started = time.monotonic()
        try:
            trader.tick()
        except Exception as exc:
            log.exception("dynamic-exit paper automation tick failed")
            _publish(
                enabled=True,
                state="ERROR",
                reason=f"{type(exc).__name__}: {exc}",
                exit_profile=EXIT_PROFILE,
                last_tick=datetime.now(timezone.utc).isoformat(),
            )
        elapsed = time.monotonic() - started
        time.sleep(max(0.05, settings.tick_seconds - elapsed))
