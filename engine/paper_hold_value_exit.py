from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from .ai_decision import blend_ai_consensus_into_forecast
from .greeks import model_greeks_from_quote
from .hold_value import evaluate_hold_vs_sell_now
from .market import OptionQuote
from .paper_autotrader import EASTERN
from .paper_dynamic_autotrader import _env_float
from .paper_ensemble_autotrader import build_market_ensemble
from .scenario import generate_return_scenarios

HOLD_VALUE_EXIT_PROFILE = "ensemble_hold_value_exit_v3"


def _minimum_ai_confidence() -> float:
    return _env_float("PAPER_AI_MIN_CONFIDENCE", 0.45, minimum=0.0, maximum=1.0)


def _maximum_ai_weight() -> float:
    return _env_float("PAPER_AI_DECISION_WEIGHT", 0.35, minimum=0.0, maximum=0.75)


class HoldValueExitPolicyMixin:
    """Rebase soft exits on hold-vs-liquidate value for an existing position.

    The legacy remaining-edge evaluator is entry-oriented: it compares future
    option values with buying at the current ask. Once the option is already
    owned, that entry spread is sunk. This mixin keeps all existing hard exits,
    distinct-snapshot confirmation and reversal rules, but replaces the soft
    edge fields with the incremental value of holding versus selling now.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._hold_value_state: dict[str, dict[str, object]] = {}

    def _remaining_edge(self, symbol: str, now: datetime):
        metrics = super()._remaining_edge(symbol, now)
        if metrics is None:
            return None

        cycles = self.market_reader.latest_cycles(90)
        if len(cycles) < 8:
            return metrics
        latest = cycles[0]
        row = next((item for item in latest.rows if item.symbol == symbol), None)
        if row is None or row.bid <= 0.0 or row.ask <= 0.0:
            row = self.market_reader.latest_quote(symbol)
        if row is None or row.bid <= 0.0 or row.ask <= 0.0:
            return metrics

        horizon = max(0.25, float(metrics.horizon_minutes))
        try:
            quant_forecast = build_market_ensemble(cycles, horizon)
            advice = None
            blend = None
            if hasattr(self, "ai_engine") and hasattr(self, "_context"):
                advice = self.ai_engine.latest_or_request(
                    self._context(
                        cycles,
                        quant_forecast,
                        now.astimezone(EASTERN),
                        horizon,
                    )
                )
                blend = blend_ai_consensus_into_forecast(
                    quant_forecast,
                    advice,
                    minimum_confidence=_minimum_ai_confidence(),
                    maximum_ai_weight=_maximum_ai_weight(),
                )
            forecast = quant_forecast if blend is None else blend.forecast

            minutes_to_expiry = max(
                0.0,
                self._minutes_to_close(now.astimezone(EASTERN)),
            )
            greeks = model_greeks_from_quote(
                right=row.right,  # type: ignore[arg-type]
                bid=row.bid,
                ask=row.ask,
                spot=latest.spot,
                strike=row.strike,
                minutes_to_expiry=minutes_to_expiry,
            )
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
            scenarios = generate_return_scenarios(
                forecast,
                points=31,
                tail_scale=1.20,
            )
            hold = evaluate_hold_vs_sell_now(
                quote,
                scenarios,
                forecast,
                horizon_minutes=horizon,
                fee_per_contract=self.settings.fee_per_contract,
                future_exit_slippage_spread_fraction=0.35,
                uncertainty_aversion=0.90,
            )
        except ValueError:
            return metrics

        payload = {
            **hold.as_dict(),
            "market_cycle": latest.received_at.isoformat(),
            "quant_probability_up": quant_forecast.probability_up,
            "hybrid_probability_up": forecast.probability_up,
            "effective_ai_weight": 0.0 if blend is None else blend.effective_weight,
            "ai_confidence": None if advice is None else advice.confidence,
            "ai_disagreement": None if advice is None else advice.disagreement,
            "baseline": "sell_now_at_current_bid",
        }
        self._hold_value_state[symbol] = payload

        # Existing v2 classification can now operate on the economically correct
        # remaining-position question without changing its confirmation logic.
        return replace(
            metrics,
            expected_return=hold.expected_advantage_fraction,
            lower_confidence_edge=hold.lower_confidence_advantage_fraction,
            positive_return_probability=hold.probability_hold_beats_sell_now,
            agreement_score=forecast.agreement_score,
            calibration_score=forecast.calibration_score,
            regime_match_score=forecast.regime_match_score,
        )

    def _manage_position(self, position, now: datetime, mode: str):
        symbol = str(position["symbol"])
        result = super()._manage_position(position, now, mode)
        payload = self._hold_value_state.get(symbol)

        # Only attach telemetry from the same market snapshot. A hard stop may
        # execute before a fresh soft-edge calculation, and stale hold metrics
        # should never be presented as part of that exit decision.
        quote = self.market_reader.latest_quote(symbol)
        current_cycle = None if quote is None else quote.cycle.isoformat()
        fresh_payload = (
            payload
            if payload is not None and payload.get("market_cycle") == current_cycle
            else None
        )

        if fresh_payload is not None:
            position_payload = result.get("position")
            if isinstance(position_payload, dict):
                dynamic = dict(position_payload.get("dynamic_exit") or {})
                dynamic["hold_vs_sell_now"] = fresh_payload
                result["position"] = {
                    **position_payload,
                    "dynamic_exit": dynamic,
                }
            dynamic_exit = result.get("dynamic_exit")
            if isinstance(dynamic_exit, dict):
                result["dynamic_exit"] = {
                    **dynamic_exit,
                    "hold_vs_sell_now": fresh_payload,
                }

        result["exit_profile"] = HOLD_VALUE_EXIT_PROFILE
        if result.get("state") == "POSITION_CLOSED":
            self._hold_value_state.pop(symbol, None)
        return result
