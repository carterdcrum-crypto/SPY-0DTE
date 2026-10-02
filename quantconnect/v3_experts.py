import math
import statistics

from v2_experts import QuantExperts, clip, std


class V3QuantExperts(QuantExperts):
    """V2 interpretable experts plus causal implied/realized-vol context."""

    def forecasts(self, closes, returns, chain_state):
        base = super().forecasts(closes, returns, chain_state)
        returns = tuple(float(x) for x in returns)
        if len(returns) < 20:
            return base

        rv20 = std(returns[-20:], 0.0001) * math.sqrt(self.horizon)
        implied_move = max(0.0, float(chain_state.get("atm_straddle_pct", 0.0)))
        iv_rv = implied_move / max(rv20, 1e-9) if implied_move > 0.0 else 1.0
        skew = clip(chain_state.get("skew_signal", 0.0), -1.0, 1.0)
        pressure = clip(chain_state.get("option_pressure", 0.0), -1.0, 1.0)

        # Expensive implied movement should damp directional confidence; unusually
        # cheap implied movement lets confirmed skew/pressure matter a bit more.
        damp = 1.0 / (1.0 + max(0.0, iv_rv - 1.0) * 0.35)
        confirm = clip(0.60 * skew + 0.40 * pressure, -1.0, 1.0)
        raw = 0.5 + (float(base["vol_surface"]) - 0.5) * damp + 0.04 * confirm
        base["vol_surface"] = clip(raw, 0.20, 0.80)
        return base

    def learner_features(self, closes, returns, volumes, chain_state, minute_of_day):
        core = list(super().learner_features(
            closes,
            returns,
            volumes,
            chain_state,
            minute_of_day,
        ))
        returns = tuple(float(x) for x in returns)
        if len(returns) < 20:
            return tuple(core + [0.0, 0.0, 0.0])

        rv20 = std(returns[-20:], 0.0001)
        rv5 = std(returns[-5:], 0.0001)
        vol_ratio = clip(math.log(max(rv5 / max(rv20, 1e-9), 1e-6)), -2.0, 2.0)
        implied_move = max(0.0, float(chain_state.get("atm_straddle_pct", 0.0)))
        implied_ratio = clip(
            math.log(max(implied_move / max(rv20 * math.sqrt(self.horizon), 1e-9), 1e-6))
            if implied_move > 0.0 else 0.0,
            -2.0,
            2.0,
        )
        quote_imbalance = clip(chain_state.get("quote_imbalance", 0.0), -1.0, 1.0)
        return tuple(core + [vol_ratio, implied_ratio, quote_imbalance])
