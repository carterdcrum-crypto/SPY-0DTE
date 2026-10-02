from AlgorithmImports import *
from collections import deque
from datetime import timedelta
import math
import statistics

import v3_config as cfg
from spread_ledger import VirtualSpreadLedger
from v2_strategy import SignalStats, SpyOdteQuantFirstV2
from v3_experts import V3QuantExperts
from v3_models import EvidenceGatedCombiner, RegimeDetector, RegimeExpertCombiner, V3LearnedChallenger


class SpyOdteQuantFirstV3(SpyOdteQuantFirstV2):
    """Research-only V3: regime-aware quant core + evidence-gated learner.

    The inspected trailing year is development data. The learner is evaluated
    prequentially: predict first, resolve later, then update. A $10k research
    ledger measures signal economics without $100 affordability distortion,
    while a separate $100 adaptive ledger measures deployability.
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

        self.closes = deque(maxlen=240)
        self.volumes = deque(maxlen=120)
        self.previous_option_mids = {}
        self.pending_forecasts = []

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
        self.challenger = V3LearnedChallenger(feature_count=10)
        self.top_combiner = EvidenceGatedCombiner(
            learned_prior=cfg.LEARNED_PRIOR,
            learned_min=cfg.LEARNED_MIN,
            learned_max=cfg.LEARNED_MAX,
            warmup=cfg.LEARNED_WARMUP,
            max_step=cfg.LEARNED_MAX_STEP,
            window=cfg.EVIDENCE_WINDOW,
            z_threshold=cfg.EVIDENCE_Z,
        )

        self.stats = {
            "quant": SignalStats(cfg.DIRECTION_THRESHOLD),
            "learned": SignalStats(cfg.DIRECTION_THRESHOLD),
            "adaptive": SignalStats(cfg.DIRECTION_THRESHOLD),
        }
        self.ledgers = {
            "research_quant": VirtualSpreadLedger("research_quant", cfg.RESEARCH_CASH, cfg.FEE_PER_LEG_EACH_SIDE),
            "research_learned": VirtualSpreadLedger("research_learned", cfg.RESEARCH_CASH, cfg.FEE_PER_LEG_EACH_SIDE),
            "research_adaptive": VirtualSpreadLedger("research_adaptive", cfg.RESEARCH_CASH, cfg.FEE_PER_LEG_EACH_SIDE),
            "deploy_adaptive": VirtualSpreadLedger("deploy_adaptive", cfg.DEPLOY_CASH, cfg.FEE_PER_LEG_EACH_SIDE),
        }
        self.ledger_stream = {
            "research_quant": "quant",
            "research_learned": "learned",
            "research_adaptive": "adaptive",
            "deploy_adaptive": "adaptive",
        }
        self.pending_entries = {name: None for name in self.ledgers}
        self.entry_rejections = {
            name: {"risk": 0, "liquidity": 0, "reward_risk": 0, "no_chain": 0, "disagreement": 0}
            for name in self.ledgers
        }
        self.regime_counts = {name: 0 for name in RegimeDetector.names}
        self.monthly = {}

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

    def _chain_state(self, chain, spot):
        state = super()._chain_state(chain, spot)
        call_mids = []
        put_mids = []
        if chain is not None:
            for contract in chain:
                if contract.expiry.date() != self.time.date():
                    continue
                bid = float(contract.bid_price)
                ask = float(contract.ask_price)
                if bid <= 0.0 or ask <= 0.0 or ask < bid:
                    continue
                if abs(float(contract.strike) - spot) / max(spot, 1e-9) > 0.003:
                    continue
                mid = (bid + ask) / 2.0
                if contract.right == OptionRight.CALL:
                    call_mids.append(mid)
                elif contract.right == OptionRight.PUT:
                    put_mids.append(mid)
        call_mid = statistics.median(call_mids) if call_mids else 0.0
        put_mid = statistics.median(put_mids) if put_mids else 0.0
        state["atm_straddle_pct"] = (call_mid + put_mid) / max(spot, 1e-9)
        return state

    def _record_month(self, stream, probability, outcome, when):
        key = f"{when.year:04d}-{when.month:02d}:{stream}"
        bucket = self.monthly.setdefault(key, {"n": 0, "brier": 0.0, "active": 0, "correct": 0})
        p = max(0.0, min(1.0, float(probability)))
        y = float(outcome)
        bucket["n"] += 1
        bucket["brier"] += (p - y) ** 2
        direction = 1 if p >= cfg.DIRECTION_THRESHOLD else -1 if p <= 1.0 - cfg.DIRECTION_THRESHOLD else 0
        if direction:
            realized = 1 if y > 0.5 else -1 if y < 0.5 else 0
            bucket["active"] += 1
            bucket["correct"] += int(realized != 0 and direction == realized)

    def _resolve_forecasts(self, spot):
        unresolved = []
        for item in self.pending_forecasts:
            if self.time < item["target"]:
                unresolved.append(item)
                continue
            outcome = 1.0 if spot > item["spot"] else 0.0 if spot < item["spot"] else 0.5
            for stream in self.stats:
                self.stats[stream].observe(item[stream], outcome)
                self._record_month(stream, item[stream], outcome, item["target"])
            self.expert_combiner.observe(item["experts"], outcome, item["regime"])
            self.top_combiner.observe(item["quant"], item["learned"], outcome)
            self.challenger.update(item["features"], outcome)
        self.pending_forecasts = unresolved

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

    def _risk_caps(self, ledger_name):
        if ledger_name == "deploy_adaptive":
            return cfg.DEPLOY_PREFERRED_RISK_FRACTION, cfg.DEPLOY_HARD_RISK_FRACTION
        return cfg.RESEARCH_PREFERRED_RISK_FRACTION, cfg.RESEARCH_HARD_RISK_FRACTION

    def _select_vertical(self, chain, direction, balance, spot, preferred_fraction, hard_fraction):
        if chain is None:
            return None, "no_chain"
        want_call = direction > 0
        contracts = []
        had_candidate = False
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
            contracts.append(contract)

        if len(contracts) < 2:
            return None, "no_chain"
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
                had_candidate = True
                debit = max(0.0, (float(long_contract.ask_price) - float(short_contract.bid_price)) * 100.0)
                if debit <= 0.0 or debit > float(balance) * hard_fraction + 1e-9:
                    continue
                had_affordable = True
                max_profit = width * 100.0 - debit
                reward_risk = max_profit / max(debit, 1e-9)
                if reward_risk < cfg.MIN_REWARD_RISK:
                    continue
                had_reward = True
                preferred_penalty = 0 if debit <= float(balance) * preferred_fraction else 1
                distance = abs(long_strike - spot) / max(spot, 1e-9)
                total_spread = self._leg_spread_fraction(long_contract) + self._leg_spread_fraction(short_contract)
                candidates.append((preferred_penalty, distance, total_spread, debit, (long_contract, short_contract)))

        if candidates:
            candidates.sort(key=lambda row: row[:4])
            return candidates[0][4], None
        if not had_candidate:
            return None, "liquidity"
        if not had_affordable:
            return None, "risk"
        if not had_reward:
            return None, "reward_risk"
        return None, "liquidity"

    def _execute_pending_entries(self, chain, spot):
        for ledger_name, pending in tuple(self.pending_entries.items()):
            if pending is None or self.time <= pending["signal_time"]:
                continue
            ledger = self.ledgers[ledger_name]
            if not ledger.can_enter(self.time, cfg.COOLDOWN_MINUTES):
                self.pending_entries[ledger_name] = None
                continue
            preferred, hard = self._risk_caps(ledger_name)
            pair, reason = self._select_vertical(
                chain,
                pending["direction"],
                ledger.balance,
                spot,
                preferred,
                hard,
            )
            if pair is None:
                reason = reason or "no_chain"
                self.entry_rejections[ledger_name][reason] += 1
                if reason == "risk":
                    ledger.unaffordable_signals += 1
                else:
                    ledger.no_contract_signals += 1
                self.pending_entries[ledger_name] = None
                continue
            ledger.open(pair, self.time)
            self.pending_entries[ledger_name] = None

    def _queue_entry(self, ledger_name, probability, disagreement=0.0):
        if self.pending_entries[ledger_name] is not None:
            return
        ledger = self.ledgers[ledger_name]
        if not ledger.can_enter(self.time, cfg.COOLDOWN_MINUTES):
            return
        stream = self.ledger_stream[ledger_name]
        direction = self.stats[stream].direction(probability)
        if direction == 0:
            return
        if stream in ("quant", "adaptive") and disagreement > cfg.MAX_EXPERT_DISAGREEMENT:
            self.entry_rejections[ledger_name]["disagreement"] += 1
            return
        self.pending_entries[ledger_name] = {"direction": direction, "signal_time": self.time}

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
        regime = self.regime_detector.classify(returns, chain_state)
        self.regime_counts[regime] += 1
        expert_ps = self.experts.forecasts(self.closes, returns, chain_state)
        quant_p = self.expert_combiner.probability(expert_ps, regime)
        minute_of_day = (self.time.hour * 60 + self.time.minute) - 570
        features = self.experts.learner_features(self.closes, returns, self.volumes, chain_state, minute_of_day)
        learned_p = self.challenger.probability(features)
        adaptive_p = self.top_combiner.probability(quant_p, learned_p)
        disagreement = statistics.pstdev(tuple(expert_ps.values())) if len(expert_ps) > 1 else 0.0

        self.pending_forecasts.append({
            "target": self.time + timedelta(minutes=cfg.FORECAST_HORIZON_MINUTES),
            "spot": spot,
            "experts": dict(expert_ps),
            "regime": regime,
            "quant": quant_p,
            "learned": learned_p,
            "adaptive": adaptive_p,
            "features": tuple(features),
        })

        self._queue_entry("research_quant", quant_p, disagreement)
        self._queue_entry("research_learned", learned_p, 0.0)
        self._queue_entry("research_adaptive", adaptive_p, disagreement)
        self._queue_entry("deploy_adaptive", adaptive_p, disagreement)

    def _log_metrics(self, label, values):
        self.log(label)
        for key, value in values.items():
            if isinstance(value, float):
                self.log(f"{key}={value:.6f}")
            else:
                self.log(f"{key}={value}")

    def on_end_of_algorithm(self):
        self.log("SPY_0DTE_QUANT_FIRST_V3_RESULT")
        self.log("validation_mode=prequential_walk_forward_development_data")
        self.log(f"learned_weight_final={self.top_combiner.learned_weight:.6f}")
        self.log(f"quant_weight_final={self.top_combiner.quant_weight:.6f}")
        self.log(f"learned_vs_quant_evidence_z={self.top_combiner.last_z:.6f}")

        for stream in ("quant", "learned", "adaptive"):
            self._log_metrics(f"{stream.upper()}_FORECAST", self.stats[stream].metrics())

        for ledger_name in ("research_quant", "research_learned", "research_adaptive", "deploy_adaptive"):
            self._log_metrics(ledger_name.upper(), self.ledgers[ledger_name].metrics())
            for reason, count in self.entry_rejections[ledger_name].items():
                self.log(f"{ledger_name}_rejected_{reason}={count}")

        for regime, count in self.regime_counts.items():
            self.log(f"regime_{regime}_signals={count}")
            weights = self.expert_combiner.weights_for(regime)
            for expert, weight in weights.items():
                self.log(f"regime_{regime}_expert_{expert}_weight={weight:.6f}")

        for key in sorted(self.monthly):
            bucket = self.monthly[key]
            brier = bucket["brier"] / bucket["n"] if bucket["n"] else 0.0
            acc = 100.0 * bucket["correct"] / bucket["active"] if bucket["active"] else 0.0
            self.log(f"walkforward_{key}_n={bucket['n']}_brier={brier:.6f}_active={bucket['active']}_accuracy_pct={acc:.3f}")
