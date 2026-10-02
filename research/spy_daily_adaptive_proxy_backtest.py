from __future__ import annotations

"""Research-only, no-lookahead proxy test for the SPY 0DTE decision stack.

Why this exists
---------------
Free historical *underlying* SPY data is easy to obtain, while a complete
point-in-time archive of the LLM outputs and intraday SPY 0DTE option chain used
by the live system does not exist in this project.  This script therefore does
NOT pretend to be an exact historical replay of the production option trader.

It answers a narrower question without hindsight leakage:

* Does the quant-majority/adaptive weighting architecture predict the direction
  of unseen SPY sessions?
* What would a $100 micro-account have looked like under a deliberately simple,
  conservative synthetic 0DTE option execution model?

The newest block (2024-01-01 onward) is a locked holdout.  Model/weight updates
are sequential: each day's prediction is made before that day's close is used.
No holdout result is used to choose a parameter.
"""

import csv
import io
import json
import math
import statistics
from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable

import requests

from engine.adaptive_weighting import AdaptiveQuantAIWeight, AdaptiveWeightConfig
from engine.ensemble import ModelForecast, combine_forecasts

SPY_URL = (
    "https://raw.githubusercontent.com/OStochastic/"
    "Daily-SPY-data-from-2000-2025/main/spy_data.csv"
)
VIX_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=VIXCLS"

HOLDOUT_START = date(2024, 1, 1)
LEARNING_START = date(2010, 1, 1)
DIRECTION_THRESHOLD = 0.55
STARTING_CASH = 100.0

# These execution assumptions are declared before looking at the locked holdout.
BASE_IV_MULTIPLIER = 1.25
BASE_SPREAD_FRACTION = 0.08
BASE_TARGET_DELTA = 0.15
FEE_PER_CONTRACT = 0.65
MAX_PREMIUM_FRACTION = 0.35
MAX_CAPITAL_FRACTION = 0.90
DRAWDOWN_HALT = 0.25
MAX_MONEYNESS_FRACTION = 0.025
RISK_FREE_RATE = 0.045
TIME_TO_EXPIRY_YEARS = 6.5 / (24.0 * 365.0)


@dataclass(frozen=True)
class Day:
    day: date
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class ForecastRow:
    day: date
    open: float
    close: float
    vix: float
    quant_p: float
    learned_p: float
    fixed_p: float
    adaptive_p: float
    adaptive_ai_weight: float


@dataclass
class StrategyStats:
    name: str
    brier_sum: float = 0.0
    observations: int = 0
    active: int = 0
    correct: int = 0

    def add(self, probability: float, outcome_up: float) -> None:
        self.brier_sum += (probability - outcome_up) ** 2
        self.observations += 1
        direction = signal_direction(probability)
        if direction == 0:
            return
        self.active += 1
        realized = 1 if outcome_up > 0.5 else -1 if outcome_up < 0.5 else 0
        self.correct += int(realized != 0 and direction == realized)

    def as_dict(self) -> dict[str, float | int | None]:
        return {
            "days": self.observations,
            "brier": self.brier_sum / self.observations if self.observations else None,
            "active_signals": self.active,
            "signal_coverage": self.active / self.observations if self.observations else None,
            "directional_accuracy": self.correct / self.active if self.active else None,
        }


class OnlineLogisticProxy:
    """Small point-in-time learner standing in for the unavailable LLM archive.

    This is intentionally simple.  It is not labelled as historical AI.  It
    exists only to test whether the adaptive quant-vs-learned weighting mechanism
    behaves sensibly when the learned side improves or deteriorates over time.
    """

    def __init__(self) -> None:
        self.weights = [0.0] * 7
        self.updates = 0

    @staticmethod
    def _sigmoid(value: float) -> float:
        value = max(-20.0, min(20.0, value))
        return 1.0 / (1.0 + math.exp(-value))

    def probability(self, features: tuple[float, ...]) -> float:
        score = sum(w * x for w, x in zip(self.weights, features))
        return max(0.10, min(0.90, self._sigmoid(score)))

    def update(self, features: tuple[float, ...], outcome: float) -> None:
        prediction = self.probability(features)
        # Fixed schedule; never tuned on the holdout.
        learning_rate = 0.035 / math.sqrt(1.0 + self.updates / 750.0)
        l2 = 0.0005
        error = outcome - prediction
        for index, feature in enumerate(features):
            self.weights[index] += learning_rate * (
                error * feature - l2 * self.weights[index]
            )
        self.updates += 1


def fetch_text(url: str) -> str:
    response = requests.get(url, timeout=45)
    response.raise_for_status()
    return response.text


def load_spy() -> list[Day]:
    text = fetch_text(SPY_URL)
    lines = [line for line in text.splitlines() if line.strip()]
    # Source has three yfinance-style header rows before the observations.
    rows: list[Day] = []
    for line in lines[3:]:
        values = next(csv.reader([line]))
        if len(values) < 6:
            continue
        try:
            rows.append(
                Day(
                    day=date.fromisoformat(values[0]),
                    close=float(values[1]),
                    high=float(values[2]),
                    low=float(values[3]),
                    open=float(values[4]),
                    volume=float(values[5]),
                )
            )
        except (ValueError, IndexError):
            continue
    rows.sort(key=lambda item: item.day)
    if not rows:
        raise RuntimeError("SPY download parsed no rows")
    return rows


def load_vix() -> dict[date, float]:
    text = fetch_text(VIX_URL)
    reader = csv.DictReader(io.StringIO(text))
    result: dict[date, float] = {}
    for row in reader:
        raw_date = row.get("DATE") or row.get("observation_date") or ""
        raw_value = row.get("VIXCLS") or ""
        if not raw_date or raw_value in {"", "."}:
            continue
        try:
            result[date.fromisoformat(raw_date)] = float(raw_value)
        except ValueError:
            continue
    if not result:
        raise RuntimeError("VIX download parsed no rows")
    return result


def clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


def pstdev(values: Iterable[float], floor: float = 1e-6) -> float:
    seq = tuple(values)
    return max(floor, statistics.pstdev(seq) if len(seq) >= 2 else floor)


def normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def probability_up(expected: float, sigma: float) -> float:
    return clip(normal_cdf(expected / max(sigma, 1e-9)), 0.10, 0.90)


def recent_brier(returns: tuple[float, ...], window: int) -> float:
    if len(returns) < window + 6:
        return 0.25
    scores: list[float] = []
    for index in range(window, len(returns)):
        history = returns[index - window : index]
        expected = statistics.fmean(history)
        sigma = pstdev(history)
        p = probability_up(expected, sigma)
        outcome = 1.0 if returns[index] > 0.0 else 0.0
        scores.append((p - outcome) ** 2)
    return statistics.fmean(scores[-30:]) if scores else 0.25


def calibration_from_brier(brier: float) -> float:
    return clip(0.75 - 1.2 * (brier - 0.18), 0.35, 0.92)


def regime_match(returns: tuple[float, ...]) -> float:
    if len(returns) < 12:
        return 0.65
    fast = pstdev(returns[-8:])
    slow = pstdev(returns[-min(40, len(returns)) :])
    ratio = fast / max(slow, 1e-9)
    return clip(math.exp(-0.65 * abs(math.log(max(ratio, 1e-6)))), 0.35, 1.0)


def quant_probability(days: list[Day], index: int) -> float:
    """Daily analogue of the app's fixed fast/trend/reversion quant ensemble.

    Only data available at today's open is used.  The current opening gap is
    allowed; today's high/low/close are not.
    """
    if index < 45:
        return 0.5
    prior = days[:index]
    prior_returns = tuple(
        math.log(prior[i].close / prior[i - 1].close)
        for i in range(max(1, len(prior) - 80), len(prior))
        if prior[i].close > 0 and prior[i - 1].close > 0
    )
    gap = math.log(days[index].open / prior[-1].close)
    returns = prior_returns + (gap,)
    regime = regime_match(returns)

    forecasts: list[ModelForecast] = []
    for name, window in (("fast_momentum", 6), ("intraday_trend", 20)):
        history = returns[-min(window, len(returns)) :]
        expected = clip(statistics.fmean(history), -0.008, 0.008)
        sigma = clip(pstdev(history), 0.00015, 0.04)
        brier = recent_brier(returns, max(3, min(window, 20)))
        forecasts.append(
            ModelForecast(
                name=name,
                probability_up=probability_up(expected, sigma),
                expected_log_return=expected,
                volatility=sigma,
                validation_loss=brier,
                calibration_score=calibration_from_brier(brier),
                regime_match_score=regime,
            )
        )

    close_history = [item.close for item in prior[-30:] if item.close > 0]
    log_history = [math.log(value) for value in close_history]
    center = statistics.fmean(log_history)
    deviation = math.log(days[index].open) - center
    expected = clip(-0.65 * deviation, -0.006, 0.006)
    sigma = clip(pstdev(returns[-30:]), 0.00015, 0.04)
    reversion_brier = max(0.20, recent_brier(tuple(-value for value in returns), 12))
    forecasts.append(
        ModelForecast(
            name="short_mean_reversion",
            probability_up=probability_up(expected, sigma),
            expected_log_return=expected,
            volatility=sigma,
            validation_loss=reversion_brier,
            calibration_score=clip(calibration_from_brier(reversion_brier) * 0.90, 0.30, 0.85),
            regime_match_score=regime,
        )
    )
    return combine_forecasts(forecasts).probability_up


def proxy_features(days: list[Day], index: int) -> tuple[float, ...]:
    prior = days[:index]
    if len(prior) < 25:
        return (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    returns = [
        math.log(prior[i].close / prior[i - 1].close)
        for i in range(max(1, len(prior) - 30), len(prior))
    ]
    vol = max(0.002, pstdev(returns[-20:]))
    gap = math.log(days[index].open / prior[-1].close)
    ret1 = returns[-1]
    ret5 = math.log(prior[-1].close / prior[-6].close)
    ret20 = math.log(prior[-1].close / prior[-21].close)
    prev_range = (prior[-1].high - prior[-1].low) / max(prior[-1].close, 1e-9)
    volume_window = [item.volume for item in prior[-20:] if item.volume > 0]
    volume_ratio = prior[-1].volume / statistics.fmean(volume_window) if volume_window else 1.0
    return (
        1.0,
        clip(gap / vol, -4.0, 4.0),
        clip(ret1 / vol, -4.0, 4.0),
        clip(ret5 / (vol * math.sqrt(5.0)), -4.0, 4.0),
        clip(ret20 / (vol * math.sqrt(20.0)), -4.0, 4.0),
        clip(prev_range / vol, 0.0, 6.0),
        clip(math.log(max(volume_ratio, 1e-6)), -2.0, 2.0),
    )


def outcome_for(day: Day) -> float:
    if day.close > day.open:
        return 1.0
    if day.close < day.open:
        return 0.0
    return 0.5


def signal_direction(probability: float) -> int:
    if probability >= DIRECTION_THRESHOLD:
        return 1
    if probability <= 1.0 - DIRECTION_THRESHOLD:
        return -1
    return 0


def previous_vix(day: date, vix: dict[date, float], fallback: float) -> float:
    candidates = [value for key, value in vix.items() if key < day]
    return candidates[-1] if candidates else fallback


def build_forecasts(days: list[Day], vix: dict[date, float]) -> list[ForecastRow]:
    learned = OnlineLogisticProxy()
    adaptive = AdaptiveQuantAIWeight(
        AdaptiveWeightConfig(
            default_ai_weight=0.45,
            maximum_ai_weight=0.45,
            minimum_ai_weight=0.05,
            warmup_samples=12,
            shrinkage_samples=24.0,
            half_life_samples=30.0,
            loss_temperature=0.10,
        )
    )
    rows: list[ForecastRow] = []
    last_vix = 20.0

    for index, day in enumerate(days):
        if index < 45 or day.day < LEARNING_START:
            continue
        features = proxy_features(days, index)
        q = quant_probability(days, index)
        learned_p = learned.probability(features)
        weight = adaptive.current_ai_weight
        fixed_p = 0.55 * q + 0.45 * learned_p
        adaptive_p = (1.0 - weight) * q + weight * learned_p
        outcome = outcome_for(day)

        # VIX used for this session is yesterday's close only.
        vix_candidates = [value for key, value in vix.items() if key < day.day]
        if vix_candidates:
            last_vix = vix_candidates[-1]

        if day.day >= HOLDOUT_START:
            rows.append(
                ForecastRow(
                    day=day.day,
                    open=day.open,
                    close=day.close,
                    vix=last_vix,
                    quant_p=q,
                    learned_p=learned_p,
                    fixed_p=fixed_p,
                    adaptive_p=adaptive_p,
                    adaptive_ai_weight=weight,
                )
            )

        # Critical ordering: observe/update only after the day's prediction.
        if outcome != 0.5:
            learned.update(features, outcome)
            adaptive.observe(
                quant_probability_up=q,
                ai_probability_up=learned_p,
                outcome_up=outcome,
            )

    return rows


def norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def bs_price_delta(
    *,
    spot: float,
    strike: float,
    sigma: float,
    right: int,
) -> tuple[float, float]:
    t = TIME_TO_EXPIRY_YEARS
    sigma = max(0.01, sigma)
    if spot <= 0 or strike <= 0:
        return 0.0, 0.0
    root_t = math.sqrt(t)
    d1 = (
        math.log(spot / strike)
        + (RISK_FREE_RATE + 0.5 * sigma * sigma) * t
    ) / (sigma * root_t)
    d2 = d1 - sigma * root_t
    nd1 = normal_cdf(d1)
    nd2 = normal_cdf(d2)
    discount = math.exp(-RISK_FREE_RATE * t)
    if right > 0:
        price = spot * nd1 - strike * discount * nd2
        delta = nd1
    else:
        price = strike * discount * normal_cdf(-d2) - spot * normal_cdf(-d1)
        delta = nd1 - 1.0
    return max(0.0, price), delta


def choose_contract(
    *,
    spot: float,
    vix: float,
    right: int,
    equity: float,
    settled_cash: float,
    iv_multiplier: float,
    spread_fraction: float,
    target_delta: float,
) -> tuple[float, float, float] | None:
    sigma = max(0.08, vix / 100.0 * iv_multiplier)
    max_cost = min(
        equity * MAX_PREMIUM_FRACTION,
        settled_cash * MAX_CAPITAL_FRACTION,
    )
    if max_cost <= FEE_PER_CONTRACT:
        return None

    center = round(spot)
    candidates: list[tuple[float, float, float, float]] = []
    for offset in range(-20, 21):
        strike = float(center + offset)
        if strike <= 0:
            continue
        if abs(strike - spot) / spot > MAX_MONEYNESS_FRACTION:
            continue
        price, delta = bs_price_delta(
            spot=spot,
            strike=strike,
            sigma=sigma,
            right=right,
        )
        if not (0.05 <= abs(delta) <= 0.80):
            continue
        width = max(0.01, price * spread_fraction)
        ask = price + width / 2.0
        cost = ask * 100.0 + FEE_PER_CONTRACT
        if cost > max_cost + 1e-9:
            continue
        candidates.append((abs(abs(delta) - target_delta), strike, ask, delta))

    if not candidates:
        return None
    _, strike, ask, delta = min(candidates, key=lambda item: (item[0], item[2]))
    return strike, ask, delta


@dataclass
class AccountResult:
    ending_equity: float
    profit: float
    growth: float
    trades: int
    wins: int
    max_drawdown: float
    halted: bool
    unaffordable_signals: int
    trade_returns: list[float]

    def as_dict(self) -> dict[str, float | int | bool | None]:
        losses = [-value for value in self.trade_returns if value < 0]
        wins = [value for value in self.trade_returns if value > 0]
        gross_profit = sum(wins)
        gross_loss = sum(losses)
        profit_factor = (
            gross_profit / gross_loss
            if gross_loss > 0
            else (float("inf") if gross_profit > 0 else 0.0)
        )
        geometric = (
            (self.ending_equity / STARTING_CASH) ** (1.0 / self.trades) - 1.0
            if self.trades and self.ending_equity > 0
            else 0.0
        )
        return {
            "starting_equity": STARTING_CASH,
            "ending_equity": self.ending_equity,
            "profit_dollars": self.profit,
            "growth_pct": self.growth * 100.0,
            "trades": self.trades,
            "trade_win_rate": self.wins / self.trades if self.trades else None,
            "profit_factor": profit_factor,
            "geometric_growth_per_trade": geometric,
            "max_drawdown_pct": self.max_drawdown * 100.0,
            "drawdown_halt_reached": self.halted,
            "unaffordable_signals": self.unaffordable_signals,
        }


def simulate_account(
    rows: list[ForecastRow],
    probability_field: str,
    *,
    iv_multiplier: float,
    spread_fraction: float,
    target_delta: float,
) -> AccountResult:
    settled = STARTING_CASH
    unsettled = 0.0
    high_water = STARTING_CASH
    peak = STARTING_CASH
    max_drawdown = 0.0
    trades = wins = unaffordable = 0
    halted = False
    trade_returns: list[float] = []

    for row in rows:
        # Equity-option sale proceeds are usable on the next trading day (T+1).
        settled += unsettled
        unsettled = 0.0
        equity = settled
        peak = max(peak, equity)
        drawdown = 0.0 if peak <= 0 else max(0.0, 1.0 - equity / peak)
        max_drawdown = max(max_drawdown, drawdown)
        high_water = max(high_water, equity)
        if drawdown >= DRAWDOWN_HALT:
            halted = True
            continue

        probability = float(getattr(row, probability_field))
        direction = signal_direction(probability)
        if direction == 0:
            continue

        selected = choose_contract(
            spot=row.open,
            vix=row.vix,
            right=direction,
            equity=equity,
            settled_cash=settled,
            iv_multiplier=iv_multiplier,
            spread_fraction=spread_fraction,
            target_delta=target_delta,
        )
        if selected is None:
            unaffordable += 1
            continue
        strike, ask, _delta = selected
        cost = ask * 100.0 + FEE_PER_CONTRACT
        if cost > settled + 1e-9:
            unaffordable += 1
            continue

        settled -= cost
        if direction > 0:
            intrinsic = max(0.0, row.close - strike)
        else:
            intrinsic = max(0.0, strike - row.close)
        proceeds = max(0.0, intrinsic * 100.0 - FEE_PER_CONTRACT)
        unsettled += proceeds
        pnl = proceeds - cost
        trade_return = pnl / cost
        trade_returns.append(trade_return)
        trades += 1
        wins += int(pnl > 0)

        close_equity = settled + unsettled
        peak = max(peak, close_equity)
        close_drawdown = 0.0 if peak <= 0 else max(0.0, 1.0 - close_equity / peak)
        max_drawdown = max(max_drawdown, close_drawdown)

    ending = settled + unsettled
    return AccountResult(
        ending_equity=ending,
        profit=ending - STARTING_CASH,
        growth=ending / STARTING_CASH - 1.0,
        trades=trades,
        wins=wins,
        max_drawdown=max_drawdown,
        halted=halted,
        unaffordable_signals=unaffordable,
        trade_returns=trade_returns,
    )


def evaluate_forecasts(rows: list[ForecastRow]) -> dict[str, object]:
    stats = {
        "quant_only": StrategyStats("quant_only"),
        "learned_proxy_only": StrategyStats("learned_proxy_only"),
        "fixed_55_45": StrategyStats("fixed_55_45"),
        "adaptive": StrategyStats("adaptive"),
    }
    for row in rows:
        outcome = 1.0 if row.close > row.open else 0.0 if row.close < row.open else 0.5
        stats["quant_only"].add(row.quant_p, outcome)
        stats["learned_proxy_only"].add(row.learned_p, outcome)
        stats["fixed_55_45"].add(row.fixed_p, outcome)
        stats["adaptive"].add(row.adaptive_p, outcome)
    return {name: value.as_dict() for name, value in stats.items()}


def main() -> None:
    days = load_spy()
    vix = load_vix()
    rows = build_forecasts(days, vix)
    if not rows:
        raise RuntimeError("locked holdout contains no rows")

    forecast_metrics = evaluate_forecasts(rows)
    account_metrics: dict[str, object] = {}
    for name, field in (
        ("quant_only", "quant_p"),
        ("fixed_55_45", "fixed_p"),
        ("adaptive", "adaptive_p"),
    ):
        account_metrics[name] = simulate_account(
            rows,
            field,
            iv_multiplier=BASE_IV_MULTIPLIER,
            spread_fraction=BASE_SPREAD_FRACTION,
            target_delta=BASE_TARGET_DELTA,
        ).as_dict()

    sensitivity: dict[str, object] = {}
    # Pre-declared stress grid.  These are reported side-by-side; we do not pick
    # the best result and call it the model.
    for iv_mult in (1.00, 1.25, 1.50):
        for spread in (0.05, 0.08, 0.12):
            key = f"ivx{iv_mult:.2f}_spread{spread:.2f}"
            sensitivity[key] = simulate_account(
                rows,
                "adaptive_p",
                iv_multiplier=iv_mult,
                spread_fraction=spread,
                target_delta=BASE_TARGET_DELTA,
            ).as_dict()

    weights = [row.adaptive_ai_weight for row in rows]
    result = {
        "method": "locked_holdout_daily_spy_synthetic_0dte_proxy",
        "holdout_start": rows[0].day.isoformat(),
        "holdout_end": rows[-1].day.isoformat(),
        "holdout_sessions": len(rows),
        "starting_cash": STARTING_CASH,
        "direction_threshold": DIRECTION_THRESHOLD,
        "adaptive_weight": {
            "mean_ai_proxy_weight": statistics.fmean(weights),
            "first_ai_proxy_weight": weights[0],
            "last_ai_proxy_weight": weights[-1],
            "quant_weight_is_always_majority": max(weights) < 0.5,
        },
        "forecast_metrics": forecast_metrics,
        "base_execution_assumptions": {
            "prior_day_vix_multiplier": BASE_IV_MULTIPLIER,
            "synthetic_spread_fraction": BASE_SPREAD_FRACTION,
            "target_abs_delta": BASE_TARGET_DELTA,
            "fee_per_contract_each_side": FEE_PER_CONTRACT,
            "max_premium_fraction": MAX_PREMIUM_FRACTION,
            "max_capital_fraction": MAX_CAPITAL_FRACTION,
            "drawdown_halt": DRAWDOWN_HALT,
            "max_moneyness_fraction": MAX_MONEYNESS_FRACTION,
            "position_size": "maximum one synthetic long option contract",
            "exit": "close/expiry intrinsic proxy; no intraday stop/target data available",
        },
        "account_metrics": account_metrics,
        "adaptive_sensitivity": sensitivity,
        "limitations": [
            "Daily SPY OHLC is used, not historical 1-second/1-minute underlying tape.",
            "Option premiums are Black-Scholes proxies based on prior-day VIX, not archived SPY 0DTE bid/ask quotes.",
            "The learned side is a point-in-time online logistic proxy, not a retroactive LLM backtest.",
            "No option breadth, intraday dynamic exit, news, or actual historical liquidity/open-interest data is available in this proxy.",
            "Profitability is a synthetic stress test and must be confirmed on real historical option quotes before being treated as evidence of tradable edge.",
        ],
    }

    print("RESULT_JSON=" + json.dumps(result, sort_keys=True, allow_nan=False))
    print("\nLOCKED HOLDOUT SUMMARY")
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
