import math
import statistics

from v2_adaptive import OnlineLogisticChallenger
from v2_experts import clip


class SpreadOutcomeLearner(OnlineLogisticChallenger):
    """Online classifier for positive executable spread P&L.

    It is trained only after the candidate's stop/take/time outcome resolves.
    """

    pass


class PayoffEstimator:
    """Causal EWMA of realized winner/loser return magnitudes."""

    def __init__(self, win_prior=0.50, loss_prior=0.35, alpha=0.03):
        self.avg_win = float(win_prior)
        self.avg_loss = float(loss_prior)
        self.alpha = clip(alpha, 0.001, 1.0)
        self.wins = 0
        self.losses = 0

    def update(self, return_fraction):
        value = float(return_fraction)
        if value > 0.0:
            magnitude = min(3.0, value)
            self.avg_win = (1.0 - self.alpha) * self.avg_win + self.alpha * magnitude
            self.wins += 1
        elif value < 0.0:
            magnitude = min(1.5, -value)
            self.avg_loss = (1.0 - self.alpha) * self.avg_loss + self.alpha * magnitude
            self.losses += 1

    def expected_return(self, win_probability):
        p = clip(win_probability, 0.0, 1.0)
        return p * self.avg_win - (1.0 - p) * self.avg_loss


class SpreadStats:
    """Probability calibration and winner classification for spread outcomes."""

    def __init__(self):
        self.brier_sum = 0.0
        self.observations = 0
        self.correct = 0
        self.predicted_positive = 0

    def observe(self, probability, outcome):
        p = clip(probability, 0.0, 1.0)
        y = 1.0 if float(outcome) > 0.5 else 0.0
        self.brier_sum += (p - y) ** 2
        self.observations += 1
        predicted = 1.0 if p >= 0.5 else 0.0
        self.correct += int(predicted == y)
        self.predicted_positive += int(predicted > 0.5)

    def metrics(self):
        return {
            "brier": self.brier_sum / self.observations if self.observations else 0.0,
            "observations": self.observations,
            "classification_accuracy_pct": 100.0 * self.correct / self.observations if self.observations else 0.0,
            "predicted_positive": self.predicted_positive,
        }


def normal_cdf(value):
    return 0.5 * (1.0 + math.erf(float(value) / math.sqrt(2.0)))


def _expected_call_intrinsic(spot, strike, mu, sigma):
    spot = max(1e-9, float(spot))
    strike = max(1e-9, float(strike))
    sigma = max(1e-8, float(sigma))
    d2 = (math.log(spot / strike) + float(mu)) / sigma
    d1 = d2 + sigma
    forward_mean = spot * math.exp(float(mu) + 0.5 * sigma * sigma)
    return max(0.0, forward_mean * normal_cdf(d1) - strike * normal_cdf(d2))


def _expected_put_intrinsic(spot, strike, mu, sigma):
    spot = max(1e-9, float(spot))
    strike = max(1e-9, float(strike))
    sigma = max(1e-8, float(sigma))
    d2 = (math.log(spot / strike) + float(mu)) / sigma
    d1 = d2 + sigma
    forward_mean = spot * math.exp(float(mu) + 0.5 * sigma * sigma)
    return max(0.0, strike * normal_cdf(-d2) - forward_mean * normal_cdf(-d1))


def quant_candidate_estimate(candidate, spot, direction_probability, sigma_horizon):
    """Interpretable quant estimate for one debit spread.

    The directional probability is converted to a log-return mean under a
    normal-return assumption. Expected vertical intrinsic value and the
    probability of crossing the debit breakeven are then computed directly.
    No future quotes are used.
    """

    p_up = clip(direction_probability, 0.01, 0.99)
    sigma = max(1e-6, float(sigma_horizon))
    z = statistics.NormalDist().inv_cdf(p_up)
    mu = sigma * z

    long_strike = float(candidate["long_strike"])
    short_strike = float(candidate["short_strike"])
    debit = float(candidate["debit"])
    direction = int(candidate["direction"])

    if direction > 0:
        expected_long = _expected_call_intrinsic(spot, long_strike, mu, sigma)
        expected_short = _expected_call_intrinsic(spot, short_strike, mu, sigma)
        expected_payoff = max(0.0, expected_long - expected_short) * 100.0
        breakeven = long_strike + debit / 100.0
        threshold = (math.log(max(breakeven, 1e-9) / max(float(spot), 1e-9)) - mu) / sigma
        win_probability = 1.0 - normal_cdf(threshold)
    else:
        expected_long = _expected_put_intrinsic(spot, long_strike, mu, sigma)
        expected_short = _expected_put_intrinsic(spot, short_strike, mu, sigma)
        expected_payoff = max(0.0, expected_long - expected_short) * 100.0
        breakeven = long_strike - debit / 100.0
        if breakeven <= 0.0:
            win_probability = 0.0
        else:
            threshold = (math.log(breakeven / max(float(spot), 1e-9)) - mu) / sigma
            win_probability = normal_cdf(threshold)

    expected_value = expected_payoff - debit
    return {
        "win_probability": clip(win_probability, 0.01, 0.99),
        "expected_value_dollars": float(expected_value),
        "mu": float(mu),
        "sigma": float(sigma),
    }


def spread_features(candidate, quant_win_probability, chain_state, iv_rv_ratio, minute_of_day, regime):
    direction = int(candidate["direction"])
    debit_fraction = float(candidate["debit_fraction"])
    distance = float(candidate["distance_fraction"])
    relative_spread = float(candidate["total_relative_spread"])
    aligned_pressure = direction * float(chain_state.get("option_pressure", 0.0))
    aligned_imbalance = direction * float(chain_state.get("quote_imbalance", 0.0))
    aligned_skew = direction * float(chain_state.get("skew_signal", 0.0))
    tod = clip((float(minute_of_day) - 195.0) / 195.0, -1.0, 1.0)
    return (
        1.0,
        clip((float(quant_win_probability) - 0.5) * 2.0, -1.0, 1.0),
        clip((debit_fraction - 0.5) * 2.0, -1.0, 1.0),
        clip(distance / 0.02, 0.0, 2.0),
        clip(relative_spread / 0.20, 0.0, 2.0),
        clip(math.log(max(float(iv_rv_ratio), 1e-6)), -2.0, 2.0),
        clip(aligned_pressure, -1.0, 1.0),
        clip(aligned_imbalance, -1.0, 1.0),
        clip(aligned_skew, -1.0, 1.0),
        tod,
        1.0 if regime == "trend" else 0.0,
        1.0 if regime == "high_vol" else 0.0,
        1.0 if regime == "chop" else 0.0,
    )
