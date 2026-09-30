from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Mapping


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


@dataclass(frozen=True)
class AdaptiveWeightConfig:
    """Conservative online weighting for quant vs AI consensus.

    The AI side is never allowed to become the majority. The learner only uses
    outcomes that were already resolved before the next forecast, so replay and
    live use the same no-lookahead ordering.
    """

    default_ai_weight: float = 0.45
    maximum_ai_weight: float = 0.45
    minimum_ai_weight: float = 0.05
    warmup_samples: int = 12
    shrinkage_samples: float = 24.0
    half_life_samples: float = 30.0
    loss_temperature: float = 0.10

    def validated(self) -> "AdaptiveWeightConfig":
        if not 0.0 <= self.minimum_ai_weight <= self.default_ai_weight:
            raise ValueError("minimum_ai_weight must be between 0 and default_ai_weight")
        if not self.default_ai_weight <= self.maximum_ai_weight < 0.5:
            raise ValueError("default/max AI weight must stay below 0.5")
        if self.warmup_samples < 0:
            raise ValueError("warmup_samples cannot be negative")
        if self.shrinkage_samples < 0:
            raise ValueError("shrinkage_samples cannot be negative")
        if self.half_life_samples <= 0:
            raise ValueError("half_life_samples must be positive")
        if self.loss_temperature <= 0:
            raise ValueError("loss_temperature must be positive")
        return self


@dataclass(frozen=True)
class AdaptiveReplayResult:
    samples: int
    quant_brier: float | None
    ai_brier: float | None
    fixed_45_brier: float | None
    adaptive_brier: float | None
    quant_directional_accuracy: float | None
    fixed_45_directional_accuracy: float | None
    adaptive_directional_accuracy: float | None
    mean_ai_weight: float | None
    final_ai_weight: float

    def as_dict(self) -> dict[str, object]:
        return {
            "samples": self.samples,
            "quant_brier": self.quant_brier,
            "ai_brier": self.ai_brier,
            "fixed_45_brier": self.fixed_45_brier,
            "adaptive_brier": self.adaptive_brier,
            "quant_directional_accuracy": self.quant_directional_accuracy,
            "fixed_45_directional_accuracy": self.fixed_45_directional_accuracy,
            "adaptive_directional_accuracy": self.adaptive_directional_accuracy,
            "mean_ai_weight": self.mean_ai_weight,
            "final_ai_weight": self.final_ai_weight,
            "method": "sequential_no_lookahead_ewma_brier",
        }


class AdaptiveQuantAIWeight:
    """Online learner that can only reduce AI from a quant-majority ceiling."""

    def __init__(self, config: AdaptiveWeightConfig = AdaptiveWeightConfig()) -> None:
        self.config = config.validated()
        self.samples = 0
        self.quant_loss: float | None = None
        self.ai_loss: float | None = None
        self._last_weight = self.config.default_ai_weight
        self._alpha = 1.0 - math.pow(0.5, 1.0 / self.config.half_life_samples)

    @property
    def current_ai_weight(self) -> float:
        if self.samples < self.config.warmup_samples:
            return self.config.default_ai_weight
        if self.quant_loss is None or self.ai_loss is None:
            return self.config.default_ai_weight

        # Lower loss gets more softmax weight. Equal performance would assign
        # 50/50, but the AI side is capped below 50% by design.
        q_score = math.exp(-self.quant_loss / self.config.loss_temperature)
        ai_score = math.exp(-self.ai_loss / self.config.loss_temperature)
        denominator = q_score + ai_score
        raw_ai = 0.5 if denominator <= 0 else ai_score / denominator
        target = _clip(
            raw_ai,
            self.config.minimum_ai_weight,
            self.config.maximum_ai_weight,
        )
        reliability = self.samples / (self.samples + self.config.shrinkage_samples) if (
            self.samples + self.config.shrinkage_samples
        ) > 0 else 1.0
        learned = (
            (1.0 - reliability) * self.config.default_ai_weight
            + reliability * target
        )
        return _clip(
            learned,
            self.config.minimum_ai_weight,
            self.config.maximum_ai_weight,
        )

    def observe(self, *, quant_probability_up: float, ai_probability_up: float, outcome_up: float) -> None:
        outcome = _clip(outcome_up, 0.0, 1.0)
        quant = _clip(quant_probability_up, 0.0, 1.0)
        ai = _clip(ai_probability_up, 0.0, 1.0)
        quant_error = (quant - outcome) ** 2
        ai_error = (ai - outcome) ** 2
        if self.quant_loss is None:
            self.quant_loss = quant_error
            self.ai_loss = ai_error
        else:
            self.quant_loss = (1.0 - self._alpha) * self.quant_loss + self._alpha * quant_error
            self.ai_loss = (1.0 - self._alpha) * self.ai_loss + self._alpha * ai_error
        self.samples += 1
        self._last_weight = self.current_ai_weight


def replay_adaptive_weights(
    rows: Iterable[Mapping[str, object]],
    *,
    config: AdaptiveWeightConfig = AdaptiveWeightConfig(),
    direction_threshold: float = 0.55,
) -> AdaptiveReplayResult:
    """Replay archived quant/AI forecasts strictly in timestamp order.

    Each row's blend weight is chosen before that row's outcome is observed.
    Required keys: quant_probability_up, ai_probability_up, outcome_up.
    """

    learner = AdaptiveQuantAIWeight(config)
    q_losses: list[float] = []
    ai_losses: list[float] = []
    fixed_losses: list[float] = []
    adaptive_losses: list[float] = []
    used_weights: list[float] = []
    q_correct = fixed_correct = adaptive_correct = comparable = 0

    def direction(probability: float) -> int:
        if probability >= direction_threshold:
            return 1
        if probability <= 1.0 - direction_threshold:
            return -1
        return 0

    for row in rows:
        quant = _clip(float(row["quant_probability_up"]), 0.0, 1.0)
        ai = _clip(float(row["ai_probability_up"]), 0.0, 1.0)
        outcome = _clip(float(row["outcome_up"]), 0.0, 1.0)

        # Critical anti-lookahead ordering: choose weight first, then score the
        # current forecast, then update the learner with the resolved outcome.
        adaptive_weight = learner.current_ai_weight
        fixed_weight = min(0.45, config.maximum_ai_weight)
        fixed_probability = (1.0 - fixed_weight) * quant + fixed_weight * ai
        adaptive_probability = (1.0 - adaptive_weight) * quant + adaptive_weight * ai

        q_losses.append((quant - outcome) ** 2)
        ai_losses.append((ai - outcome) ** 2)
        fixed_losses.append((fixed_probability - outcome) ** 2)
        adaptive_losses.append((adaptive_probability - outcome) ** 2)
        used_weights.append(adaptive_weight)

        # Ties in the realized spot are not directional outcomes.
        if outcome != 0.5:
            realized_direction = 1 if outcome > 0.5 else -1
            comparable += 1
            q_correct += int(direction(quant) == realized_direction)
            fixed_correct += int(direction(fixed_probability) == realized_direction)
            adaptive_correct += int(direction(adaptive_probability) == realized_direction)

        learner.observe(
            quant_probability_up=quant,
            ai_probability_up=ai,
            outcome_up=outcome,
        )

    count = len(q_losses)
    mean = lambda values: None if not values else sum(values) / len(values)
    accuracy = lambda correct: None if comparable == 0 else correct / comparable
    return AdaptiveReplayResult(
        samples=count,
        quant_brier=mean(q_losses),
        ai_brier=mean(ai_losses),
        fixed_45_brier=mean(fixed_losses),
        adaptive_brier=mean(adaptive_losses),
        quant_directional_accuracy=accuracy(q_correct),
        fixed_45_directional_accuracy=accuracy(fixed_correct),
        adaptive_directional_accuracy=accuracy(adaptive_correct),
        mean_ai_weight=mean(used_weights),
        final_ai_weight=learner.current_ai_weight,
    )
