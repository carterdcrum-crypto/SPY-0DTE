from __future__ import annotations

import logging
import math
import os
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from statistics import NormalDist
from typing import Callable, Iterable

from .dynamic_risk import DynamicRiskRequest, choose_dynamic_long_option_size
from .ensemble import EnsembleForecast, ModelForecast, combine_forecasts
from .greeks import model_greeks_from_quote
from .market import MarketSnapshot, OptionQuote
from .opportunity import OpportunityResult, evaluate_option_candidate, rank_opportunities
from .paper_account import PaperAccountStore
from .paper_autotrader import (
    EASTERN,
    MarketCycle,
    PaperAutoSettings,
    PaperSignal,
    QuoteRow,
    SnapshotReader,
    _paper_db_path,
    _paper_starting_cash,
    _publish,
)
from .paper_dynamic_autotrader import (
    DynamicPaperAutoTrader,
    _account_risk_state,
    _clip,
    _env_float,
    risk_config_from_env,
)
from .scenario import generate_return_scenarios

log = logging.getLogger("spy0dte.paper.ensemble")
SIGNAL_STRATEGY = "market_ensemble_v1"
RISK_PROFILE = "dynamic_posterior_v2"
POSITION_STRATEGY = f"{SIGNAL_STRATEGY}+{RISK_PROFILE}"


def _feed_delay_limit() -> float:
    return _env_float("PAPER_MAX_FEED_DELAY_SECONDS", 3.0, minimum=0.25, maximum=60.0)


def _horizon_minutes() -> float:
    return _env_float("PAPER_ENSEMBLE_HORIZON_MINUTES", 5.0, minimum=0.5, maximum=30.0)


def _minimum_lower_edge() -> float:
    return _env_float("PAPER_MIN_LOWER_CONFIDENCE_EDGE", 0.005, minimum=0.0, maximum=1.0)


def _direction_threshold() -> float:
    return _env_float("PAPER_ENSEMBLE_DIRECTION_THRESHOLD", 0.55, minimum=0.50, maximum=0.80)


def _safe_log_return(new: float, old: float) -> float:
    if new <= 0.0 or old <= 0.0:
        return 0.0
    return math.log(new / old)


def _std(values: Iterable[float], floor: float = 1e-6) -> float:
    items = tuple(float(value) for value in values)
    if len(items) < 2:
        return floor
    return max(floor, statistics.pstdev(items))


def _normal_probability_up(mean: float, sigma: float) -> float:
    sigma = max(1e-9, sigma)
    z = mean / sigma
    return _clip(NormalDist().cdf(z), 0.10, 0.90)


def _recent_brier(returns: tuple[float, ...], window: int) -> float:
    """Walk-forward one-step Brier loss used only as an online reliability prior."""
    if len(returns) < window + 6:
        return 0.25
    scores: list[float] = []
    for index in range(window, len(returns)):
        history = returns[index - window : index]
        mean = statistics.fmean(history)
        sigma = _std(history)
        p_up = _normal_probability_up(mean, sigma)
        outcome = 1.0 if returns[index] > 0.0 else 0.0
        scores.append((p_up - outcome) ** 2)
    return statistics.fmean(scores[-30:]) if scores else 0.25


def _calibration_from_brier(brier: float) -> float:
    # Random 50/50 prediction has Brier 0.25. Keep a non-zero prior because the
    # live-paper sample is intentionally small, but penalize deterioration fast.
    return _clip(0.75 - 1.2 * (brier - 0.18), 0.35, 0.92)


def _regime_match(returns: tuple[float, ...]) -> float:
    if len(returns) < 12:
        return 0.65
    fast = _std(returns[-8:])
    slow = _std(returns[-min(40, len(returns)) :])
    ratio = fast / max(slow, 1e-9)
    return _clip(math.exp(-0.65 * abs(math.log(max(ratio, 1e-6)))), 0.35, 1.0)


def _momentum_forecast(
    *,
    name: str,
    returns: tuple[float, ...],
    window: int,
    horizon_steps: float,
    regime_match: float,
) -> ModelForecast:
    history = returns[-min(window, len(returns)) :]
    mean_step = statistics.fmean(history) if history else 0.0
    sigma_step = _std(history)
    expected = _clip(mean_step * horizon_steps, -0.008, 0.008)
    volatility = _clip(sigma_step * math.sqrt(max(1.0, horizon_steps)), 0.00015, 0.02)
    brier = _recent_brier(returns, max(3, min(window, 20)))
    return ModelForecast(
        name=name,
        probability_up=_normal_probability_up(expected, volatility),
        expected_log_return=expected,
        volatility=volatility,
        validation_loss=brier,
        calibration_score=_calibration_from_brier(brier),
        regime_match_score=regime_match,
    )


def _mean_reversion_forecast(
    *,
    spots: tuple[float, ...],
    returns: tuple[float, ...],
    horizon_steps: float,
    regime_match: float,
) -> ModelForecast:
    sample = spots[-min(30, len(spots)) :]
    log_spots = tuple(math.log(max(1e-9, value)) for value in sample)
    center = statistics.fmean(log_spots)
    deviation = log_spots[-1] - center
    expected = _clip(-0.65 * deviation, -0.006, 0.006)
    sigma_step = _std(returns[-min(30, len(returns)) :])
    volatility = _clip(sigma_step * math.sqrt(max(1.0, horizon_steps)), 0.00015, 0.02)
    # Mean reversion is deliberately assigned a more conservative online prior
    # until enough paper observations exist to calibrate it by regime.
    brier = max(0.20, _recent_brier(tuple(-value for value in returns), 12))
    return ModelForecast(
        name="short_mean_reversion",
        probability_up=_normal_probability_up(expected, volatility),
        expected_log_return=expected,
        volatility=volatility,
        validation_loss=brier,
        calibration_score=_clip(_calibration_from_brier(brier) * 0.90, 0.30, 0.85),
        regime_match_score=regime_match,
    )


def _option_breadth_forecast(
    latest: MarketCycle,
    previous: MarketCycle,
    *,
    baseline_volatility: float,
    regime_match: float,
) -> ModelForecast:
    previous_by_symbol = {row.symbol: row for row in previous.rows}
    call_moves: list[float] = []
    put_moves: list[float] = []
    for row in latest.rows:
        prior = previous_by_symbol.get(row.symbol)
        if prior is None or prior.mid <= 0.0 or row.mid <= 0.0:
            continue
        move = math.log(row.mid / prior.mid)
        if row.right == "call":
            call_moves.append(move)
        elif row.right == "put":
            put_moves.append(move)
    call_move = statistics.median(call_moves) if call_moves else 0.0
    put_move = statistics.median(put_moves) if put_moves else 0.0
    differential = _clip(call_move - put_move, -0.25, 0.25)
    expected = _clip(0.012 * differential, -0.003, 0.003)
    probability_up = _clip(0.5 + 1.5 * differential, 0.20, 0.80)
    return ModelForecast(
        name="option_breadth",
        probability_up=probability_up,
        expected_log_return=expected,
        volatility=max(0.00015, baseline_volatility),
        validation_loss=0.24,
        calibration_score=0.62,
        regime_match_score=_clip(regime_match * 0.90, 0.35, 0.90),
    )


def build_market_ensemble(cycles: tuple[MarketCycle, ...], horizon_minutes: float) -> EnsembleForecast:
    if len(cycles) < 8:
        raise ValueError("market ensemble requires at least eight completed cycles")
    chronological = tuple(reversed(cycles))
    spots = tuple(cycle.spot for cycle in chronological if cycle.spot > 0.0)
    if len(spots) < 8:
        raise ValueError("market ensemble requires valid SPY spot history")
    returns = tuple(_safe_log_return(spots[index], spots[index - 1]) for index in range(1, len(spots)))

    intervals = [
        max(0.05, (chronological[index].received_at - chronological[index - 1].received_at).total_seconds())
        for index in range(1, len(chronological))
    ]
    step_seconds = statistics.median(intervals) if intervals else 2.0
    horizon_steps = max(1.0, horizon_minutes * 60.0 / max(0.25, step_seconds))
    regime = _regime_match(returns)

    fast = _momentum_forecast(
        name="fast_momentum",
        returns=returns,
        window=min(6, len(returns)),
        horizon_steps=horizon_steps,
        regime_match=regime,
    )
    trend = _momentum_forecast(
        name="intraday_trend",
        returns=returns,
        window=min(20, len(returns)),
        horizon_steps=horizon_steps,
        regime_match=regime,
    )
    reversion = _mean_reversion_forecast(
        spots=spots,
        returns=returns,
        horizon_steps=horizon_steps,
        regime_match=regime,
    )
    breadth = _option_breadth_forecast(
        chronological[-1],
        chronological[-2],
        baseline_volatility=trend.volatility,
        regime_match=regime,
    )
    return combine_forecasts((fast, trend, reversion, breadth))


def _positive_return_probability(result: OpportunityResult) -> float:
    probabilities = result.candidate.scenario_probabilities
    total = sum(probabilities)
    if total <= 0.0:
        return 0.0
    return sum(
        probability
        for value, probability in zip(result.candidate.scenario_returns, probabilities)
        if value > 0.0
    ) / total


def _premium_move(row: QuoteRow, previous: MarketCycle) -> float:
    previous_by_symbol = {item.symbol: item for item in previous.rows}
    prior = previous_by_symbol.get(row.symbol)
    if prior is None or prior.mid <= 0.0 or row.mid <= 0.0:
        return 0.0
    return row.mid / prior.mid - 1.0


class EnsemblePaperAutoTrader(DynamicPaperAutoTrader):
    """Execution-aware probabilistic paper trader.

    The entry brain combines several zero-cost market models, generates a joint
    SPY/IV scenario distribution, reprices every candidate option after modeled
    spread/slippage, and sends that net P&L distribution to the independent
    dynamic risk engine. There is no broker-order path in this class.
    """

    def _fresh_feed_rows(self, latest: MarketCycle) -> tuple[QuoteRow, ...]:
        limit = _feed_delay_limit()
        return tuple(
            row
            for row in latest.rows
            if row.feed_delay_seconds is not None and 0.0 <= row.feed_delay_seconds <= limit
        )

    def _manage_position(
        self,
        position: dict[str, object],
        now: datetime,
        mode: str,
    ) -> dict[str, object]:
        symbol = str(position["symbol"])
        quote = self.market_reader.latest_quote(symbol)
        if quote is None:
            return super()._manage_position(position, now, mode)
        receive_age = max(0.0, (now - quote.cycle).total_seconds())
        feed_delay = quote.feed_delay_seconds
        limit = _feed_delay_limit()
        if feed_delay is None or feed_delay > limit or receive_age > self.settings.max_data_age_seconds:
            return {
                "state": "POSITION_FEED_DELAYED",
                "reason": (
                    f"refusing to mark/exit {symbol} from stale market data "
                    f"(receive_age={receive_age:.1f}s feed_delay="
                    f"{'unknown' if feed_delay is None else f'{feed_delay:.1f}s'})"
                ),
                "mode": mode,
                "last_signal": None,
                "last_action": None,
                "position": position,
            }
        return super()._manage_position(position, now, mode)

    def _select_ensemble_opportunity(
        self,
        latest: MarketCycle,
        previous: MarketCycle,
        cycles: tuple[MarketCycle, ...],
        settled_cash: float,
        receive_age: float,
        eastern: datetime,
    ) -> tuple[PaperSignal, OpportunityResult, EnsembleForecast] | None:
        horizon = min(_horizon_minutes(), max(0.5, self._minutes_to_close(eastern) - 1.0))
        forecast = build_market_ensemble(cycles, horizon)
        threshold = _direction_threshold()
        if forecast.probability_up >= threshold:
            right = "call"
        elif forecast.probability_up <= 1.0 - threshold:
            right = "put"
        else:
            return None

        scenarios = generate_return_scenarios(forecast, points=31, tail_scale=1.20)
        budget = settled_cash * self.risk_config.maximum_capital_fraction
        fresh = {row.symbol: row for row in self._fresh_feed_rows(latest)}
        evaluated: list[tuple[OpportunityResult, QuoteRow, float]] = []

        for row in latest.rows:
            if row.symbol not in fresh or row.right != right or row.bid <= 0.0 or row.ask <= 0.0:
                continue
            if row.spread_fraction > min(self.settings.maximum_spread_fraction, self.risk_config.maximum_spread_fraction):
                continue
            if row.liquidity_score < max(self.settings.minimum_liquidity_score, self.risk_config.minimum_liquidity_score):
                continue
            moneyness = abs(row.strike - latest.spot) / max(latest.spot, 1e-9)
            if moneyness > self.settings.maximum_moneyness_fraction:
                continue
            contract_cost = row.ask * 100.0 + self.settings.fee_per_contract
            if contract_cost > budget + 1e-9:
                continue

            minutes_to_expiry = max(0.0, self._minutes_to_close(eastern))
            try:
                greeks = model_greeks_from_quote(
                    right=right,  # type: ignore[arg-type]
                    bid=row.bid,
                    ask=row.ask,
                    spot=latest.spot,
                    strike=row.strike,
                    minutes_to_expiry=minutes_to_expiry,
                )
            except ValueError:
                continue
            if not (0.05 <= abs(greeks.delta) <= 0.80):
                continue

            quote = OptionQuote(
                symbol=row.symbol,
                right=right,  # type: ignore[arg-type]
                strike=row.strike,
                bid=row.bid,
                ask=row.ask,
                delta=greeks.delta,
                gamma=greeks.gamma,
                theta=greeks.theta_per_day,
                vega=greeks.vega_per_vol_point,
                implied_volatility=greeks.implied_volatility,
                volume=row.volume,
                open_interest=row.open_interest,
                underlying_price=latest.spot,
                minutes_to_expiry=minutes_to_expiry,
            )
            market = MarketSnapshot(
                spot=latest.spot,
                bid=latest.spot,
                ask=latest.spot,
                realized_volatility=forecast.volatility,
                implied_volatility=greeks.implied_volatility,
                volume_ratio=1.0,
                minutes_to_close=minutes_to_expiry,
                data_age_seconds=max(receive_age, float(row.feed_delay_seconds or 0.0)),
                ood_score=1.0 - forecast.regime_match_score,
            )
            result = evaluate_option_candidate(
                quote,
                scenarios,
                forecast,
                market,
                horizon_minutes=horizon,
                fee_per_contract=self.settings.fee_per_contract,
                exit_slippage_spread_fraction=0.35,
                uncertainty_aversion=0.90,
            )
            probability_positive = _positive_return_probability(result)
            if result.candidate.lower_confidence_edge < _minimum_lower_edge():
                continue
            if probability_positive < self.risk_config.minimum_positive_edge_probability:
                continue
            evaluated.append((result, row, probability_positive))

        if not evaluated:
            return None
        ranked = rank_opportunities(item[0] for item in evaluated)
        winner = ranked[0]
        winner_row = next(row for result, row, _ in evaluated if result is winner)
        probability_positive = next(p for result, _, p in evaluated if result is winner)
        premium_move = _premium_move(winner_row, previous)
        spot_move = latest.spot / previous.spot - 1.0 if previous.spot > 0.0 else 0.0
        reason = (
            f"ensemble_{right} p_up={forecast.probability_up:.3f} "
            f"net_ev={winner.expected_return:+.2%} lower_edge={winner.candidate.lower_confidence_edge:+.2%} "
            f"p_net_positive={probability_positive:.3f} agreement={forecast.agreement_score:.2f} "
            f"calibration={forecast.calibration_score:.2f} regime={forecast.regime_match_score:.2f}"
        )
        signal = PaperSignal(
            symbol=winner_row.symbol,
            right=right,
            spot=latest.spot,
            spot_move_fraction=spot_move,
            premium_move_fraction=premium_move,
            edge_proxy=winner.candidate.lower_confidence_edge,
            liquidity_score=winner.candidate.liquidity_score,
            spread_fraction=winner.candidate.spread_fraction,
            ask=winner_row.ask,
            bid=winner_row.bid,
            contract_cost=winner.candidate.entry_cost_per_contract,
            reason=reason,
        )
        return signal, winner, forecast

    def tick(self, now: datetime | None = None) -> dict[str, object]:
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        eastern = current.astimezone(EASTERN)
        mode = self.mode_getter().strip().upper()
        released = self.account_store.settle_due(eastern.date())
        positions = self.account_store.positions()

        if positions:
            result = self._manage_position(positions[0], current, mode)
            result["settled_released"] = released
            return self._finish(current, result)

        common = {
            "mode": mode,
            "last_signal": None,
            "last_action": None,
            "settled_released": released,
            "risk": None,
        }
        if mode not in {"PAPER", "SHADOW"}:
            return self._finish(current, {**common, "state": "IDLE", "reason": f"paper automation idle while mode={mode}"})
        if not self._market_session(eastern):
            return self._finish(current, {**common, "state": "MARKET_CLOSED", "reason": "waiting for the configured SPY 0DTE session"})

        cycles = self.market_reader.latest_cycles(90)
        if len(cycles) < 8:
            return self._finish(current, {**common, "state": "WAITING_HISTORY", "reason": "building enough fresh market history for the ensemble"})
        latest, previous = cycles[0], cycles[1]
        receive_age = max(0.0, (current - latest.received_at).total_seconds())
        if receive_age > self.settings.max_data_age_seconds:
            return self._finish(
                current,
                {
                    **common,
                    "state": "WAITING_FRESH_DATA",
                    "reason": f"latest chain snapshot is {receive_age:.1f}s old",
                    "data_age_seconds": receive_age,
                },
            )

        fresh_rows = self._fresh_feed_rows(latest)
        if not fresh_rows:
            delays = [row.feed_delay_seconds for row in latest.rows if row.feed_delay_seconds is not None]
            best_delay = min(delays) if delays else None
            limit = _feed_delay_limit()
            reason = (
                f"market feed is {'timestamp-unknown' if best_delay is None else f'{best_delay:.1f}s delayed'}; "
                f"paper entries require <= {limit:.1f}s"
            )
            return self._finish(
                current,
                {
                    **common,
                    "state": "FEED_DELAYED",
                    "reason": reason,
                    "data_age_seconds": receive_age,
                    "feed_delay_seconds": best_delay,
                },
            )

        account = self.account_store.snapshot()
        if account.settled_cash <= 0.0:
            return self._finish(current, {**common, "state": "NO_SETTLED_CASH", "reason": "paper account has no settled buying power"})

        selected = self._select_ensemble_opportunity(
            latest,
            previous,
            cycles,
            account.settled_cash,
            receive_age,
            eastern,
        )
        if selected is None:
            return self._finish(
                current,
                {
                    **common,
                    "state": "NO_SIGNAL",
                    "reason": "no fresh affordable SPY 0DTE contract has positive execution-aware ensemble edge",
                },
            )
        signal, opportunity, forecast = selected

        if self._in_cooldown(current):
            return self._finish(
                current,
                {
                    **common,
                    "state": "COOLDOWN",
                    "reason": "recent paper execution is still inside the re-entry cooldown",
                    "last_signal": signal.as_dict(),
                },
            )

        selected_quote = next(row for row in latest.rows if row.symbol == signal.symbol)
        actual_delay = float(selected_quote.feed_delay_seconds or 0.0)
        positive_probability = _positive_return_probability(opportunity)
        model_health = _clip(
            statistics.fmean(
                (forecast.agreement_score, forecast.calibration_score, forecast.regime_match_score)
            ),
            0.0,
            1.0,
        )
        equity, high_watermark, daily_start, daily_realized = _account_risk_state(
            self.account_store,
            account,
            current,
        )
        risk_decision = choose_dynamic_long_option_size(
            DynamicRiskRequest(
                account_equity=equity,
                high_watermark=high_watermark,
                settled_cash=account.settled_cash,
                daily_start_equity=daily_start,
                daily_realized_pnl=daily_realized,
                entry_cost_per_contract=opportunity.candidate.entry_cost_per_contract,
                scenario_returns=opportunity.candidate.scenario_returns,
                scenario_probabilities=opportunity.candidate.scenario_probabilities,
                positive_edge_probability=positive_probability,
                liquidity_score=opportunity.candidate.liquidity_score,
                spread_fraction=opportunity.candidate.spread_fraction,
                quote_age_seconds=max(receive_age, actual_delay),
                minutes_to_expiry=max(0.0, self._minutes_to_close(eastern)),
                ask_size=0,
                existing_portfolio_es99_dollars=0.0,
                event_multiplier=self.event_multiplier_getter(),
                model_health_multiplier=model_health,
            ),
            self.risk_config,
        )
        risk_payload = {
            **risk_decision.as_dict(),
            "profile": RISK_PROFILE,
            "signal_model": SIGNAL_STRATEGY,
            "source_feed_delay_seconds": actual_delay,
            "lower_confidence_edge": opportunity.candidate.lower_confidence_edge,
            "forecast_probability_up": forecast.probability_up,
            "forecast_expected_log_return": forecast.expected_log_return,
            "forecast_volatility": forecast.volatility,
            "forecast_agreement": forecast.agreement_score,
            "forecast_calibration": forecast.calibration_score,
            "forecast_regime_match": forecast.regime_match_score,
            "model_weights": {name: weight for name, weight in forecast.model_weights},
            "scenario_returns": list(opportunity.candidate.scenario_returns),
            "scenario_probabilities": list(opportunity.candidate.scenario_probabilities),
        }
        if not risk_decision.allowed:
            return self._finish(
                current,
                {
                    **common,
                    "state": "SHADOW_RISK_REJECTED" if mode == "SHADOW" else "RISK_REJECTED",
                    "reason": f"dynamic risk rejected ensemble opportunity: {risk_decision.reason}",
                    "last_signal": signal.as_dict(),
                    "risk": risk_payload,
                },
            )
        if mode == "SHADOW":
            return self._finish(
                current,
                {
                    **common,
                    "state": "SHADOW_SIGNAL",
                    "reason": f"ensemble + risk approve {risk_decision.contracts} contract(s); SHADOW records no simulated position",
                    "last_signal": signal.as_dict(),
                    "last_action": "WOULD_BUY",
                    "risk": risk_payload,
                },
            )

        contracts = risk_decision.contracts
        stop = max(0.0, signal.contract_cost * (1.0 - self.settings.stop_loss_fraction))
        target = signal.contract_cost * (1.0 + self.settings.take_profit_fraction)
        risk_reason = (
            f"risk_profile={RISK_PROFILE} size={contracts} mode={risk_decision.sizing_mode} "
            f"ev=${risk_decision.expected_net_ev_per_contract:.2f}/contract "
            f"es99=${risk_decision.es99_per_contract:.2f}/contract"
        )
        position = self.account_store.open_long(
            symbol=signal.symbol,
            quantity=contracts,
            fill_price=signal.contract_cost,
            opened_at=current,
            entry_spot=signal.spot,
            strategy=POSITION_STRATEGY,
            stop_price=stop,
            target_price=target,
            max_hold_seconds=self.settings.max_hold_seconds,
            reason=f"{signal.reason}; {risk_reason}",
        )
        log.info(
            "paper ensemble buy symbol=%s quantity=%d cost=%.2f spot=%.2f p_up=%.3f delay=%.3fs",
            signal.symbol,
            contracts,
            signal.contract_cost,
            signal.spot,
            forecast.probability_up,
            actual_delay,
        )
        return self._finish(
            current,
            {
                **common,
                "state": "POSITION_OPENED",
                "reason": f"{signal.reason}; {risk_reason}",
                "last_signal": signal.as_dict(),
                "last_action": "BUY",
                "position": position,
                "risk": risk_payload,
            },
        )

    @staticmethod
    def _finish(now: datetime, values: dict[str, object]) -> dict[str, object]:
        payload = {
            "enabled": True,
            "strategy": SIGNAL_STRATEGY,
            "risk_profile": RISK_PROFILE,
            "last_tick": now.isoformat(),
            **values,
        }
        _publish(**payload)
        return payload


def run_forever(mode_getter: Callable[[], str]) -> None:
    settings = PaperAutoSettings.from_env()
    risk_config = risk_config_from_env()
    market_path = Path(os.environ.get("COLLECTOR_DB_PATH", "/data/spy_0dte.sqlite"))
    account = PaperAccountStore(
        _paper_db_path(),
        default_starting_cash=_paper_starting_cash(),
    )
    trader = EnsemblePaperAutoTrader(
        mode_getter=mode_getter,
        market_reader=SnapshotReader(market_path),
        account_store=account,
        settings=settings,
        risk_config=risk_config,
    )
    _publish(
        enabled=True,
        state="STARTING",
        reason="execution-aware market ensemble SHADOW/PAPER loop starting",
        strategy=SIGNAL_STRATEGY,
        risk_profile=RISK_PROFILE,
    )
    log.info(
        "paper ensemble autotrader started signal=%s risk=%s tick=%.2fs feed_delay_limit=%.2fs market_db=%s",
        SIGNAL_STRATEGY,
        RISK_PROFILE,
        settings.tick_seconds,
        _feed_delay_limit(),
        market_path,
    )
    while True:
        started = time.monotonic()
        try:
            trader.tick()
        except Exception as exc:
            log.exception("ensemble paper automation tick failed")
            _publish(
                enabled=True,
                state="ERROR",
                reason=f"{type(exc).__name__}: {exc}",
                last_tick=datetime.now(timezone.utc).isoformat(),
            )
        elapsed = time.monotonic() - started
        time.sleep(max(0.05, settings.tick_seconds - elapsed))
