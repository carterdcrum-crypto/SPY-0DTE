from AlgorithmImports import *
from collections import deque
from datetime import timedelta
import math
import statistics

import v4_config as cfg
from spread_ledger import VirtualSpreadLedger
from v2_strategy import SignalStats
from v3_experts import V3QuantExperts
from v3_models import EvidenceGatedCombiner, RegimeDetector, RegimeExpertCombiner
from v4_models import (
    PayoffEstimator,
    SpreadOutcomeLearner,
    SpreadStats,
    quant_candidate_estimate,
    spread_features,
)


class SpyOdteSpreadEvV4(QCAlgorithm):
    """Research-only V4: rank actual 0DTE spreads by expected value.

    Direction is only one input. The learned layer predicts whether a specific
    executable spread will finish positive under the same stop/take/time rules.
    It is updated only after that candidate outcome resolves. Quant keeps
    majority control through an evidence-gated blend.
    """

    def initialize(self):
        self.set_start_date(*cfg.BACKTEST_START)
        self.set_end_date(*cfg.BACKTEST_END)
        self.set_cash(cfg.RESEARCH_CASH)
        self.set_brokerage_model(BrokerageName.WEBULL, AccountType.CASH)

        equity = self.add_equity(
            "SPY",
            Resolution.MINUTE,
            data_normalization_mode=DataNormalizationMode.RAW,
        )
        self.spy = equity.symbol
        option = self.add_option(self.spy, Resolution.MINUTE)
        option.set_filter(lambda u: u.include_weeklys().expiration(0, 0).strikes(-25, 25))
        self.option_symbol = option.symbol

        self.closes = deque(maxlen=300)
        self.volumes = deque(maxlen=180)
        self.previous_option_mids = {}
        self.pending_direction_forecasts = []
        self.pending_candidate_samples = []
        self.resolved_candidate_samples = 0
        self.dropped_candidate_samples = 0

        self.experts = V3QuantExperts(cfg.FORECAST_HORIZON_MINUTES)
        self.regime_detector = RegimeDetector()
        self.expert_combiner = RegimeExpertCombiner(
            self.experts.names,
            eta=cfg.EXPERT_ETA,
            share=cfg.EXPERT_SHARE,
            min_weight=cfg.EXPERT_MIN_WEIGHT,
            max_weight=cfg.EXPERT_MAX_WEIGHT,
            regime_blend=cfg.REGIME_BLEND,
        )
        self.spread_learner = SpreadOutcomeLearner(feature_count=13)
        self.spread_combiner = EvidenceGatedCombiner(
            learned_prior=cfg.LEARNED_PRIOR,
            learned_min=cfg.LEARNED_MIN,
            learned_max=cfg.LEARNED_MAX,
            warmup=cfg.LEARNED_WARMUP,
            max_step=cfg.LEARNED_MAX_STEP,
            window=cfg.EVIDENCE_WINDOW,
            z_threshold=cfg.EVIDENCE_Z,
        )
        self.payoff = PayoffEstimator(
            cfg.PAYOFF_PRIOR_WIN_RETURN,
            cfg.PAYOFF_PRIOR_LOSS_RETURN,
            cfg.PAYOFF_EWMA_ALPHA,
        )

        self.direction_stats = SignalStats(cfg.DIRECTION_DIAGNOSTIC_THRESHOLD)
        self.spread_stats = {
            "quant": SpreadStats(),
            "learned": SpreadStats(),
            "adaptive": SpreadStats(),
        }
        self.monthly = {}
        self.regime_counts = {name: 0 for name in RegimeDetector.names}

        self.ledgers = {
            "research_quant": VirtualSpreadLedger("research_quant", cfg.RESEARCH_CASH, cfg.FEE_PER_LEG_EACH_SIDE),
            "research_learned": VirtualSpreadLedger("research_learned", cfg.RESEARCH_CASH, cfg.FEE_PER_LEG_EACH_SIDE),
            "research_adaptive": VirtualSpreadLedger("research_adaptive", cfg.RESEARCH_CASH, cfg.FEE_PER_LEG_EACH_SIDE),
            "deploy_adaptive": VirtualSpreadLedger("deploy_adaptive", cfg.DEPLOY_CASH, cfg.FEE_PER_LEG_EACH_SIDE),
        }
        self.pending_entries = {name: None for name in self.ledgers}
        self.entry_rejections = {
            name: {
                "risk": 0,
                "liquidity": 0,
                "no_positive_ev": 0,
                "disagreement": 0,
                "stale_quote": 0,
                "open_failed": 0,
            }
            for name in self.ledgers
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
            or (self.time.hour == cfg.FORCE_FLAT_HOUR and self.time.minute >= cfg.FORCE_FLAT_MINUTE)
        )

    @staticmethod
    def _quote_map(chain):
        return {contract.symbol: contract for contract in chain} if chain is not None else {}

    @staticmethod
    def _leg_spread_fraction(contract):
        bid = float(contract.bid_price)
        ask = float(contract.ask_price)
        mid = (bid + ask) / 2.0
        if bid <= 0.0 or ask <= 0.0 or ask < bid or mid <= 0.0:
            return None
        return (ask - bid) / mid

    def _chain_state(self, chain, spot):
        call_moves = []
        put_moves = []
        imbalances = []
        near_calls = []
        near_puts = []
        current_mids = {}

        if chain is not None:
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
                    if total_size > 0.0:
                        imbalances.append((bid_size - ask_size) / total_size)
                except Exception:
                    pass

                distance = abs(float(contract.strike) - float(spot)) / max(float(spot), 1e-9)
                if distance <= 0.003:
                    if contract.right == OptionRight.CALL:
                        near_calls.append(mid)
                    elif contract.right == OptionRight.PUT:
                        near_puts.append(mid)

        if current_mids:
            self.previous_option_mids = current_mids

        call_move = statistics.median(call_moves) if call_moves else 0.0
        put_move = statistics.median(put_moves) if put_moves else 0.0
        option_pressure = max(-1.0, min(1.0, (call_move - put_move) * 8.0))
        quote_imbalance = max(-1.0, min(1.0, statistics.median(imbalances) if imbalances else 0.0))
        call_mid = statistics.median(near_calls) if near_calls else 0.0
        put_mid = statistics.median(near_puts) if near_puts else 0.0
        denom = call_mid + put_mid
        skew_signal = max(-1.0, min(1.0, (call_mid - put_mid) / denom if denom > 0.0 else 0.0))
        return {
            "option_pressure": option_pressure,
            "quote_imbalance": quote_imbalance,
            "skew_signal": skew_signal,
            "atm_straddle_pct": denom / max(float(spot), 1e-9),
        }

    def _risk_caps(self, ledger_name):
        if ledger_name == "deploy_adaptive":
            return cfg.DEPLOY_PREFERRED_RISK_FRACTION, cfg.DEPLOY_HARD_RISK_FRACTION
        return cfg.RESEARCH_PREFERRED_RISK_FRACTION, cfg.RESEARCH_HARD_RISK_FRACTION

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

    def _resolve_direction_forecasts(self, spot):
        unresolved = []
        for item in self.pending_direction_forecasts:
            if self.time < item["target"]:
                unresolved.append(item)
                continue
            outcome = 1.0 if spot > item["spot"] else 0.0 if spot < item["spot"] else 0.5
            self.direction_stats.observe(item["quant_probability"], outcome)
            self.expert_combiner.observe(item["experts"], outcome, item["regime"])
        self.pending_direction_forecasts = unresolved

    def _record_spread_month(self, stream, probability, outcome, when):
        key = f"{when.year:04d}-{when.month:02d}:{stream}"
        bucket = self.monthly.setdefault(key, {"n": 0, "brier": 0.0, "correct": 0})
        p = max(0.0, min(1.0, float(probability)))
        y = 1.0 if float(outcome) > 0.5 else 0.0
        bucket["n"] += 1
        bucket["brier"] += (p - y) ** 2
        bucket["correct"] += int((p >= 0.5) == (y >= 0.5))

    def _resolve_candidate_samples(self, chain):
        quotes = self._quote_map(chain)
        unresolved = []
        for item in self.pending_candidate_samples:
            held = self.time - item["entry_time"]
            long_quote = quotes.get(item["long_symbol"])
            short_quote = quotes.get(item["short_symbol"])
            if long_quote is None or short_quote is None:
                if held < timedelta(minutes=cfg.MAX_PENDING_SAMPLE_MINUTES):
                    unresolved.append(item)
                else:
                    self.dropped_candidate_samples += 1
                continue

            long_bid = float(long_quote.bid_price)
            short_ask = float(short_quote.ask_price)
            if long_bid < 0.0 or short_ask < 0.0:
                unresolved.append(item)
                continue
            credit = max(0.0, (long_bid - short_ask) * 100.0)
            pnl = credit - float(item["entry_debit"])
            base = max(float(item["entry_debit"]), 1e-9)
            trade_return = pnl / base
            should_resolve = (
                trade_return <= -cfg.STOP_LOSS_FRACTION
                or trade_return >= cfg.TAKE_PROFIT_FRACTION
                or held >= timedelta(minutes=cfg.MAX_HOLD_MINUTES)
                or self._force_flat()
            )
            if not should_resolve:
                unresolved.append(item)
                continue

            outcome = 1.0 if pnl > 0.0 else 0.0
            for stream in ("quant", "learned", "adaptive"):
                probability = item[f"{stream}_probability"]
                self.spread_stats[stream].observe(probability, outcome)
                self._record_spread_month(stream, probability, outcome, self.time)
            self.spread_combiner.observe(
                item["quant_probability"],
                item["learned_probability"],
                outcome,
            )
            self.spread_learner.update(item["features"], outcome)
            self.payoff.update(trade_return)
            self.resolved_candidate_samples += 1
        self.pending_candidate_samples = unresolved

    def _candidate_pool(self, chain, spot):
        if chain is None:
            return []
        by_right = {OptionRight.CALL: [], OptionRight.PUT: []}
        for contract in chain:
            if contract.expiry.date() != self.time.date():
                continue
            if contract.right not in by_right:
                continue
            rel_spread = self._leg_spread_fraction(contract)
            if rel_spread is None or rel_spread > cfg.MAX_LEG_RELATIVE_SPREAD:
                continue
            distance = abs(float(contract.strike) - float(spot)) / max(float(spot), 1e-9)
            if distance > cfg.MAX_MONEYNESS_FRACTION:
                continue
            by_right[contract.right].append(contract)

        candidates = []
        for right, contracts in by_right.items():
            contracts.sort(key=lambda c: float(c.strike))
            for long_contract in contracts:
                long_strike = float(long_contract.strike)
                for short_contract in contracts:
                    short_strike = float(short_contract.strike)
                    if right == OptionRight.CALL and short_strike <= long_strike:
                        continue
                    if right == OptionRight.PUT and short_strike >= long_strike:
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
                    width_dollars = width * 100.0
                    max_profit = width_dollars - debit
                    if max_profit <= 0.0:
                        continue
                    reward_risk = max_profit / debit
                    if reward_risk < cfg.MIN_REWARD_RISK:
                        continue
                    long_rel = self._leg_spread_fraction(long_contract)
                    short_rel = self._leg_spread_fraction(short_contract)
                    total_rel = (long_rel or 0.0) + (short_rel or 0.0)
                    candidates.append({
                        "direction": 1 if right == OptionRight.CALL else -1,
                        "long_symbol": long_contract.symbol,
                        "short_symbol": short_contract.symbol,
                        "long_strike": long_strike,
                        "short_strike": short_strike,
                        "debit": debit,
                        "width_dollars": width_dollars,
                        "max_profit": max_profit,
                        "reward_risk": reward_risk,
                        "debit_fraction": debit / width_dollars,
                        "distance_fraction": abs(long_strike - float(spot)) / max(float(spot), 1e-9),
                        "total_relative_spread": total_rel,
                    })
        return candidates

    def _score_candidates(self, candidates, spot, quant_direction_probability, returns, chain_state, regime):
        if not candidates:
            return []
        rv20 = statistics.pstdev(tuple(returns[-20:])) if len(returns) >= 20 else 0.0001
        sigma_horizon = max(0.0001, rv20) * math.sqrt(cfg.FORECAST_HORIZON_MINUTES)
        implied_move = max(0.0, float(chain_state.get("atm_straddle_pct", 0.0)))
        iv_rv_ratio = implied_move / max(sigma_horizon, 1e-9) if implied_move > 0.0 else 1.0
        minute_of_day = (self.time.hour * 60 + self.time.minute) - 570

        scored = []
        for candidate in candidates:
            quant = quant_candidate_estimate(
                candidate,
                spot,
                quant_direction_probability,
                sigma_horizon,
            )
            features = spread_features(
                candidate,
                quant["win_probability"],
                chain_state,
                iv_rv_ratio,
                minute_of_day,
                regime,
            )
            learned_p = self.spread_learner.probability(features)
            adaptive_p = self.spread_combiner.probability(quant["win_probability"], learned_p)
            learned_ev = self.payoff.expected_return(learned_p) * float(candidate["debit"])
            adaptive_ev = (
                self.spread_combiner.quant_weight * float(quant["expected_value_dollars"])
                + self.spread_combiner.learned_weight * learned_ev
            )
            row = dict(candidate)
            row.update({
                "quant_probability": quant["win_probability"],
                "learned_probability": learned_p,
                "adaptive_probability": adaptive_p,
                "quant_ev": float(quant["expected_value_dollars"]),
                "learned_ev": float(learned_ev),
                "adaptive_ev": float(adaptive_ev),
                "features": tuple(features),
            })
            scored.append(row)
        return scored

    def _enqueue_training_samples(self, scored):
        for direction in (1, -1):
            side = [row for row in scored if row["direction"] == direction]
            side.sort(key=lambda row: (
                row["total_relative_spread"],
                row["distance_fraction"],
                abs(row["debit_fraction"] - 0.5),
            ))
            for row in side[:cfg.TRAIN_CANDIDATES_PER_SIDE]:
                self.pending_candidate_samples.append({
                    "entry_time": self.time,
                    "long_symbol": row["long_symbol"],
                    "short_symbol": row["short_symbol"],
                    "entry_debit": row["debit"],
                    "features": row["features"],
                    "quant_probability": row["quant_probability"],
                    "learned_probability": row["learned_probability"],
                    "adaptive_probability": row["adaptive_probability"],
                })

    def _queue_best(self, ledger_name, scored, disagreement):
        if self.pending_entries[ledger_name] is not None:
            return
        ledger = self.ledgers[ledger_name]
        if not ledger.can_enter(self.time, cfg.COOLDOWN_MINUTES):
            return

        if ledger_name in ("research_quant", "research_adaptive", "deploy_adaptive"):
            if disagreement > cfg.MAX_EXPERT_DISAGREEMENT:
                self.entry_rejections[ledger_name]["disagreement"] += 1
                return

        preferred, hard = self._risk_caps(ledger_name)
        score_key = (
            "quant_ev" if ledger_name == "research_quant"
            else "learned_ev" if ledger_name == "research_learned"
            else "adaptive_ev"
        )
        positive = [row for row in scored if row[score_key] > cfg.MIN_EXPECTED_VALUE_DOLLARS]
        if not positive:
            self.entry_rejections[ledger_name]["no_positive_ev"] += 1
            return

        affordable = [row for row in positive if row["debit"] <= ledger.balance * hard + 1e-9]
        if not affordable:
            self.entry_rejections[ledger_name]["risk"] += 1
            ledger.unaffordable_signals += 1
            return

        affordable.sort(key=lambda row: (
            0 if row["debit"] <= ledger.balance * preferred else 1,
            -row[score_key],
            row["total_relative_spread"],
            row["distance_fraction"],
        ))
        best = affordable[0]
        self.pending_entries[ledger_name] = {
            "signal_time": self.time,
            "long_symbol": best["long_symbol"],
            "short_symbol": best["short_symbol"],
            "expected_value": best[score_key],
        }

    def _execute_pending_entries(self, chain):
        quotes = self._quote_map(chain)
        for ledger_name, pending in tuple(self.pending_entries.items()):
            if pending is None or self.time <= pending["signal_time"]:
                continue
            ledger = self.ledgers[ledger_name]
            if not ledger.can_enter(self.time, cfg.COOLDOWN_MINUTES):
                self.pending_entries[ledger_name] = None
                continue
            long_quote = quotes.get(pending["long_symbol"])
            short_quote = quotes.get(pending["short_symbol"])
            if long_quote is None or short_quote is None:
                self.entry_rejections[ledger_name]["stale_quote"] += 1
                self.pending_entries[ledger_name] = None
                continue
            debit = max(0.0, (float(long_quote.ask_price) - float(short_quote.bid_price)) * 100.0)
            _, hard = self._risk_caps(ledger_name)
            if debit <= 0.0 or debit > ledger.balance * hard + 1e-9:
                self.entry_rejections[ledger_name]["risk"] += 1
                ledger.unaffordable_signals += 1
                self.pending_entries[ledger_name] = None
                continue
            if not ledger.open((long_quote, short_quote), self.time):
                self.entry_rejections[ledger_name]["open_failed"] += 1
            self.pending_entries[ledger_name] = None

    def on_data(self, data: Slice):
        bar = data.bars.get(self.spy)
        if bar is None:
            return
        spot = float(bar.close)
        self.closes.append(spot)
        self.volumes.append(float(bar.volume))
        returns = self._returns()
        chain = data.option_chains.get(self.option_symbol)

        self._resolve_direction_forecasts(spot)
        self._resolve_candidate_samples(chain)
        self._manage_ledgers(chain)
        self._execute_pending_entries(chain)

        if len(returns) < 30 or not self._is_signal_time() or self._force_flat():
            return

        chain_state = self._chain_state(chain, spot)
        regime = self.regime_detector.classify(returns, chain_state)
        self.regime_counts[regime] += 1
        expert_ps = self.experts.forecasts(self.closes, returns, chain_state)
        quant_direction_p = self.expert_combiner.probability(expert_ps, regime)
        disagreement = statistics.pstdev(tuple(expert_ps.values())) if len(expert_ps) > 1 else 0.0

        self.pending_direction_forecasts.append({
            "target": self.time + timedelta(minutes=cfg.FORECAST_HORIZON_MINUTES),
            "spot": spot,
            "experts": dict(expert_ps),
            "regime": regime,
            "quant_probability": quant_direction_p,
        })

        candidates = self._candidate_pool(chain, spot)
        if not candidates:
            for name in self.entry_rejections:
                self.entry_rejections[name]["liquidity"] += 1
            return
        scored = self._score_candidates(
            candidates,
            spot,
            quant_direction_p,
            returns,
            chain_state,
            regime,
        )
        self._enqueue_training_samples(scored)
        for ledger_name in self.ledgers:
            self._queue_best(ledger_name, scored, disagreement)

    def _log_metrics(self, label, values):
        self.log(label)
        for key, value in values.items():
            if isinstance(value, float):
                self.log(f"{key}={value:.6f}")
            else:
                self.log(f"{key}={value}")

    def on_end_of_algorithm(self):
        self.log("SPY_0DTE_SPREAD_EV_V4_RESULT")
        self.log("validation_mode=prequential_spread_outcome_development_data")
        self.log(f"learned_weight_final={self.spread_combiner.learned_weight:.6f}")
        self.log(f"quant_weight_final={self.spread_combiner.quant_weight:.6f}")
        self.log(f"learned_vs_quant_evidence_z={self.spread_combiner.last_z:.6f}")
        self.log(f"payoff_avg_win_return={self.payoff.avg_win:.6f}")
        self.log(f"payoff_avg_loss_return={self.payoff.avg_loss:.6f}")
        self.log(f"payoff_wins={self.payoff.wins}")
        self.log(f"payoff_losses={self.payoff.losses}")
        self.log(f"resolved_candidate_samples={self.resolved_candidate_samples}")
        self.log(f"dropped_candidate_samples={self.dropped_candidate_samples}")

        self._log_metrics("QUANT_DIRECTION_DIAGNOSTIC", self.direction_stats.metrics())
        for stream in ("quant", "learned", "adaptive"):
            self._log_metrics(f"{stream.upper()}_SPREAD_OUTCOME", self.spread_stats[stream].metrics())

        for ledger_name in ("research_quant", "research_learned", "research_adaptive", "deploy_adaptive"):
            self._log_metrics(ledger_name.upper(), self.ledgers[ledger_name].metrics())
            for reason, count in self.entry_rejections[ledger_name].items():
                self.log(f"{ledger_name}_rejected_{reason}={count}")

        for regime, count in self.regime_counts.items():
            self.log(f"regime_{regime}_signals={count}")
            weights = self.expert_combiner.weights_for(regime)
            for expert, weight in weights.items():
                self.log(f"regime_{regime}_expert_weight_{expert}={weight:.6f}")

        for key in sorted(self.monthly):
            bucket = self.monthly[key]
            n = bucket["n"]
            if n <= 0:
                continue
            self.log(
                f"month_{key}_n={n},"
                f"brier={bucket['brier'] / n:.6f},"
                f"accuracy_pct={100.0 * bucket['correct'] / n:.6f}"
            )
