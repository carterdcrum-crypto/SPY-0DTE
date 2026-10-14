"""Pure-Python, causal QC research rules. No broker orders or synthetic fills.

V4 baseline signal logic is a faithful independent port of the locked
engine.regime_reclaim_frozen_signal setup conditions; production V4 is untouched.
Signals consume ONLY completed, same-day SPY minute bars.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import statistics


@dataclass(frozen=True)
class Signal:
    side: str
    setup: str
    rvol: float
    trend_bps: float
    efficiency: float


def ema(values, period):
    if not values or period < 2:
        raise ValueError("empty EMA input")
    value = float(values[0])
    alpha = 2.0 / (period + 1.0)
    for x in values[1:]:
        value = alpha * x + (1.0 - alpha) * value
    return value


def _bps(a, b):
    return 10000.0 * (a / b - 1.0) if b > 0 else 0.0


def realized_volatility(closes):
    """Annualized 1-minute log realized volatility, only past bars."""
    if len(closes) < 31 or min(closes[-31:]) <= 0:
        return 0.0
    log_returns = [math.log(b/a) for a, b in zip(closes[-31:-1], closes[-30:])]
    return statistics.stdev(log_returns) * math.sqrt(252.0 * 390.0)


def latest_signals(closes, highs, lows, volumes, variant="v5_current",
                   min_rvol=None):
    """Return closed-bar opportunities; no option data or future bars.

    'v4_original': frozen original trend/stretched reclaim only.
    'v5_current': 3 new components with predeclared entry thresholds.
    'v5_optimized': *unvalidated* alternative thresholds to challenge V5.
    """
    if variant not in ("v4_original", "v5_current", "v5_optimized",
                       "v5_unrestricted_research"):
        raise ValueError("unknown strategy variant")
    n = len(closes)
    if n < 36 or not (n == len(highs) == len(lows) == len(volumes)):
        return []
    if not all(math.isfinite(float(x)) and x > 0 for series in
               (closes[-36:], highs[-36:], lows[-36:], volumes[-36:])
               for x in series):
        return []
    mean_vol = statistics.fmean(volumes[-21:-1])
    rvol = volumes[-1] / mean_vol if mean_vol else 0.0
    threshold = (.75 if variant == "v4_original" else
                 1.5 if variant == "v5_current" else 1.25)
    if min_rvol is not None:
        threshold = float(min_rvol)
    if rvol < threshold:
        return []

    recent = closes[-36:]
    prior_fast = ema(recent[:-1], 8)
    prior_slow = ema(recent[:-1], 21)
    older_slow = ema(recent[:-11], 21)
    fast = ema(recent, 8)
    trend = _bps(prior_slow, older_slow)
    now = closes[-1]
    prev = closes[-2]
    path = sum(abs(closes[j]-closes[j-1]) for j in range(n-15, n))
    efficiency = abs(now - closes[-16]) / path if path else 0.0
    out = []

    def emit(side, setup):
        out.append(Signal(side, setup, rvol, trend, efficiency))

    if variant == "v4_original":
        if trend >= 2 and prev <= prior_fast and now > fast and now > prior_slow and now > prev and efficiency >= .18:
            emit("call", "trend_reclaim")
        elif trend <= -2 and prev >= prior_fast and now < fast and now < prior_slow and now < prev and efficiency >= .18:
            emit("put", "trend_reclaim")
        # Frozen band is based on 20 closes before the *previous* close.
        base = closes[-23:-3]  # equivalent to spots[i-22:i-2]
        avg = statistics.fmean(base)
        sigma = statistics.pstdev(base)
        if sigma > 0:
            low, high = avg - 1.2*sigma, avg + 1.2*sigma
            if prev < low and now > low and now > prev and efficiency < .60:
                emit("call", "stretch_reclaim")
            elif prev > high and now < high and now < prev and efficiency < .60:
                emit("put", "stretch_reclaim")
        return out

    # Component 1: sustained, high-RVOL momentum breakout.
    min_eff = .28 if variant == "v5_current" else .24
    momentum = _bps(now, closes[-4])
    if efficiency >= min_eff and now > max(highs[-13:-1]) and fast > prior_slow and trend >= 2 and momentum >= 3:
        emit("call", "momentum_ignition")
    elif efficiency >= min_eff and now < min(lows[-13:-1]) and fast < prior_slow and trend <= -2 and momentum <= -3:
        emit("put", "momentum_ignition")

    # Component 2: failed range break, independently observed reversal.
    high = max(highs[-23:-3])
    low = min(lows[-23:-3])
    if prev > high and now < high and now < prev and efficiency < .60:
        emit("put", "liquidity_reversal")
    elif prev < low and now > low and now > prev and efficiency < .60:
        emit("call", "liquidity_reversal")

    # Component 3: *prior* range compression followed by current breakout.
    old_range = max(highs[-33:-9]) - min(lows[-33:-9])
    compact_high, compact_low = max(highs[-9:-1]), min(lows[-9:-1])
    compact = compact_high - compact_low
    if old_range > 0 and compact / old_range <= (.60 if variant == "v5_current" else .75):
        if now > compact_high and now > prev and momentum >= 2 and fast > prior_slow:
            emit("call", "volatility_expansion")
        elif now < compact_low and now < prev and momentum <= -2 and fast < prior_slow:
            emit("put", "volatility_expansion")
    return out


def quality_score(signal, spread_fraction, delta, iv, realized_vol):
    """Original V4's six as-of features; V5 uses it as a heuristic, not P(win)."""
    trending = signal.setup in ("trend_reclaim", "momentum_ignition",
                                "volatility_expansion")
    return (int(abs(signal.trend_bps) >= 4 if trending else abs(signal.trend_bps) <= 6)
            + int(signal.efficiency >= .30 if trending else signal.efficiency <= .40)
            + int(signal.rvol >= 1.20)
            + int(spread_fraction <= .020)
            + int(.45 <= abs(delta) <= .55)
            + int(realized_vol > 0 and iv > 0 and iv/realized_vol <= 1.25))


def contract_quantity(variant, score, equity, settled_cash, peak_equity,
                      ask, *, bid_size, ask_size, start_cash=1000.,
                      maximum_exposure=.35, max_contracts=250,
                      cost_per_share=.01, fee_per_contract=.65):
    """Long options, full premium at risk, cash-account; never assume stop fill.

    The research-only arm removes EXPOSURE cap, not cash or quote-depth checks.
    The V4 baseline matches its integer 1/2/3 choice and extra-contract cap.
    """
    values = (equity, settled_cash, peak_equity, ask, start_cash,
              maximum_exposure, cost_per_share, fee_per_contract)
    if not all(math.isfinite(float(x)) for x in values):
        return 0
    if min(equity, settled_cash, peak_equity, ask, start_cash) <= 0:
        return 0
    if not 0 < maximum_exposure <= 1 or min(bid_size, ask_size, max_contracts) < 1:
        return 0
    debit = 100.0 * (ask + cost_per_share) + fee_per_contract
    cash_qty = max(0, int((settled_cash + 1e-8) // debit))
    depth_qty = min(int(bid_size), int(ask_size), int(max_contracts))
    if variant == "v4_original":
        if cash_qty == 0:
            return 0
        # Original V4 permits one baseline contract if cash covers debit.
        target = 3 if score >= 5 else 2 if score >= 4 else 1
        if target > 1 and equity / peak_equity < .96:
            target = 1
        if target > 1:
            limit = int(.35 * equity // debit)
            target = max(1, min(target, limit))
        return min(target, cash_qty, depth_qty)
    if variant == "v5_unrestricted_research":
        return min(cash_qty, depth_qty)
    if variant not in ("v5_current", "v5_optimized"):
        raise ValueError("unknown variant")
    if score < (4 if variant == "v5_current" else 3):
        return 0
    drawdown = max(0.0, 1 - equity / peak_equity)
    if drawdown >= .08:
        return 0  # research circuit breaker, not authorization to go live
    fraction = (.20 if variant == "v5_current" else .25)
    if score >= 5:
        fraction = .30 if variant == "v5_current" else .35
    if drawdown >= .04:
        fraction *= .25
    elif drawdown >= .02:
        fraction *= .50
    reserved = .25 * max(0.0, equity-start_cash)
    available = min(settled_cash, fraction * (equity-reserved),
                    maximum_exposure * equity)
    return max(0, min(cash_qty, depth_qty, int((available+1e-8)//debit)))


def score_equity(equity_curve, *, start_cash=1000., target=5000.):
    """Sampled curve diagnostics, not a proof of profitability or probability."""
    if not equity_curve:
        return None
    peak = start_cash
    worst = 0.0
    hit = None
    for day_index, equity in enumerate(equity_curve, 1):
        peak = max(peak, equity)
        worst = max(worst, (peak-equity) / peak if peak else 1.0)
        if hit is None and equity >= target:
            hit = day_index
    return {"ending_equity":round(equity_curve[-1],2),
            "max_eod_dd_pct":round(100*worst,2),
            "sessions_to_5k":hit}
