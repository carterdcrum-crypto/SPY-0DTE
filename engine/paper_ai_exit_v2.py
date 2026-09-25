from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from .paper_ai_autotrader import AIAugmentedDynamicExitTrader
from .paper_exit_policy_v2 import SaferDynamicExitPolicyMixin


class SaferAIAugmentedDynamicExitTrader(
    SaferDynamicExitPolicyMixin,
    AIAugmentedDynamicExitTrader,
):
    """AI advisory + v2 exit policy.

    AI is still allowed to veto/reduce entry risk and to make an already-weak
    remaining-edge assessment more conservative. It is *not* allowed to create
    a direction-reversal exit by itself. Direction reversal remains anchored to
    the quantitative ensemble; that prevents a cached/slow LLM opinion from
    becoming a one-model emergency sell signal.
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
