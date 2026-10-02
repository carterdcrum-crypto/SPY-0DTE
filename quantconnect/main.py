from AlgorithmImports import *
from collections import deque
from datetime import timedelta
import math
import statistics


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


class SpyOdteAdaptiveValidation(QCAlgorithm):
    """No-lookahead SPY 0DTE validation using QuantConnect minute option quotes.

    This is deliberately one frozen test, not a parameter optimizer.  The option
    chain and fills come from QuantConnect.  The unavailable historical LLM side
    is represented by a sequential online learner whose parameters update only
    after the future 5-minute outcome is known.
    """

    def initialize(self):
        # QuantConnect currently provides one trailing year of minute US equity-option history.
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
            lambda u: u.include_weeklys().expiration(0, 0).strikes(-20, 20)
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

        self.open_symbol = None
        self.entry_time = None
        self.entry_fill = None
        self.exit_pending = False
        self.last_entry_time = None

        # Frozen production-like micro-account rails.
        self.max_premium_fraction = 0.35
        self.max_capital_fraction = 0.90
        self.maximum_spread_fraction = 0.10
        self.maximum_moneyness_fraction = 0.025
        self.target_abs_delta = 0.15
        self.stop_loss_fraction = 0.35
        self.take_profit_fraction = 0.50
        self.max_hold_minutes = 10
        self.cooldown_minutes = 3

        self.signal_count = 0
        self.signal_correct = 0
        self.trade_count = 0
        self.trade_wins = 0
        self.gross_profit = 0.0
        self.gross_loss = 0.0
        self.high_water = 100.0
        self.max_drawdown = 0.0
        self.starting_cash = 100.0
        self.last_ai_weight = 0.45

    @staticmethod
    def _clip(value, low, high):
        return max(low, min(high, float(value)))

    @staticmethod
    def _std(values, floor=1e-6):
        values = tuple(float(x) for x in values)
        return max(floor, statistics.pstdev(values) if len(values) >= 2 else floor)

    @staticmethod
    def _normal_cdf(value):
        return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))

    def _probability_up(self, mean, sigma):
        return self._clip(self._normal_cdf(mean / max(sigma, 1e-9)), 0.10, 0.90)

    def _recent_brier(self, returns, window):
        if len(returns) < window + 6:
            return 0.25
        scores = []
        for i in range(window, len(returns)):
            history = returns[i - window:i]
            mean = statistics.fmean(history)
            sigma = self._std(history)
            p = self._probability_up(mean, sigma)
            outcome = 1.0 if returns[i] > 0.0 else 0.0
            scores.append((p - outcome) ** 2)
        return statistics.fmean(scores[-30:]) if scores else 0.25

    def _calibration(self, brier):
        return self._clip(0.75 - 1.2 * (brier - 0.18), 0.35, 0.92)

    def _regime(self, returns):
        if len(returns) < 12:
            return 0.65
        fast = self._std(returns[-8:])
        slow = self._std(returns[-min(40, len(returns)):])
        ratio = fast / max(slow, 1e-9)
        return self._clip(math.exp(-0.65 * abs(math.log(max(ratio, 1e-6)))), 0.35, 1.0)

    def _returns(self):
        prices = list(self.closes)
        return tuple(
            math.log(prices[i] / prices[i - 1])
            for i in range(1, len(prices))
            if prices[i] > 0 and prices[i - 1] > 0
        )

    def _quant_probability(self, chain):
        returns = self._returns()
        if len(returns) < 25:
            return 0.5
        regime = self._regime(returns)
        components = []

        for name, window in (("fast", 6), ("trend", 20)):
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
            brier = self._recent_brier(returns, max(3, min(window, 20)))
            raw_p = self._probability_up(expected, sigma)
            reliability = self._calibration(brier) * regime
            adjusted = 0.5 + (raw_p - 0.5) * reliability
            components.append((adjusted, brier))

        logs = [math.log(max(1e-9, x)) for x in list(self.closes)[-30:]]
        center = statistics.fmean(logs)
        deviation = logs[-1] - center
        expected = self._clip(-0.65 * deviation, -0.006, 0.006)
        sigma = self._clip(
            self._std(returns[-min(30, len(returns)):]) * math.sqrt(self.forecast_horizon_minutes),
            0.00015,
            0.02,
        )
        brier = max(0.20, self._recent_brier(tuple(-x for x in returns), 12))
        raw_p = self._probability_up(expected, sigma)
        reliability = self._clip(self._calibration(brier) * 0.90, 0.30, 0.85) * regime
        components.append((0.5 + (raw_p - 0.5) * reliability, brier))

        # Real option-chain breadth; this part was unavailable in the daily proxy test.
        call_moves = []
        put_moves = []
        current_mids = {}
        if chain is not None:
            for contract in chain:
                bid = float(contract.bid_price)
                ask = float(contract.ask_price)
                if bid <= 0 or ask <= 0:
                    continue
                mid = (bid + ask) / 2.0
                current_mids[contract.symbol] = mid
                prior = self.previous_option_mids.get(contract.symbol)
                if prior is None or prior <= 0 or mid <= 0:
                    continue
                move = math.log(mid / prior)
                if contract.right == OptionRight.CALL:
                    call_moves.append(move)
                elif contract.right == OptionRight.PUT:
                    put_moves.append(move)
        if current_mids:
            self.previous_option_mids = current_mids
        if call_moves or put_moves:
            call_move = statistics.median(call_moves) if call_moves else 0.0
            put_move = statistics.median(put_moves) if put_moves else 0.0
            differential = self._clip(call_move - put_move, -0.25, 0.25)
            raw_p = self._clip(0.5 + 1.5 * differential, 0.20, 0.80)
            reliability = 0.62 * self._clip(regime * 0.90, 0.35, 0.90)
            components.append((0.5 + (raw_p - 0.5) * reliability, 0.24))

        raw_weights = [math.exp(-3.0 * max(0.0, loss)) for _, loss in components]
        total = sum(raw_weights)
        return self._clip(
            sum(w * p for w, (p, _) in zip(raw_weights, components)) / max(total, 1e-12),
            0.0,
            1.0,
        )

    def _features(self):
        returns = self._returns()
        if len(returns) < 25:
            return (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        vol = max(0.0002, self._std(returns[-20:]))
        ret1 = returns[-1]
        ret5 = sum(returns[-5:])
        ret20 = sum(returns[-20:])
        fast_vol = self._std(returns[-5:])
        slow_vol = self._std(returns[-20:])
        volume_ratio = 1.0
        if len(self.volumes) >= 20:
            baseline = statistics.fmean(list(self.volumes)[-20:])
            volume_ratio = self.volumes[-1] / max(baseline, 1e-9)
        minute_of_day = (self.time.hour * 60 + self.time.minute) - (9 * 60 + 30)
        return (
            1.0,
            self._clip(ret1 / vol, -4.0, 4.0),
            self._clip(ret5 / (vol * math.sqrt(5.0)), -4.0, 4.0),
            self._clip(ret20 / (vol * math.sqrt(20.0)), -4.0, 4.0),
            self._clip(math.log(max(fast_vol / max(slow_vol, 1e-9), 1e-6)), -2.0, 2.0),
            self._clip(math.log(max(volume_ratio, 1e-6)), -2.0, 2.0),
            self._clip((minute_of_day - 195.0) / 195.0, -1.0, 1.0),
        )

    def _resolve_forecasts(self, spot):
        unresolved = []
        for item in self.pending_forecasts:
            if self.time < item["target"]:
                unresolved.append(item)
                continue
            if spot > item["spot"]:
                outcome = 1.0
            elif spot < item["spot"]:
                outcome = 0.0
            else:
                outcome = 0.5
            direction = 1 if item["blend"] >= self.direction_threshold else -1 if item["blend"] <= 1.0 - self.direction_threshold else 0
            realized = 1 if outcome > 0.5 else -1 if outcome < 0.5 else 0
            if direction != 0:
                self.signal_count += 1
                self.signal_correct += int(realized != 0 and direction == realized)
            self.learner.update(item["features"], outcome)
            self.adaptive.observe(item["quant"], item["learned"], outcome)
        self.pending_forecasts = unresolved

    def _select_contract(self, chain, want_call):
        if chain is None:
            return None
        equity = float(self.portfolio.total_portfolio_value)
        cash = float(self.portfolio.cash)
        max_cost = min(cash * self.max_capital_fraction, equity * self.max_premium_fraction)
        candidates = []
        for contract in chain:
            if contract.expiry.date() != self.time.date():
                continue
            if want_call and contract.right != OptionRight.CALL:
                continue
            if not want_call and contract.right != OptionRight.PUT:
                continue
            bid = float(contract.bid_price)
            ask = float(contract.ask_price)
            if bid <= 0 or ask <= 0:
                continue
            cost = ask * 100.0
            if cost > max_cost + 1e-9:
                continue
            mid = (bid + ask) / 2.0
            spread = (ask - bid) / max(mid, 1e-9)
            if spread > self.maximum_spread_fraction:
                continue
            spot = float(self.securities[self.spy].price)
            moneyness = abs(float(contract.strike) - spot) / max(spot, 1e-9)
            if moneyness > self.maximum_moneyness_fraction:
                continue
            try:
                delta = abs(float(contract.greeks.delta))
            except Exception:
                delta = 1.0
            if delta < 0.05 or delta > 0.80:
                continue
            candidates.append((abs(delta - self.target_abs_delta), spread, cost, contract))
        if not candidates:
            return None
        candidates.sort(key=lambda x: (x[0], x[1], x[2]))
        return candidates[0][3]

    def _manage_position(self):
        if self.open_symbol is None or self.entry_fill is None or self.entry_time is None or self.exit_pending:
            return
        security = self.securities[self.open_symbol]
        bid = float(security.bid_price)
        if bid <= 0:
            return
        change = bid / self.entry_fill - 1.0
        held = self.time - self.entry_time
        should_exit = (
            change <= -self.stop_loss_fraction
            or change >= self.take_profit_fraction
            or held >= timedelta(minutes=self.max_hold_minutes)
            or (self.time.hour == 15 and self.time.minute >= 50)
        )
        if should_exit:
            self.exit_pending = True
            self.market_order(self.open_symbol, -1, tag="adaptive-exit")

    def on_data(self, data: Slice):
        bar = data.bars.get(self.spy)
        if bar is None:
            return
        spot = float(bar.close)
        self.closes.append(spot)
        self.volumes.append(float(bar.volume))
        self._resolve_forecasts(spot)

        equity = float(self.portfolio.total_portfolio_value)
        self.high_water = max(self.high_water, equity)
        if self.high_water > 0:
            self.max_drawdown = max(self.max_drawdown, 1.0 - equity / self.high_water)

        self._manage_position()
        if len(self.closes) < 45:
            return
        if self.time.hour < 9 or (self.time.hour == 9 and self.time.minute < 45) or self.time.hour >= 16:
            return
        if self.time.hour == 15 and self.time.minute >= 40:
            return
        if self.time.minute % 5 != 0:
            return

        chain = data.option_chains.get(self.option_symbol)
        quant_p = self._quant_probability(chain)
        features = self._features()
        learned_p = self.learner.probability(features)
        learned_weight = self.adaptive.learned_weight
        self.last_ai_weight = learned_weight
        blend = (1.0 - learned_weight) * quant_p + learned_weight * learned_p
        self.pending_forecasts.append(
            {
                "target": self.time + timedelta(minutes=self.forecast_horizon_minutes),
                "spot": spot,
                "quant": quant_p,
                "learned": learned_p,
                "blend": blend,
                "features": features,
            }
        )

        if self.open_symbol is not None or self.exit_pending:
            return
        if self.last_entry_time is not None and self.time - self.last_entry_time < timedelta(minutes=self.cooldown_minutes):
            return
        if blend >= self.direction_threshold:
            want_call = True
        elif blend <= 1.0 - self.direction_threshold:
            want_call = False
        else:
            return

        contract = self._select_contract(chain, want_call)
        if contract is None:
            return
        self.last_entry_time = self.time
        self.market_order(contract.symbol, 1, tag=f"adaptive-entry p={blend:.3f} q={quant_p:.3f} l={learned_p:.3f} w={learned_weight:.3f}")

    def on_order_event(self, order_event: OrderEvent):
        if order_event.status != OrderStatus.FILLED:
            return
        symbol = order_event.symbol
        quantity = float(order_event.fill_quantity)
        price = float(order_event.fill_price)
        if quantity > 0:
            self.open_symbol = symbol
            self.entry_time = self.time
            self.entry_fill = price
            self.exit_pending = False
            return
        if quantity < 0 and self.open_symbol == symbol and self.entry_fill is not None:
            pnl = (price - self.entry_fill) * 100.0
            self.trade_count += 1
            if pnl > 0:
                self.trade_wins += 1
                self.gross_profit += pnl
            elif pnl < 0:
                self.gross_loss += -pnl
            self.open_symbol = None
            self.entry_time = None
            self.entry_fill = None
            self.exit_pending = False

    def on_end_of_algorithm(self):
        ending = float(self.portfolio.total_portfolio_value)
        profit = ending - self.starting_cash
        growth = ending / self.starting_cash - 1.0
        accuracy = self.signal_correct / self.signal_count if self.signal_count else 0.0
        win_rate = self.trade_wins / self.trade_count if self.trade_count else 0.0
        profit_factor = self.gross_profit / self.gross_loss if self.gross_loss > 0 else (float("inf") if self.gross_profit > 0 else 0.0)
        self.log("SPY_0DTE_REAL_OPTION_RESULT")
        self.log(f"starting_cash={self.starting_cash:.2f}")
        self.log(f"ending_equity={ending:.2f}")
        self.log(f"profit_dollars={profit:.2f}")
        self.log(f"growth_pct={growth * 100.0:.2f}")
        self.log(f"directional_accuracy_pct={accuracy * 100.0:.2f}")
        self.log(f"resolved_active_signals={self.signal_count}")
        self.log(f"completed_trades={self.trade_count}")
        self.log(f"trade_win_rate_pct={win_rate * 100.0:.2f}")
        self.log(f"profit_factor={profit_factor}")
        self.log(f"max_drawdown_pct={self.max_drawdown * 100.0:.2f}")
        self.log(f"final_learned_weight={self.last_ai_weight:.4f}")
        self.log(f"final_quant_weight={1.0 - self.last_ai_weight:.4f}")
