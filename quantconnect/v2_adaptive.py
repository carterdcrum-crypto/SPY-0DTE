import math


class OnlineLogisticChallenger:
    """Small sequential statistical challenger; not a historical LLM replay."""

    def __init__(self, feature_count=7):
        self.weights = [0.0] * int(feature_count)
        self.updates = 0

    @staticmethod
    def _clip(value, low, high):
        return max(low, min(high, float(value)))

    @staticmethod
    def _sigmoid(value):
        value = max(-20.0, min(20.0, float(value)))
        return 1.0 / (1.0 + math.exp(-value))

    def probability(self, features):
        score = sum(w * x for w, x in zip(self.weights, features))
        return self._clip(self._sigmoid(score), 0.10, 0.90)

    def update(self, features, outcome):
        prediction = self.probability(features)
        lr = 0.025 / math.sqrt(1.0 + self.updates / 1000.0)
        l2 = 0.0005
        error = float(outcome) - prediction
        for i, feature in enumerate(features):
            self.weights[i] += lr * (error * float(feature) - l2 * self.weights[i])
        self.updates += 1


class FixedShareExpertCombiner:
    """Online expert weighting using only already-resolved outcomes."""

    def __init__(
        self,
        names,
        eta=0.05,
        share=0.03,
        min_weight=0.05,
        max_weight=0.60,
    ):
        self.names = tuple(names)
        n = max(1, len(self.names))
        self.prior = {name: 1.0 / n for name in self.names}
        self.weights = dict(self.prior)
        self.eta = float(eta)
        self.share = float(share)
        self.min_weight = float(min_weight)
        self.max_weight = float(max_weight)
        self.samples = 0

    def probability(self, forecasts):
        total = sum(self.weights.get(name, 0.0) for name in self.names)
        if total <= 0:
            return 0.5
        return max(
            0.0,
            min(
                1.0,
                sum(self.weights[name] * float(forecasts[name]) for name in self.names)
                / total,
            ),
        )

    def observe(self, forecasts, outcome):
        raw = {}
        for name in self.names:
            loss = (float(forecasts[name]) - float(outcome)) ** 2
            raw[name] = self.weights[name] * math.exp(-self.eta * loss)
        total = sum(raw.values()) or 1.0
        mixed = {}
        for name in self.names:
            posterior = raw[name] / total
            mixed[name] = (1.0 - self.share) * posterior + self.share * self.prior[name]

        clipped = {
            name: max(self.min_weight, min(self.max_weight, mixed[name]))
            for name in self.names
        }
        normalizer = sum(clipped.values()) or 1.0
        self.weights = {name: clipped[name] / normalizer for name in self.names}
        self.samples += 1


class QuantLedCombiner:
    """Quant-first top-level combiner with a bounded learned challenger."""

    def __init__(
        self,
        quant_prior=0.90,
        learned_prior=0.10,
        learned_min=0.0,
        learned_max=0.25,
        warmup=100,
        max_step=0.005,
        eta=0.05,
        share=0.02,
    ):
        self.quant_prior = float(quant_prior)
        self.learned_prior = float(learned_prior)
        self.learned_min = float(learned_min)
        self.learned_max = float(learned_max)
        self.warmup = int(warmup)
        self.max_step = float(max_step)
        self.eta = float(eta)
        self.share = float(share)
        self.learned_weight = self.learned_prior
        self.quant_weight = 1.0 - self.learned_weight
        self.samples = 0
        self.quant_score = 1.0
        self.learned_score = 1.0

    def probability(self, quant_p, learned_p):
        return max(
            0.0,
            min(
                1.0,
                self.quant_weight * float(quant_p)
                + self.learned_weight * float(learned_p),
            ),
        )

    def observe(self, quant_p, learned_p, outcome):
        q_loss = (float(quant_p) - float(outcome)) ** 2
        l_loss = (float(learned_p) - float(outcome)) ** 2
        self.quant_score *= math.exp(-self.eta * q_loss)
        self.learned_score *= math.exp(-self.eta * l_loss)
        self.samples += 1

        if self.samples < self.warmup:
            target = self.learned_prior
        else:
            denom = self.quant_score + self.learned_score
            raw_learned = 0.5 if denom <= 0 else self.learned_score / denom
            shared = (
                (1.0 - self.share) * raw_learned
                + self.share * self.learned_prior
            )
            target = max(self.learned_min, min(self.learned_max, shared))

        low = self.learned_weight - self.max_step
        high = self.learned_weight + self.max_step
        self.learned_weight = max(low, min(high, target))
        self.learned_weight = max(
            self.learned_min,
            min(self.learned_max, self.learned_weight),
        )
        self.quant_weight = 1.0 - self.learned_weight
