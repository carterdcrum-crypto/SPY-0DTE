from AlgorithmImports import *
from collections import deque
from datetime import timedelta
import math
import statistics

import v2_config as cfg
from spread_ledger import VirtualSpreadLedger
from v2_adaptive import (
    FixedShareExpertCombiner,
    OnlineLogisticChallenger,
    QuantLedCombiner,
)
from v2_experts import QuantExperts, clip


class SignalStats:
    def __init__(self, threshold):
        self.threshold = float(threshold)
        self.brier_sum = 0.0
        self.observations = 0
        self.active = 0
        self.correct = 0

    def direction(self, probability):
        p = float(probability)
        if p >= self.threshold:
            return 1
        if p <= 1.0 - self.threshold:
            return -1
        return 0

    def observe(self, probability, outcome):
        p = clip(probability, 0.0, 1.0)
        y = clip(outcome, 0.0, 1.0)
        self.brier_sum += (p - y) ** 2
        self.observations += 1
        direction = self.direction(p)
        if direction == 0:
            return
        realized = 1 if y > 0.5 else -1 if y < 0.5 else 0
        self.active += 1
        self.correct += int(realized != 0 and direction == realized)

    def metrics(self):
        return {
            "brier": self.brier_sum / self.observations if self.observations else 0.0,
            "observations": self.observations,
            "active_signals": self.active,
            "directional_accuracy_pct": 100.0 * self.correct / self.active if self.active else 0.0,
        }


class SpyOdteQuantFirstV2(QCAlgorithm):
    """Research-only v2: quant experts + bounded online challenger + real NBBO spreads.

    Important safeguards:
    - historical LLM replay is not used;
    - scoring forecasts are non-overlapping 5-minute anchors;
    - online updates happen only after outcomes resolve;
    - entry is delayed until a later minute;
    - long legs pay ask and short legs receive bid through VirtualSpreadLedger;
    - $100 account risk is capped and NO TRADE is valid.
    """

    def initialize(self):
        self.set_start_date(*cfg.BACKTEST_START)
        self.set_end_date(*cfg.BACKTEST_END)
        self.set_cash(cfg.STARTING_CASH)
        self.set_brokerage_model(BrokerageName.WEBULL, AccountType.CASH)

        equity = self.add_equity(
            "SPY",
            Resolution.MINUTE,
            data_normalization_mode=DataNormalizationMode.RAW,
        )
        self.spy = equity.symbol
        option = self.add_option(self.spy, Resolution.MINUTE)
        option.set_filter(
            lambda u: u.include_weeklys().expiration(0, 0).strikes(-25, 25)
        )
        self.option_symbol = option.symbol

        self.closes = deque(maxlen=240)
        self.volumes = deque(maxlen=120)
        self.previous_option_mids = {}
        self.pending_forecasts = []

        self.experts = QuantExperts(cfg.FORECAST_HORIZON_MINUTES)
        self.expert_combiner = FixedShareExpertCombiner(
            self.experts.names,
            eta=cfg.EXPERT_ETA,
            share=cfg.EXPERT_SHARE,
            min_weight=cfg.EXPERT_MIN_WEIGHT,
            max_weight=cfg.EXPERT_MAX_WEIGHT,
        )
        self.challenger = OnlineLogisticChallenger(feature_count=7)
        self.top_combiner = QuantLedCombiner(
            quant_prior=cfg.QUANT_PRIOR,
            learned_prior=cfg.LEARNED_PRIOR,
            learned_min=cfg.LEARNED_MIN,
            learned_max=cfg.LEARNED_MAX,
            warmup=cfg.LEARNED_WARMUP,
            max_step=cfg.LEARNED_MAX_STEP,
            eta=cfg.TOP_LEVEL_ETA,
            share=cfg.TOP_LEVEL_SHARE,
        )

        self.stats = {
            "quant": SignalStats(cfg.DIRECTION_THRESHOLD),
            "learned": SignalStats(cfg.DIRECTION_THRESHOLD),
            "adaptive": SignalStats(cfg.DIRECTION_THRESHOLD),
        }
        self.ledgers = {
            name: VirtualSpreadLedger(name, cfg.STARTING_CASH, cfg.FEE_PER_LEG_EACH_SIDE)
            for name in self.stats
        }
        self.pending_entries = {name: None for name in self.stats}
        self.entry_rejections = {
            name: {"risk": 0, "liquidity": 0, "reward_risk": 0, "no_chain": 0}
            for name in self.stats
        }

    def _returns(self):
        prices = list(self.closes)
        return tuple(
            math.log(prices[i] / prices[i - 1])
            for i in range(1, len(prices))
            if prices[i] > 0.0 and prices[i - 1] > 0.0
        )

    def _is_signal_time(self):
        hour = self.time.hour
        minute = self.time.minute
        if hour < cfg.FIRST_SIGNAL_HOUR:
            return False
        if hour == cfg.FIRST_SIGNAL_HOUR and minute < cfg.FIRST_SIGNAL_MINUTE:
            return False
        if hour > cfg.LAST_SIGNAL_HOUR:
            return False
        if hour == cfg.LAST_SIGNAL_HOUR and minute > cfg.LAST_SIGNAL_MINUTE:
            return False
        return minute % cfg.SCORING_INTERVAL_MINUTES == 0

    def _force_flat(self):
        return (
            self.time.hour > cfg.FORCE_FLAT_HOUR
            or (
                self.time.hour == cfg.FORCE_FLAT_HOUR
                and self.time.minute >= cfg.FORCE_FLAT_MINUTE
            )
        )

    def _chain_state(self, chain, spot):
        call_moves = []
        put_moves = []
        imbalances = []
        near_calls = []
        near_puts = []
        current_mids = {}

        if chain is None:
            return {"option_pressure": 0.0, "quote_imbalance": 0.0, "skew_signal": 0.0}

        for contract in chain:
            if contract.expiry.date() != self.time.date():
                continue
            bid = float(contract.bid_price)
            ask = float(contract.ask_price)
            if bid <= 0.0 or ask <= 0.0 or ask < bid:
                continue
            mid = (bid + ask) / 2.0
            current_mids[contract.symbol] = mid
            prior = self.previous_option_mids.get(contract.symbol)
            if prior is not None and prior > 0.0 and mid > 0.0:
                move = math.log(mid / prior)
                if contract.right == OptionRight.CALL:
                    call_moves.append(move)
                elif contract.right == OptionRight.PUT:
                    put_moves.append(move)

            try:
                bid_size = float(contract.bid_size)
                ask_size = float(contract.ask_size)
                total_size = bid_size + ask_size
                if total_size > 0:
                    imbalances.append((bid_size - ask_size) / total_size)
            except Exception:
                pass

            distance = abs(float(contract.strike) - float(spot)) / max(float(spot), 1e-9)
            if distance <= 0.005:
                if contract.right == OptionRight.CALL:
                    near_calls.append(mid)
                elif contract.right == OptionRight.PUT:
                    near_puts.append(mid)

        if current_mids:
            self.previous_option_mids = current_mids

        call_move = statistics.median(call_moves) if call_moves else 0.0
        put_move = statistics.median(put_moves) if put_moves else 0.0
        option_pressure = clip((call_move - put_move) * 8.0, -1.0, 1.0)
        quote_imbalance = clip(statistics.median(imbalances) if imbalances else 0.0, -1.0, 1.0)

        call_mid = statistics.median(near_calls) if near_calls else 0.0
        put_mid = statistics.median(near_puts) if near_puts else 0.0
        denom = call_mid + put_mid
        skew_signal = clip((call_mid - put_mid) / denom if denom > 0 else 0.0, -1.0, 1.0)
        return {
            "option_pressure": option_pressure,
            "quote_imbalance": quote_imbalance,
            "skew_signal": skew_signal,
        }

    def _resolve_forecasts(self, spot):
        unresolved = []
        for item in self.pending_forecasts:
            if self.time < item["target"]:
                unresolved.append(item)
                continue
            outcome = 1.0 if spot > item["spot"] else 0.0 if spot < item["spot"] else 0.5
            self.stats["quant"].observe(item["quant"], outcome)
            self.stats["learned"].observe(item["learned"], outcome)
            self.stats["adaptive"].observe(item["adaptive"], outcome)

            self.expert_combiner.observe(item["experts"], outcome)
            self.top_combiner.observe(item["quant"], item["learned"], outcome)
            self.challenger.update(item["features"], outcome)
        self.pending_forecasts = unresolved

    @staticmethod
    def _quote_map(chain):
        return {contract.symbol: contract for contract in chain} if chain is not None else {}

    def _manage_ledgers(self, chain):
        quotes = self._quote_map(chain)
        force = self._force_flat()
        for ledger in self.ledgers.values():
            ledger.mark_and_maybe_exit(
                quotes,
                self.time,
                cfg.STOP_LOSS_FRACTION,
                cfg.TAKE_PROFIT_FRACTION,
                cfg.MAX_HOLD_MINUTES,
                force_exit=force,
            )

    def _leg_spread_fraction(self, contract):
        bid = float(contract.bid_price)
        ask = float(contract.ask_price)
        mid = (bid + ask) / 2.0
        if bid <= 0.0 or ask <= 0.0 or ask < bid or mid <= 0.0:
            return None
        return (ask - bid) / mid

    def _select_vertical(self, chain, direction, balance, spot):
        if chain is None:
            return None, "no_chain"
        want_call = direction > 0
        contracts = []
        had_liquid = False
        had_affordable = False
        had_reward = False

        for contract in chain:
            if contract.expiry.date() != self.time.date():
                continue
            if want_call and contract.right != OptionRight.CALL:
                continue
            if not want_call and contract.right != OptionRight.PUT:
                continue
            rel_spread = self._leg_spread_fraction(contract)
            if rel_spread is None or rel_spread > cfg.MAX_LEG_RELATIVE_SPREAD:
                continue
            moneyness = abs(float(contract.strike) - spot) / max(spot, 1e-9)
            if moneyness > cfg.MAX_MONEYNESS_FRACTION:
                continue
            had_liquid = True
            contracts.append(contract)

        if len(contracts) < 2:
            return None, "liquidity" if had_liquid else "no_chain"

        contracts.sort(key=lambda c: float(c.strike))
        candidates = []
        for long_contract in contracts:
            long_strike = float(long_contract.strike)
            for short_contract in contracts:
                short_strike = float(short_contract.strike)
                if want_call and short_strike <= long_strike:
                    continue
                if not want_call and short_strike >= long_strike:
                    continue
                width = abs(short_strike - long_strike)
                if width <= 0.0 or width > cfg.MAX_SPREAD_WIDTH:
                    continue
                debit = max(
                    0.0,
                    (float(long_contract.ask_price) - float(short_contract.bid_price)) * 100.0,
                )
                if debit <= 0.0:
                    continue
                hard_cap = float(balance) * cfg.HARD_RISK_FRACTION
                if debit > hard_cap + 1e-9:
                    continue
                had_affordable = True
                max_profit = width * 100.0 - debit
                reward_risk = max_profit / max(debit, 1e-9)
                if reward_risk < cfg.MIN_REWARD_RISK:
                    continue
                had_reward = True
                preferred_cap = float(balance) * cfg.PREFERRED_RISK_FRACTION
                preferred_penalty = 0 if debit <= preferred_cap else 1
                long_distance = abs(long_strike - spot) / max(spot, 1e-9)
                total_spread = (
                    self._leg_spread_fraction(long_contract)
                    + self._leg_spread_fraction(short_contract)
                )
                candidates.append(
                    (
                        preferred_penalty,
                        long_distance,
                        total_spread,
                        debit,
                        (long_contract, short_contract),
                    )
                )

        if candidates:
            candidates.sort(key=lambda row: row[:4])
            return candidates[0][4], None
        if not had_affordable:
            return None, "risk"
        if not had_reward:
            return None, "reward_risk"
        return None, "liquidity"

    def _execute_pending_entries(self, chain, spot):
        for name, pending in tuple(self.pending_entries.items()):
            if pending is None or self.time <= pending["signal_time"]:
                continue
            ledger = self.ledgers[name]
            if not ledger.can_enter(self.time, cfg.COOLDOWN_MINUTES):
                self.pending_entries[name] = None
                continue
            pair, reason = self._select_vertical(chain, pending["direction"], ledger.balance, spot)
            if pair is None:
                self.entry_rejections[name][reason or "no_chain"] += 1
                if reason == "risk":
                    ledger.unaffordable_signals += 1
                else:
                    ledger.no_contract_signals += 1
                self.pending_entries[name] = None
                continue
            ledger.open(pair, self.time)
            self.pending_entries[name] = None

    def _queue_entry(self, name, probability):
        if self.pending_entries[name] is not None:
            return
        ledger = self.ledgers[name]
        if not ledger.can_enter(self.time, cfg.COOLDOWN_MINUTES):
            return
        direction = self.stats[name].direction(probability)
        if direction == 0:
            return
        self.pending_entries[name] = {
            "direction": direction,
            "signal_time": self.time,
        }

    def on_data(self, data: Slice):
        bar = data.bars.get(self.spy)
        if bar is None:
            return
        spot = float(bar.close)
        self.closes.append(spot)
        self.volumes.append(float(bar.volume))
        returns = self._returns()
        chain = data.option_chains.get(self.option_symbol)

        self._resolve_forecasts(spot)
        self._manage_ledgers(chain)
        self._execute_pending_entries(chain, spot)

        if len(returns) < 30 or not self._is_signal_time() or self._force_flat():
            return

        chain_state = self._chain_state(chain, spot)
        expert_ps = self.experts.forecasts(self.closes, returns, chain_state)
        quant_p = self.expert_combiner.probability(expert_ps)
        minute_of_day = (self.time.hour * 60 + self.time.minute) - 570
        features = self.experts.learner_features(
            self.closes,
            returns,
            self.volumes,
            chain_state,
            minute_of_day,
        )
        learned_p = self.challenger.probability(features)
        adaptive_p = self.top_combiner.probability(quant_p, learned_p)

        self.pending_forecasts.append(
            {
                "target": self.time + timedelta(minutes=cfg.FORECAST_HORIZON_MINUTES),
                "spot": spot,
                "experts": dict(expert_ps),
                "quant": quant_p,
                "learned": learned_p,
                "adaptive": adaptive_p,
                "features": tuple(features),
            }
        )

        self._queue_entry("quant", quant_p)
        self._queue_entry("learned", learned_p)
        self._queue_entry("adaptive", adaptive_p)

    def _log_metrics(self, label, values):
        self.log(label)
        for key, value in values.items():
            if isinstance(value, float):
                self.log(f"{key}={value:.6f}")
            else:
                self.log(f"{key}={value}")

    def on_end_of_algorithm(self):
        self.log("SPY_0DTE_QUANT_FIRST_V2_RESULT")
        self.log("historical_llm_weight=0.000000")
        self.log("note=learned stream is a forward-only statistical challenger, not retrospective LLM output")
        self.log(f"quant_weight_final={self.top_combiner.quant_weight:.6f}")
        self.log(f"learned_weight_final={self.top_combiner.learned_weight:.6f}")
        self.log(f"top_level_resolved_samples={self.top_combiner.samples}")
        for name in self.experts.names:
            self.log(f"expert_weight_{name}={self.expert_combiner.weights[name]:.6f}")

        for name in ("quant", "learned", "adaptive"):
            self._log_metrics(f"{name.upper()}_FORECAST", self.stats[name].metrics())
            self._log_metrics(f"{name.upper()}_SPREAD_ACCOUNT", self.ledgers[name].metrics())
            for reason, count in self.entry_rejections[name].items():
                self.log(f"{name}_rejected_{reason}={count}")
