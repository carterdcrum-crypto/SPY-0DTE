from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from .ai_validation import AIValidationMixin
from .paper_ai_autotrader import AIAugmentedDynamicExitTrader
from .paper_exit_policy_v2 import SaferDynamicExitPolicyMixin
from .paper_hold_value_exit import HoldValueExitPolicyMixin


class SaferAIAugmentedDynamicExitTrader(
    AIValidationMixin,
    HoldValueExitPolicyMixin,
    SaferDynamicExitPolicyMixin,
    AIAugmentedDynamicExitTrader,
):
    """Hybrid AI trader with forward validation and hold-value-aware exits.

    Entry decisions use the first-class quant/AI hybrid. The validation mixin
    records paired no-lookahead outcomes for quant-only, the former AI-veto
    behavior, and the hybrid. Soft exits compare holding with selling at the
    current bid instead of pretending the already-owned contract must be bought
    again at the ask.

    Direction-reversal exits remain anchored to the quantitative ensemble so a
    cached/slow LLM opinion cannot become a one-model emergency sell signal.
    """

    def _remaining_edge(self, symbol: str, now: datetime):
        metrics = super()._remaining_edge(symbol, now)
        if metrics is None:
            return None

        cycles = self.market_reader.latest_cycles(1)
        latest = cycles[0] if cycles else None
        row = None
        if latest is not None:
            row = next((item for item in latest.rows if item.symbol == symbol), None)
        if row is None:
            row = self.market_reader.latest_quote(symbol)
        if row is None:
            return metrics

        quant_direction_support = (
            metrics.forecast_probability_up
            if row.right == "call"
            else 1.0 - metrics.forecast_probability_up
        )
        return replace(metrics, direction_support=quant_direction_support)
