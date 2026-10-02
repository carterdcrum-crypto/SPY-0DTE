import math


class OnlineLearner:
    """Point-in-time learner used only because historical LLM outputs do not exist."""

    def __init__(self):
        self.weights = [0.0] * 7
        self.updates = 0

    @staticmethod
    def _clip(value, low, high):
        return max(low, min(high, float(value)))

    @staticmethod
    def _sigmoid(value):
        value = max(-20.0, min(20.0, value))
        return 1.0 / (1.0 + math.exp(-value))

    def probability(self, features):
        score = sum(w * x for w, x in zip(self.weights, features))
        return self._clip(self._sigmoid(score), 0.10, 0.90)

    def update(self, features, outcome):
        prediction = self.probability(features)
        learning_rate = 0.035 / math.sqrt(1.0 + self.updates / 750.0)
        l2 = 0.0005
        error = float(outcome) - prediction
        for i, feature in enumerate(features):
            self.weights[i] += learning_rate * (error * feature - l2 * self.weights[i])
        self.updates += 1


class AdaptiveWeight:
    """Sequential Brier-loss weighting. The learner can never outweigh the quant."""

    def __init__(self):
        self.default = 0.45
        self.maximum = 0.45
        self.minimum = 0.05
        self.warmup = 12
        self.shrinkage = 24.0
        self.temperature = 0.10
        self.alpha = 1.0 - math.pow(0.5, 1.0 / 30.0)
        self.samples = 0
        self.quant_loss = None
        self.learned_loss = None

    @staticmethod
    def _clip(value, low, high):
        return max(low, min(high, float(value)))

    @property
    def learned_weight(self):
        if self.samples < self.warmup or self.quant_loss is None or self.learned_loss is None:
            return self.default
        q_score = math.exp(-self.quant_loss / self.temperature)
        l_score = math.exp(-self.learned_loss / self.temperature)
        denominator = q_score + l_score
        raw = 0.5 if denominator <= 0 else l_score / denominator
        target = self._clip(raw, self.minimum, self.maximum)
        reliability = self.samples / (self.samples + self.shrinkage)
        learned = (1.0 - reliability) * self.default + reliability * target
        return self._clip(learned, self.minimum, self.maximum)

    def observe(self, quant_p, learned_p, outcome):
        q_err = (float(quant_p) - float(outcome)) ** 2
        l_err = (float(learned_p) - float(outcome)) ** 2
        if self.quant_loss is None:
            self.quant_loss = q_err
            self.learned_loss = l_err
        else:
            self.quant_loss = (1.0 - self.alpha) * self.quant_loss + self.alpha * q_err
            self.learned_loss = (1.0 - self.alpha) * self.learned_loss + self.alpha * l_err
        self.samples += 1
