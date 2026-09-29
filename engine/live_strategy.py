"""Read-only strategy adapter. It cannot submit orders or mutate a paper account."""
from __future__ import annotations

import statistics
from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace

from .dynamic_risk import DynamicRiskConfig, DynamicRiskRequest, choose_dynamic_long_option_size
from .live_risk import EASTERN
from .paper_ai_autotrader import AIAugmentedDynamicExitTrader
from .paper_autotrader import PaperAutoSettings
from .paper_ensemble_autotrader import _positive_return_probability
from .paper_exit_policy_v2 import SaferDynamicExitPolicyMixin
from .paper_hold_value_exit import HoldValueExitPolicyMixin


class LiveStrategy(HoldValueExitPolicyMixin, SaferDynamicExitPolicyMixin, AIAugmentedDynamicExitTrader):
    def __init__(self, reader, audit_path: str):
        super().__init__(
            mode_getter=lambda: "LIVE", market_reader=reader,
            account_store=SimpleNamespace(path=audit_path),
            settings=PaperAutoSettings(max_data_age_seconds=3, fee_per_contract=0.70),
            risk_config=DynamicRiskConfig(
                kelly_fraction=0.10, maximum_contracts=1,
                trade_es99_fraction=0.02, aggregate_es99_fraction=0.02,
                maximum_premium_loss_fraction=0.05, maximum_capital_fraction=0.10,
                maximum_daily_loss_fraction=0.05, drawdown_soft_start=0.02,
                drawdown_zero_level=0.05, maximum_quote_age_seconds=3,
                maximum_spread_fraction=0.08, allow_minimum_viable_contract=False,
            ),
        )

    def tick(self, now=None):
        raise RuntimeError("LiveStrategy provides decisions only; use the live execution runner")

    def telemetry(self):
        blend = self._last_decision_blend
        consensus = self._last_decision_consensus
        return {"strategy": "quant_ai_live_v1", "risk_profile": "live_fractional_kelly_v1",
                "exit_profile": "hold_value_dynamic_exit_v1", "ai_advisory": self.ai_engine.status(),
                "ai_decision": {"active": bool(blend and blend.effective_weight > 0),
                    "blend": None if blend is None else blend.as_dict(),
                    "consensus": None if consensus is None else consensus.as_dict()}}

    def _context(self, *args, **kwargs):
        return replace(super()._context(*args, **kwargs), data_mode="LIVE_PRODUCTION")

    def _close_position(self, *, position, quote, now, reason):
        return {"state": "EXIT_REQUIRED", "reason": reason, "position": position}

    def _remaining_edge(self, symbol: str, now: datetime):
        metrics = super()._remaining_edge(symbol, now)
        row = self.market_reader.latest_quote(symbol)
        if metrics is None or row is None:
            return metrics
        return replace(metrics, direction_support=metrics.forecast_probability_up
                       if row.right == "call" else 1-metrics.forecast_probability_up)

    def entry(self, now, account, daily, limits):
        self._last_decision_blend = None
        self._last_decision_consensus = None
        cycles = self.market_reader.latest_cycles(90)
        if len(cycles) < 8:
            return None, {"state": "WAITING_HISTORY", "reason": "building fresh production market history"}
        self.risk_config = replace(self.risk_config, maximum_contracts=limits.max_contracts,
                                   maximum_capital_fraction=min(0.10, limits.max_account_exposure_pct))
        latest, previous = cycles[:2]
        age = (now-latest.received_at).total_seconds()
        if not 0 <= age <= 3:
            return None, {"state": "WAITING_FRESH_DATA", "reason": "production option quotes must be current"}
        selected = self._select_ensemble_opportunity(latest, previous, cycles, account["settled_cash"], age, now.astimezone(EASTERN))
        if selected is None:
            return None, {"state": "NO_SIGNAL", "reason": "no qualified live opportunity within risk limits"}
        signal, opportunity, forecast = selected
        q = self.market_reader.fresh_quote(signal.symbol, now)
        if q is None:
            return None, {"state": "WAITING_FRESH_DATA", "reason": "selected contract quote is stale"}
        decision = choose_dynamic_long_option_size(DynamicRiskRequest(
            account_equity=account["equity"], high_watermark=daily["high_watermark"],
            settled_cash=account["settled_cash"], daily_start_equity=daily["start_equity"],
            daily_realized_pnl=account["daily_pnl"],
            entry_cost_per_contract=opportunity.candidate.entry_cost_per_contract,
            scenario_returns=opportunity.candidate.scenario_returns,
            scenario_probabilities=opportunity.candidate.scenario_probabilities,
            positive_edge_probability=_positive_return_probability(opportunity),
            liquidity_score=opportunity.candidate.liquidity_score,
            spread_fraction=opportunity.candidate.spread_fraction,
            quote_age_seconds=max(age, q.feed_delay_seconds),
            minutes_to_expiry=self._minutes_to_close(now.astimezone(EASTERN)),
            model_health_multiplier=statistics.fmean((forecast.agreement_score, forecast.calibration_score, forecast.regime_match_score)),
        ), self.risk_config)
        telemetry = {"state": "SIGNAL" if decision.allowed else "RISK_BLOCKED",
                     "reason": decision.reason, "signal": signal.as_dict(), "risk": decision.as_dict()}
        return (None if not decision.allowed else {"symbol": signal.symbol, "quantity": decision.contracts,
                "reason": signal.reason, "cycle": latest.received_at.isoformat()}), telemetry

    def exit(self, position, now):
        cost = position["average_price"]*100 + self.settings.fee_per_contract
        result = self._manage_position({
            **position, "average_cost": cost,
            "stop_price": cost*(1-self.settings.stop_loss_fraction),
            "target_price": cost*(1+self.settings.take_profit_fraction),
            "max_hold_seconds": self.settings.max_hold_seconds,
        }, now, "LIVE")
        return result["reason"] if result["state"] == "EXIT_REQUIRED" else None
