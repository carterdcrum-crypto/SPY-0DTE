import math
import statistics


def clip(value, low, high):
    return max(low, min(high, float(value)))


def std(values, floor=1e-6):
    values = tuple(float(v) for v in values)
    return max(floor, statistics.pstdev(values) if len(values) >= 2 else floor)


def normal_cdf(value):
    return 0.5 * (1.0 + math.erf(float(value) / math.sqrt(2.0)))


def probability_up(expected, sigma):
    return clip(normal_cdf(float(expected) / max(float(sigma), 1e-9)), 0.10, 0.90)


class QuantExperts:
    """Small interpretable experts with distinct failure modes."""

    names = ("trend", "reversion", "vol_surface", "microstructure")

    def __init__(self, horizon_minutes=5):
        self.horizon = max(1, int(horizon_minutes))

    def forecasts(self, closes, returns, chain_state):
        closes = tuple(float(x) for x in closes)
        returns = tuple(float(x) for x in returns)
        if len(returns) < 25 or len(closes) < 30:
            return {name: 0.5 for name in self.names}

        rv20 = std(returns[-20:], 0.0001)

        fast = statistics.fmean(returns[-5:])
        slow = statistics.fmean(returns[-20:])
        trend_expected = clip((0.65 * fast + 0.35 * slow) * self.horizon, -0.006, 0.006)
        trend_sigma = clip(rv20 * math.sqrt(self.horizon), 0.0002, 0.02)
        trend_p = probability_up(trend_expected, trend_sigma)

        logs = [math.log(max(1e-9, x)) for x in closes[-30:]]
        center = statistics.fmean(logs)
        deviation = logs[-1] - center
        reversion_expected = clip(-0.50 * deviation, -0.005, 0.005)
        reversion_p = probability_up(reversion_expected, trend_sigma)

        fast_rv = std(returns[-5:], 0.0001)
        vol_ratio = clip(fast_rv / max(rv20, 1e-9), 0.25, 4.0)
        skew = clip(chain_state.get("skew_signal", 0.0), -1.0, 1.0)
        vol_direction = clip(0.60 * skew + 0.15 * math.tanh(vol_ratio - 1.0) * math.copysign(1.0, slow or 1.0), -1.0, 1.0)
        vol_p = clip(0.5 + 0.18 * vol_direction, 0.20, 0.80)

        option_pressure = clip(chain_state.get("option_pressure", 0.0), -1.0, 1.0)
        quote_imbalance = clip(chain_state.get("quote_imbalance", 0.0), -1.0, 1.0)
        micro = clip(0.70 * option_pressure + 0.30 * quote_imbalance, -1.0, 1.0)
        micro_p = clip(0.5 + 0.20 * micro, 0.20, 0.80)

        return {
            "trend": trend_p,
            "reversion": reversion_p,
            "vol_surface": vol_p,
            "microstructure": micro_p,
        }

    def learner_features(self, closes, returns, volumes, chain_state, minute_of_day):
        closes = tuple(float(x) for x in closes)
        returns = tuple(float(x) for x in returns)
        volumes = tuple(float(x) for x in volumes)
        if len(returns) < 20:
            return (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        vol = max(0.0002, std(returns[-20:]))
        ret1 = returns[-1]
        ret5 = sum(returns[-5:])
        ret20 = sum(returns[-20:])
        volume_ratio = 1.0
        if len(volumes) >= 20:
            baseline = statistics.fmean(volumes[-20:])
            volume_ratio = volumes[-1] / max(baseline, 1e-9)
        pressure = clip(chain_state.get("option_pressure", 0.0), -1.0, 1.0)
        tod = clip((float(minute_of_day) - 195.0) / 195.0, -1.0, 1.0)
        return (
            1.0,
            clip(ret1 / vol, -4.0, 4.0),
            clip(ret5 / (vol * math.sqrt(5.0)), -4.0, 4.0),
            clip(ret20 / (vol * math.sqrt(20.0)), -4.0, 4.0),
            clip(math.log(max(volume_ratio, 1e-6)), -2.0, 2.0),
            pressure,
            tod,
        )
