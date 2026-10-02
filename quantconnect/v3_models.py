from collections import deque
import math
import statistics

from v2_adaptive import FixedShareExpertCombiner, OnlineLogisticChallenger
from v2_experts import clip, std


class RegimeDetector:
    """Causal regime labels from information available at prediction time only."""

    names = ("trend", "chop", "high_vol", "balanced")

    def classify(self, returns, chain_state):
        returns = tuple(float(x) for x in returns)
        if len(returns) < 30:
            return "balanced"

        rv20 = std(returns[-20:], 0.0001)
        rv5 = std(returns[-5:], 0.0001)
        vol_ratio = rv5 / max(rv20, 1e-9)
        move10 = abs(sum(returns[-10:]))
        trend_strength = move10 / max(rv20 * math.sqrt(10.0), 1e-9)
        pressure = abs(float(chain_state.get("option_pressure", 0.0)))

        if vol_ratio >= 1.35 or (vol_ratio >= 1.20 and pressure >= 0.50):
            return "high_vol"
        if trend_strength >= 1.10:
            return "trend"
        if trend_strength <= 0.45 and vol_ratio <= 1.10:
            return "chop"
        return "balanced"


class RegimeExpertCombiner:
    """Global + regime-specific fixed-share weights, updated only after resolution."""

    def __init__(
        self,
        expert_names,
        eta=0.05,
        share=0.03,
        min_weight=0.05,
        max_weight=0.60,
        regime_blend=0.70,
    ):
        self.names = tuple(expert_names)
        kwargs = dict(
            eta=eta,
            share=share,
            min_weight=min_weight,
            max_weight=max_weight,
        )
        self.global_model = FixedShareExpertCombiner(self.names, **kwargs)
        self.by_regime = {
            regime: FixedShareExpertCombiner(self.names, **kwargs)
            for regime in RegimeDetector.names
        }
        self.regime_blend = clip(regime_blend, 0.0, 1.0)

    def probability(self, forecasts, regime):
        global_p = self.global_model.probability(forecasts)
        local = self.by_regime.get(regime)
        if local is None or local.samples < 50:
            return global_p
        local_p = local.probability(forecasts)
        return clip(
            self.regime_blend * local_p + (1.0 - self.regime_blend) * global_p,
            0.0,
            1.0,
        )

    def observe(self, forecasts, outcome, regime):
        self.global_model.observe(forecasts, outcome)
        local = self.by_regime.get(regime)
        if local is not None:
            local.observe(forecasts, outcome)

    def weights_for(self, regime):
        local = self.by_regime.get(regime)
        if local is None or local.samples < 50:
            return dict(self.global_model.weights)
        result = {}
        for name in self.names:
            result[name] = (
                self.regime_blend * local.weights[name]
                + (1.0 - self.regime_blend) * self.global_model.weights[name]
            )
        total = sum(result.values()) or 1.0
        return {name: value / total for name, value in result.items()}


class EvidenceGatedCombiner:
    """Quant-led blend. Learned weight rises only with resolved paired evidence."""

    def __init__(
        self,
        learned_prior=0.10,
        learned_min=0.0,
        learned_max=0.20,
        warmup=200,
        max_step=0.002,
        window=400,
        z_threshold=1.64,
    ):
        self.learned_prior = float(learned_prior)
        self.learned_min = float(learned_min)
        self.learned_max = float(learned_max)
        self.warmup = int(warmup)
        self.max_step = float(max_step)
        self.z_threshold = float(z_threshold)
        self.loss_advantages = deque(maxlen=max(50, int(window)))
        self.learned_weight = self.learned_prior
        self.quant_weight = 1.0 - self.learned_weight
        self.samples = 0
        self.last_z = 0.0

    def probability(self, quant_p, learned_p):
        return clip(
            self.quant_weight * float(quant_p)
            + self.learned_weight * float(learned_p),
            0.0,
            1.0,
        )

    def observe(self, quant_p, learned_p, outcome):
        y = float(outcome)
        q_loss = (float(quant_p) - y) ** 2
        l_loss = (float(learned_p) - y) ** 2
        # Positive means learned had lower loss than quant on this resolved sample.
        self.loss_advantages.append(q_loss - l_loss)
        self.samples += 1

        target = self.learned_prior
        self.last_z = 0.0
        if self.samples >= self.warmup and len(self.loss_advantages) >= 30:
            values = tuple(self.loss_advantages)
            mean_adv = statistics.fmean(values)
            sigma = statistics.pstdev(values) if len(values) > 1 else 0.0
            stderr = sigma / math.sqrt(len(values)) if sigma > 0.0 else 0.0
            if stderr > 0.0:
                self.last_z = mean_adv / stderr
                if self.last_z >= self.z_threshold:
                    target = self.learned_max
                elif self.last_z <= -self.z_threshold:
                    target = self.learned_min

        low = self.learned_weight - self.max_step
        high = self.learned_weight + self.max_step
        self.learned_weight = max(low, min(high, target))
        self.learned_weight = clip(
            self.learned_weight,
            self.learned_min,
            self.learned_max,
        )
        self.quant_weight = 1.0 - self.learned_weight


class V3LearnedChallenger(OnlineLogisticChallenger):
    """Same causal online learner as V2, with a richer fixed feature vector."""

    pass
