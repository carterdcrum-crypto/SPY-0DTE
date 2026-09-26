from __future__ import annotations

import logging
import math
import os
import statistics
import threading
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import paper_ensemble_autotrader as ensemble_module
from .ai_consensus import AIAdvisoryEngine, AIContext
from .ai_decision import AIDecisionBlend, blend_ai_consensus_into_forecast
from .ensemble import EnsembleForecast
from .opportunity import OpportunityResult
from .paper_account import PaperAccountStore
from .paper_autotrader import (
    EASTERN,
    MarketCycle,
    PaperAutoSettings,
    PaperSignal,
    SnapshotReader,
    _paper_db_path,
    _paper_starting_cash,
    _publish,
)
from .paper_dynamic_autotrader import _clip, _env_float, risk_config_from_env
from .paper_dynamic_exit import (
    DynamicExitEnsemblePaperAutoTrader,
    DynamicExitMetrics,
    EXIT_PROFILE,
)
from .paper_ensemble_autotrader import (
    EnsemblePaperAutoTrader,
    RISK_PROFILE,
    SIGNAL_STRATEGY,
    build_market_ensemble,
)

log = logging.getLogger("spy0dte.paper.ai")
AI_PROFILE = "multi_provider_decision_v2"
_ENSEMBLE_SELECTION_LOCK = threading.Lock()


def _min_ai_confidence() -> float:
    return _env_float("PAPER_AI_MIN_CONFIDENCE", 0.45, minimum=0.0, maximum=1.0)


def _maximum_ai_decision_weight() -> float:
    return _env_float("PAPER_AI_DECISION_WEIGHT", 0.35, minimum=0.0, maximum=0.75)


def _veto_support_threshold() -> float:
    return _env_float("PAPER_AI_VETO_DIRECTION_SUPPORT", 0.38, minimum=0.05, maximum=0.49)


def _safe_return(new: float, old: float) -> float:
    if new <= 0.0 or old <= 0.0:
        return 0.0
    return math.log(new / old)


def _median_option_moves(latest: MarketCycle, previous: MarketCycle) -> tuple[float, float]:
    before = {row.symbol: row for row in previous.rows}
    calls: list[float] = []
    puts: list[float] = []
    for row in latest.rows:
        prior = before.get(row.symbol)
        if prior is None or prior.mid <= 0.0 or row.mid <= 0.0:
            continue
        move = row.mid / prior.mid - 1.0
        if row.right == "call":
            calls.append(move)
        elif row.right == "put":
            puts.append(move)
    return (
        statistics.median(calls) if calls else 0.0,
        statistics.median(puts) if puts else 0.0,
    )


class AIAugmentedDynamicExitTrader(DynamicExitEnsemblePaperAutoTrader):
    """Quant + configured-AI hybrid PAPER/SHADOW decision engine.

    The configured AI consensus is blended into the directional forecast before
    call/put selection, scenario generation, option ranking and dynamic sizing.
    Confidence, provider disagreement and the AI risk multiplier bound its
    effective weight. AI can also veto a candidate or reduce exit confidence,
    but it never bypasses account/risk rails or submits broker orders.
    """

    def __init__(self, *args, ai_engine: AIAdvisoryEngine | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.ai_engine = ai_engine or AIAdvisoryEngine()
        self._last_decision_blend: AIDecisionBlend | None = None
        self._last_decision_consensus = None

    def _context(
        self,
        cycles: tuple[MarketCycle, ...],
        forecast: EnsembleForecast,
        eastern: datetime,
        horizon: float,
    ) -> AIContext:
        chronological = tuple(reversed(cycles))
        spots = tuple(cycle.spot for cycle in chronological if cycle.spot > 0.0)
        returns = tuple(
            _safe_return(spots[index], spots[index - 1])
            for index in range(1, len(spots))
        )
        call_move, put_move = _median_option_moves(cycles[0], cycles[1])
        data_mode = (
            "DELAYED_SIMULATION"
            if os.environ.get("PAPER_DELAYED_SIMULATION", "").strip().lower()
            in {"1", "true", "yes", "on"}
            else "REALTIME_COMPATIBLE"
        )
        return AIContext(
            market_time=eastern.astimezone(timezone.utc).isoformat(),
            data_mode=data_mode,
            spot=cycles[0].spot,
            horizon_minutes=horizon,
            minutes_to_close=max(0.0, self._minutes_to_close(eastern)),
            quant_probability_up=forecast.probability_up,
            quant_expected_log_return=forecast.expected_log_return,
            quant_volatility=forecast.volatility,
            quant_agreement=forecast.agreement_score,
            quant_calibration=forecast.calibration_score,
            quant_regime_match=forecast.regime_match_score,
            recent_spot_returns=returns,
            option_call_median_move=call_move,
            option_put_median_move=put_move,
            event_state="unknown",
        )

    def _hybrid_decision_forecast(
        self,
        forecast: EnsembleForecast,
        cycles: tuple[MarketCycle, ...],
        eastern: datetime,
        horizon: float,
    ) -> AIDecisionBlend:
        advice = self.ai_engine.latest_or_request(
            self._context(cycles, forecast, eastern, horizon)
        )
        blend = blend_ai_consensus_into_forecast(
            forecast,
            advice,
            minimum_confidence=_min_ai_confidence(),
            maximum_ai_weight=_maximum_ai_decision_weight(),
        )
        self._last_decision_blend = blend
        self._last_decision_consensus = advice
        return blend

    def _select_ensemble_opportunity(
        self,
        latest: MarketCycle,
        previous: MarketCycle,
        cycles: tuple[MarketCycle, ...],
        settled_cash: float,
        receive_age: float,
        eastern: datetime,
    ) -> tuple[PaperSignal, OpportunityResult, EnsembleForecast] | None:
        horizon = min(
            _env_float(
                "PAPER_ENSEMBLE_HORIZON_MINUTES",
                5.0,
                minimum=0.5,
                maximum=30.0,
            ),
            max(0.5, self._minutes_to_close(eastern) - 1.0),
        )
        quant_forecast = build_market_ensemble(cycles, horizon)
        blend = self._hybrid_decision_forecast(
            quant_forecast,
            cycles,
            eastern,
            horizon,
        )

        # EnsemblePaperAutoTrader owns the full execution-aware candidate
        # selection path. Feed it the hybrid forecast for this one selection so
        # AI affects direction, scenarios, option ranking and downstream sizing
        # without duplicating that logic or giving the AI any execution path.
        original_builder = ensemble_module.build_market_ensemble
        with _ENSEMBLE_SELECTION_LOCK:
            ensemble_module.build_market_ensemble = lambda _cycles, _horizon: blend.forecast
            try:
                selected = super()._select_ensemble_opportunity(
                    latest,
                    previous,
                    cycles,
                    settled_cash,
                    receive_age,
                    eastern,
                )
            finally:
                ensemble_module.build_market_ensemble = original_builder

        if selected is None:
            return None
        signal, opportunity, forecast = selected
        advice = self._last_decision_consensus
        if advice is None or advice.confidence < _min_ai_confidence():
            return signal, opportunity, forecast

        direction_support = (
            advice.probability_up
            if signal.right == "call"
            else 1.0 - advice.probability_up
        )
        if direction_support < _veto_support_threshold():
            log.info(
                "AI veto symbol=%s right=%s support=%.3f confidence=%.3f providers=%s",
                signal.symbol,
                signal.right,
                direction_support,
                advice.confidence,
                ",".join(item.provider for item in advice.signals),
            )
            return None

        providers = ",".join(item.provider for item in advice.signals)
        signal = replace(
            signal,
            reason=(
                f"{signal.reason}; ai_decision quant_p_up={blend.quant_probability_up:.3f} "
                f"ai_p_up={blend.ai_probability_up:.3f} "
                f"hybrid_p_up={blend.forecast.probability_up:.3f} "
                f"ai_weight={blend.effective_weight:.3f} "
                f"confidence={advice.confidence:.2f} providers={providers}"
            ),
        )
        return signal, opportunity, forecast

    def _remaining_edge(
        self,
        symbol: str,
        now: datetime,
    ) -> DynamicExitMetrics | None:
        metrics = super()._remaining_edge(symbol, now)
        if metrics is None:
            return None
        cycles = self.market_reader.latest_cycles(90)
        if len(cycles) < 8:
            return metrics
        try:
            quant_forecast = build_market_ensemble(cycles, metrics.horizon_minutes)
        except ValueError:
            return metrics
        advice = self.ai_engine.latest_or_request(
            self._context(
                cycles,
                quant_forecast,
                now.astimezone(EASTERN),
                metrics.horizon_minutes,
            )
        )
        if advice is None or advice.confidence < _min_ai_confidence():
            return metrics

        latest = cycles[0]
        row = next((item for item in latest.rows if item.symbol == symbol), None)
        if row is None:
            row = self.market_reader.latest_quote(symbol)
        if row is None:
            return metrics
        ai_support = (
            advice.probability_up
            if row.right == "call"
            else 1.0 - advice.probability_up
        )
        return replace(
            metrics,
            direction_support=min(metrics.direction_support, ai_support),
            positive_return_probability=min(
                metrics.positive_return_probability,
                ai_support,
            ),
        )

    def _finish(self, now: datetime, values: dict[str, object]) -> dict[str, object]:
        blend = self._last_decision_blend
        consensus = self._last_decision_consensus
        enriched = {
            **values,
            "ai_profile": AI_PROFILE,
            "ai_advisory": self.ai_engine.status(),
            "ai_decision": {
                "role": "first_class_forecast_input",
                "maximum_weight": _maximum_ai_decision_weight(),
                "minimum_confidence": _min_ai_confidence(),
                "active": bool(blend is not None and blend.effective_weight > 0.0),
                "blend": None if blend is None else blend.as_dict(),
                "consensus": None if consensus is None else consensus.as_dict(),
            },
        }
        return EnsemblePaperAutoTrader._finish(now, enriched)


def run_forever(mode_getter: Callable[[], str]) -> None:
    settings = PaperAutoSettings.from_env()
    risk_config = risk_config_from_env()
    market_path = Path(os.environ.get("COLLECTOR_DB_PATH", "/data/spy_0dte.sqlite"))
    account = PaperAccountStore(
        _paper_db_path(),
        default_starting_cash=_paper_starting_cash(),
    )
    trader = AIAugmentedDynamicExitTrader(
        mode_getter=mode_getter,
        market_reader=SnapshotReader(market_path),
        account_store=account,
        settings=settings,
        risk_config=risk_config,
    )
    _publish(
        enabled=True,
        state="STARTING",
        reason="quant ensemble + dynamic risk/exit + first-class multi-provider AI decision loop starting",
        strategy=SIGNAL_STRATEGY,
        risk_profile=RISK_PROFILE,
        exit_profile=EXIT_PROFILE,
        ai_profile=AI_PROFILE,
        ai_advisory=trader.ai_engine.status(),
        ai_decision={
            "role": "first_class_forecast_input",
            "maximum_weight": _maximum_ai_decision_weight(),
            "minimum_confidence": _min_ai_confidence(),
            "active": False,
        },
    )
    log.info(
        "paper AI trader started signal=%s risk=%s exit=%s ai=%s providers=%s ai_max_weight=%.2f tick=%.2fs",
        SIGNAL_STRATEGY,
        RISK_PROFILE,
        EXIT_PROFILE,
        AI_PROFILE,
        ",".join(provider.name for provider in trader.ai_engine.providers) or "none",
        _maximum_ai_decision_weight(),
        settings.tick_seconds,
    )
    while True:
        started = time.monotonic()
        try:
            trader.tick()
        except Exception as exc:
            log.exception("AI paper automation tick failed")
            _publish(
                enabled=True,
                state="ERROR",
                reason=f"{type(exc).__name__}: {exc}",
                ai_profile=AI_PROFILE,
                last_tick=datetime.now(timezone.utc).isoformat(),
            )
        elapsed = time.monotonic() - started
        time.sleep(max(0.05, settings.tick_seconds - elapsed))
