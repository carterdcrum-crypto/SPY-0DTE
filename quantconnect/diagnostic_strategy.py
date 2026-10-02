from AlgorithmImports import *
from collections import deque
from datetime import timedelta
import math
import statistics

from adaptive_helpers import OnlineLearner, AdaptiveWeight
from spread_ledger import VirtualSpreadLedger


class SignalStats:
    def __init__(self, name, threshold):
        self.name = name
        self.threshold = float(threshold)
        self.brier_sum = 0.0
        self.observations = 0
        self.active = 0
        self.correct = 0

    def observe(self, probability, outcome):
        probability = max(0.0, min(1.0, float(probability)))
        outcome = max(0.0, min(1.0, float(outcome)))
        self.brier_sum += (probability - outcome) ** 2
        self.observations += 1
        direction = self.direction(probability)
        if direction == 0:
            return
        realized = 1 if outcome > 0.5 else -1 if outcome < 0.5 else 0
        self.active += 1
        self.correct += int(realized != 0 and realized == direction)

    def direction(self, probability):
        if probability >= self.threshold:
            return 1
        if probability <= 1.0 - self.threshold:
            return -1
        return 0

    def metrics(self):
        return {
            "brier": self.brier_sum / self.observations if self.observations else 0.0,
            "observations": self.observations,
            "active_signals": self.active,
            "directional_accuracy_pct": (
                100.0 * self.correct / self.active if self.active else 0.0
            ),
        }


class SpyOdteDiagnosticValidation(QCAlgorithm):
    """Locked-period diagnostic using real minute SPY 0DTE quotes.

    Three forecast streams are scored side by side:
      1) quant only
      2) sequential learned proxy only
      3) adaptive quant-majority blend

    Each stream also gets an independent virtual $100 debit-spread ledger.
    The ledgers use observed bid/ask quotes, conservative crossing assumptions,
    fixed fees, identical risk rails, and no parameter fitting to the test period.
    """

    def initialize(self):
        self.set_start_date(2025, 10, 1)
        self.set_end_date(2026, 9, 30)
        self.set_cash(100)
        self.set_brokerage_model(BrokerageName.WEBULL, AccountType.CASH)

        equity = self.add_equity(
            "SPY",
            Resolution.MINUTE,
            data_normalization_mode=DataNormalizationMode.RAW,
        )
        self.spy = equity.symbol
        option = self.add_option(self.spy, Resolution.MINUTE)
        option.set_filter(
            lambda universe: universe.include_weeklys().expiration(0, 0).strikes(-20, 20)
        )
        self.option_symbol = option.symbol

        self.closes = deque(maxlen=180)
        self.volumes = deque(maxlen=60)
        self.previous_option_mids = {}
        self.pending_forecasts = []

        self.learner = OnlineLearner()
        self.adaptive = AdaptiveWeight()
        self.direction_threshold = 0.55
        self.forecast_horizon_minutes = 5

        self.max_premium_fraction = 0.35
        self.max_capital_fraction = 0.90
        self.maximum_leg_spread_fraction = 0.10
        self.maximum_moneyness_fraction = 0.025
        self.target_abs_delta = 0.15
        self.stop_loss_fraction = 0.35
        self.take_profit_fraction = 0.50
        self.max_hold_minutes = 10
        self.cooldown_minutes = 3
        self.max_spread_width = 5.0
        self.fee_per_leg_each_side = 0.65

        self.stats = {
            "quant": SignalStats("quant", self.direction_threshold),
            "learned": SignalStats("learned", self.direction_threshold),
            "adaptive": SignalStats("adaptive", self.direction_threshold),
        }
        self.ledgers = {
            "quant": VirtualSpreadLedger(
                "quant", 100.0, self.fee_per_leg_each_side
            ),
            "learned": VirtualSpreadLedger(
                "learned", 100.0, self.fee_per_leg_each_side
            ),
            "adaptive": VirtualSpreadLedger(
                "adaptive", 100.0, self.fee_per_leg_each_side
            ),
        }
        self.last_ai_weight = 0.45
        self.first_ai_weight = None
        self.ai_weight_sum = 0.0
        self.ai_weight_count = 0

    @staticmethod
    def _clip(value, low, high):
        return max(low, min(high, float(value)))

    @staticmethod
    def _std(values, floor=1e-6):
        values = tuple(float(value) for value in values)
        return max(
            floor,
            statistics.pstdev(values) if len(values) >= 2 else floor,
        )

    @staticmethod
    def _normal_cdf(value):
        return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))

    def _probability_up(self, mean, sigma):
        return self._clip(
            self._normal_cdf(mean / max(float(sigma), 1e-9)),
            0.10,
            0.90,
        )

    def _recent_brier(self, returns, window):
        if len(returns) < window + 6:
            return 0.25
        scores = []
        for index in range(window, len(returns)):
            history = returns[index - window:index]
            mean = statistics.fmean(history)
            sigma = self._std(history)
            probability = self._probability_up(mean, sigma)
            outcome = 1.0 if returns[index] > 0.0 else 0.0
            scores.append((probability - outcome) ** 2)
        return statistics.fmean(scores[-30:]) if scores else 0.25

    def _calibration(self, brier):
        return self._clip(
            0.75 - 1.2 * (float(brier) - 0.18),
            0.35,
            0.92,
        )

    def _regime(self, returns):
        if len(returns) < 12:
            return 0.65
        fast = self._std(returns[-8:])
        slow = self._std(returns[-min(40, len(returns)):])
        ratio = fast / max(slow, 1e-9)
        return self._clip(
            math.exp(-0.65 * abs(math.log(max(ratio, 1e-6)))),
            0.35,
            1.0,
        )

    def _returns(self):
        prices = list(self.closes)
        return tuple(
            math.log(prices[index] / prices[index - 1])
            for index in range(1, len(prices))
            if prices[index] > 0.0 and prices[index - 1] > 0.0
        )

    def _quant_probability(self, chain):
        returns = self._returns()
        if len(returns) < 25:
            return 0.5
        regime = self._regime(returns)
        components = []

        for window in (6, 20):
            history = returns[-min(window, len(returns)):]
            expected = self._clip(
                statistics.fmean(history) * self.forecast_horizon_minutes,
                -0.008,
                0.008,
            )
            sigma = self._clip(
                self._std(history) * math.sqrt(self.forecast_horizon_minutes),
                0.00015,
                0.02,
            )
            brier = self._recent_brier(
                returns,
                max(3, min(window, 20)),
            )
            raw_probability = self._probability_up(expected, sigma)
            reliability = self._calibration(brier) * regime
            adjusted = 0.5 + (raw_probability - 0.5) * reliability
            components.append((adjusted, brier))

        logs = [
            math.log(max(1e-9, value))
            for value in list(self.closes)[-30:]
        ]
        center = statistics.fmean(logs)
        deviation = logs[-1] - center
        expected = self._clip(-0.65 * deviation, -0.006, 0.006)
        sigma = self._clip(
            self._std(
                returns[-min(30, len(returns)):]
            ) * math.sqrt(self.forecast_horizon_minutes),
            0.00015,
            0.02,
        )
        brier = max(
            0.20,
            self._recent_brier(tuple(-value for value in returns), 12),
        )
        raw_probability = self._probability_up(expected, sigma)
        reliability = (
            self._clip(
                self._calibration(brier) * 0.90,
                0.30,
                0.85,
            )
            * regime
        )
        components.append(
            (0.5 + (raw_probability - 0.5) * reliability, brier)
        )

        call_moves = []
        put_moves = []
        current_mids = {}
        if chain is not None:
            for contract in chain:
                bid = float(contract.bid_price)
                ask = float(contract.ask_price)
                if bid <= 0.0 or ask <= 0.0:
                    continue
                mid = (bid + ask) / 2.0
                current_mids[contract.symbol] = mid
                prior = self.previous_option_mids.get(contract.symbol)
                if prior is None or prior <= 0.0 or mid <= 0.0:
                    continue
                move = math.log(mid / prior)
                if contract.right == OptionRight.CALL:
                    call_moves.append(move)
                elif contract.right == OptionRight.PUT:
                    put_moves.append(move)

        if current_mids:
            self.previous_option_mids = current_mids

        if call_moves or put_moves:
            call_move = (
                statistics.median(call_moves)
                if call_moves
                else 0.0
            )
            put_move = (
                statistics.median(put_moves)
                if put_moves
                else 0.0
            )
            differential = self._clip(
                call_move - put_move,
                -0.25,
                0.25,
            )
            raw_probability = self._clip(
                0.5 + 1.5 * differential,
                0.20,
                0.80,
            )
            reliability = 0.62 * self._clip(
                regime * 0.90,
                0.35,
                0.90,
            )
            components.append(
                (
                    0.5 + (raw_probability - 0.5) * reliability,
                    0.24,
                )
            )

        raw_weights = [
            math.exp(-3.0 * max(0.0, loss))
            for _, loss in components
        ]
        total = sum(raw_weights)
        return self._clip(
            sum(
                weight * probability
                for weight, (probability, _) in zip(
                    raw_weights,
                    components,
                )
            )
            / max(total, 1e-12),
            0.0,
            1.0,
        )

    def _features(self):
        returns = self._returns()
        if len(returns) < 25:
            return (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        volatility = max(
            0.0002,
            self._std(returns[-20:]),
        )
        ret1 = returns[-1]
        ret5 = sum(returns[-5:])
        ret20 = sum(returns[-20:])
        fast_vol = self._std(returns[-5:])
        slow_vol = self._std(returns[-20:])
        volume_ratio = 1.0
        if len(self.volumes) >= 20:
            baseline = statistics.fmean(
                list(self.volumes)[-20:]
            )
            volume_ratio = (
                self.volumes[-1] / max(baseline, 1e-9)
            )
        minute_of_day = (
            self.time.hour * 60 + self.time.minute
        ) - (9 * 60 + 30)
        return (
            1.0,
            self._clip(ret1 / volatility, -4.0, 4.0),
            self._clip(
                ret5 / (volatility * math.sqrt(5.0)),
                -4.0,
                4.0,
            ),
            self._clip(
                ret20 / (volatility * math.sqrt(20.0)),
                -4.0,
                4.0,
            ),
            self._clip(
                math.log(
                    max(
                        fast_vol / max(slow_vol, 1e-9),
                        1e-6,
                    )
                ),
                -2.0,
                2.0,
            ),
            self._clip(
                math.log(max(volume_ratio, 1e-6)),
                -2.0,
                2.0,
            ),
            self._clip(
                (minute_of_day - 195.0) / 195.0,
                -1.0,
                1.0,
            ),
        )

    def _resolve_forecasts(self, spot):
        unresolved = []
        for forecast in self.pending_forecasts:
            if self.time < forecast["target"]:
                unresolved.append(forecast)
                continue
            if spot > forecast["spot"]:
                outcome = 1.0
            elif spot < forecast["spot"]:
                outcome = 0.0
            else:
                outcome = 0.5

            self.stats["quant"].observe(
                forecast["quant"],
                outcome,
            )
            self.stats["learned"].observe(
                forecast["learned"],
                outcome,
            )
            self.stats["adaptive"].observe(
                forecast["adaptive"],
                outcome,
            )

            self.learner.update(
                forecast["features"],
                outcome,
            )
            self.adaptive.observe(
                forecast["quant"],
                forecast["learned"],
                outcome,
            )

        self.pending_forecasts = unresolved

    def _quote_map(self, chain):
        if chain is None:
            return {}
        return {
            contract.symbol: contract
            for contract in chain
            if contract.expiry.date() == self.time.date()
        }

    def _leg_spread_fraction(self, contract):
        bid = float(contract.bid_price)
        ask = float(contract.ask_price)
        if bid <= 0.0 or ask <= 0.0:
            return None
        mid = (bid + ask) / 2.0
        if mid <= 0.0:
            return None
        return max(0.0, ask - bid) / mid

    def _eligible_contracts(self, chain, want_call):
        if chain is None:
            return []
        spot = float(self.securities[self.spy].price)
        result = []
        for contract in chain:
            if contract.expiry.date() != self.time.date():
                continue
            if want_call and contract.right != OptionRight.CALL:
                continue
            if not want_call and contract.right != OptionRight.PUT:
                continue
            spread = self._leg_spread_fraction(contract)
            if (
                spread is None
                or spread > self.maximum_leg_spread_fraction
            ):
                continue
            moneyness = abs(
                float(contract.strike) - spot
            ) / max(spot, 1e-9)
            if moneyness > self.maximum_moneyness_fraction:
                continue
            try:
                delta = abs(float(contract.greeks.delta))
            except Exception:
                continue
            if delta < 0.05 or delta > 0.80:
                continue
            result.append((contract, delta, spread))
        return result

    def _select_spread(self, chain, want_call, ledger):
        contracts = self._eligible_contracts(
            chain,
            want_call,
        )
        if not contracts:
            ledger.no_contract_signals += 1
            return None

        maximum_cost = ledger.max_entry_cost(
            self.max_premium_fraction,
            self.max_capital_fraction,
        )
        if maximum_cost <= 0.0:
            ledger.unaffordable_signals += 1
            return None

        candidates = []
        by_strike = sorted(
            contracts,
            key=lambda item: float(item[0].strike),
        )
        for long_contract, long_delta, long_spread in by_strike:
            long_strike = float(long_contract.strike)
            for short_contract, _, short_spread in by_strike:
                short_strike = float(short_contract.strike)
                if want_call:
                    width = short_strike - long_strike
                else:
                    width = long_strike - short_strike
                if width <= 0.0 or width > self.max_spread_width:
                    continue

                debit = (
                    float(long_contract.ask_price)
                    - float(short_contract.bid_price)
                ) * 100.0
                entry_cost = (
                    debit + 2.0 * self.fee_per_leg_each_side
                )
                if debit <= 0.0 or entry_cost > maximum_cost:
                    continue

                score = (
                    abs(
                        long_delta - self.target_abs_delta
                    ),
                    width,
                    long_spread + short_spread,
                    entry_cost,
                )
                candidates.append(
                    (
                        score,
                        long_contract,
                        short_contract,
                    )
                )

        if not candidates:
            ledger.unaffordable_signals += 1
            return None

        candidates.sort(key=lambda item: item[0])
        _, long_contract, short_contract = candidates[0]
        return long_contract, short_contract

    def _manage_virtual_positions(self, quotes):
        force_exit = (
            self.time.hour == 15
            and self.time.minute >= 50
        )
        for ledger in self.ledgers.values():
            ledger.mark_and_maybe_exit(
                quotes=quotes,
                now=self.time,
                stop_loss_fraction=self.stop_loss_fraction,
                take_profit_fraction=self.take_profit_fraction,
                max_hold_minutes=self.max_hold_minutes,
                force_exit=force_exit,
            )

    def _try_virtual_entry(
        self,
        name,
        probability,
        chain,
    ):
        ledger = self.ledgers[name]
        if not ledger.can_enter(
            self.time,
            self.cooldown_minutes,
        ):
            return

        direction = self.stats[name].direction(probability)
        if direction == 0:
            return

        pair = self._select_spread(
            chain,
            direction > 0,
            ledger,
        )
        if pair is None:
            return
        ledger.open(pair, self.time)

    def on_data(self, data: Slice):
        bar = data.bars.get(self.spy)
        if bar is None:
            return

        spot = float(bar.close)
        self.closes.append(spot)
        self.volumes.append(float(bar.volume))
        self._resolve_forecasts(spot)

        chain = data.option_chains.get(self.option_symbol)
        quotes = self._quote_map(chain)
        self._manage_virtual_positions(quotes)

        if len(self.closes) < 45:
            return
        if (
            self.time.hour < 9
            or (
                self.time.hour == 9
                and self.time.minute < 45
            )
            or self.time.hour >= 16
        ):
            return
        if (
            self.time.hour == 15
            and self.time.minute >= 40
        ):
            return
        if self.time.minute % 5 != 0:
            return

        quant_probability = self._quant_probability(chain)
        features = self._features()
        learned_probability = self.learner.probability(features)
        learned_weight = self.adaptive.learned_weight
        adaptive_probability = (
            (1.0 - learned_weight) * quant_probability
            + learned_weight * learned_probability
        )

        if self.first_ai_weight is None:
            self.first_ai_weight = learned_weight
        self.last_ai_weight = learned_weight
        self.ai_weight_sum += learned_weight
        self.ai_weight_count += 1

        self.pending_forecasts.append(
            {
                "target": self.time
                + timedelta(
                    minutes=self.forecast_horizon_minutes
                ),
                "spot": spot,
                "quant": quant_probability,
                "learned": learned_probability,
                "adaptive": adaptive_probability,
                "features": features,
            }
        )

        self._try_virtual_entry(
            "quant",
            quant_probability,
            chain,
        )
        self._try_virtual_entry(
            "learned",
            learned_probability,
            chain,
        )
        self._try_virtual_entry(
            "adaptive",
            adaptive_probability,
            chain,
        )

    def _log_metrics(self, label, metrics):
        self.log(label)
        for key in sorted(metrics.keys()):
            value = metrics[key]
            if isinstance(value, float):
                self.log(f"{key}={value:.6f}")
            else:
                self.log(f"{key}={value}")

    def on_end_of_algorithm(self):
        self.log("SPY_0DTE_DIAGNOSTIC_RESULT")
        self.log(
            "method=locked_period_real_minute_quotes_virtual_debit_spreads"
        )
        self.log("starting_cash_per_ledger=100.00")
        self.log(
            f"fee_per_leg_each_side={self.fee_per_leg_each_side:.2f}"
        )
        self.log(
            f"max_premium_fraction={self.max_premium_fraction:.2f}"
        )
        self.log(
            f"direction_threshold={self.direction_threshold:.2f}"
        )

        for name in ("quant", "learned", "adaptive"):
            self._log_metrics(
                f"{name.upper()}_FORECAST",
                self.stats[name].metrics(),
            )
            self._log_metrics(
                f"{name.upper()}_SPREAD_ACCOUNT",
                self.ledgers[name].metrics(),
            )

        mean_weight = (
            self.ai_weight_sum / self.ai_weight_count
            if self.ai_weight_count
            else self.last_ai_weight
        )
        self.log(
            f"first_learned_weight={float(self.first_ai_weight or self.last_ai_weight):.6f}"
        )
        self.log(
            f"mean_learned_weight={mean_weight:.6f}"
        )
        self.log(
            f"final_learned_weight={self.last_ai_weight:.6f}"
        )
        self.log(
            f"final_quant_weight={1.0 - self.last_ai_weight:.6f}"
        )
