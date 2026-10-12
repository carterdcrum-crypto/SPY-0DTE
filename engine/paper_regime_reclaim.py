"""Forward-only combined Regime Reclaim experiment for SPY 0DTE.

No broker submission. Uses time-ordered option-chain observations, executable
ask/bid marks, the existing scenario EV/risk engine and the paper ledger.
This is a *new, explicitly specified* signal; historical proxy results are
not comparable to it until replayed on the same data and fill assumptions.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal

from .paper_autotrader import EASTERN, MarketCycle, QuoteRow, _parse_datetime
from .paper_ensemble_autotrader import EnsemblePaperAutoTrader, _feed_delay_limit
from .paper_delayed_simulation import DelayedSimulationPaperAutoTrader

SIGNAL_NAME = "combined_regime_reclaim_v1"
POSITION_NAME = "combined_regime_reclaim_v1+dynamic_posterior_v2"
EXIT_NAME = "regime_30pct_trail_35pct_loss_600s_hold_v1"
MAX_SPREAD = 0.035
MAX_IV_TO_RV = 1.50
HARD_LOSS = 0.35
TRAIL = 0.30
MAX_HOLD_SECONDS = 600
ANNUAL_TRADING_SECONDS = 252 * 390 * 60


@dataclass(frozen=True)
class Reclaim:
    right: Literal["call", "put"]
    variant: Literal["trend_pullback_reclaim", "stretch_reversal"]


def _ema(values: list[float], span: int) -> list[float]:
    alpha = 2.0 / (span + 1)
    result = [values[0]]
    for value in values[1:]:
        result.append(alpha * value + (1 - alpha) * result[-1])
    return result


def _ordered_spots(cycles: tuple[MarketCycle, ...]) -> list[tuple[datetime, float]]:
    """No forward fill across a trading-day boundary or stale/duplicate samples."""
    if not cycles:
        return []
    target_date = cycles[0].received_at.astimezone(EASTERN).date()
    observations = sorted(
        ((c.received_at, c.spot) for c in cycles
         if c.spot > 0 and c.received_at.astimezone(EASTERN).date() == target_date),
        key=lambda pair: pair[0],
    )
    result: list[tuple[datetime, float]] = []
    for timestamp, spot in observations:
        if result and timestamp <= result[-1][0]:
            continue
        if result and (timestamp - result[-1][0]).total_seconds() > 30:
            result = []  # Never silently bridge a feed gap.
        result.append((timestamp, spot))
    return result


def detect_regime_reclaim(cycles: tuple[MarketCycle, ...]) -> Reclaim | None:
    """Causal, completed-snapshot reclaim/reversal; never references future bars.

    Trend: 12/36-cycle EMA slope with a completed close across the fast EMA.
    Stretch: a recent +/-1.6 sigma excursion followed by a +/-0.9 sigma
    reclaim with confirming price direction. The signal is only on the cross.
    """
    series = _ordered_spots(cycles)
    if len(series) < 45 or (series[-1][0] - series[0][0]).total_seconds() < 60:
        return None
    spots = [spot for _, spot in series]
    fast, slow = _ema(spots, 12), _ema(spots, 36)
    if fast[-2] > slow[-2] and slow[-1] > slow[-8] and spots[-2] <= fast[-2] and spots[-1] > fast[-1]:
        return Reclaim("call", "trend_pullback_reclaim")
    if fast[-2] < slow[-2] and slow[-1] < slow[-8] and spots[-2] >= fast[-2] and spots[-1] < fast[-1]:
        return Reclaim("put", "trend_pullback_reclaim")

    history = spots[-41:-1]
    center = statistics.fmean(history)
    sigma = statistics.pstdev(history)
    if sigma < 1e-6:
        return None
    previous_z = (spots[-2] - center) / sigma
    latest_z = (spots[-1] - center) / sigma
    recent_z = [(p - center) / sigma for p in spots[-9:-1]]
    if min(recent_z) <= -1.6 and previous_z <= -0.9 < latest_z and spots[-1] > spots[-2]:
        return Reclaim("call", "stretch_reversal")
    if max(recent_z) >= 1.6 and previous_z >= 0.9 > latest_z and spots[-1] < spots[-2]:
        return Reclaim("put", "stretch_reversal")
    return None


def realized_vol_annualized(cycles: tuple[MarketCycle, ...]) -> float | None:
    """Quadratic-variation RV of sampled SPY spot; use actual elapsed seconds."""
    series = _ordered_spots(cycles)
    if len(series) < 30 or (series[-1][0] - series[0][0]).total_seconds() < 60:
        return None
    pairs = [
        (math.log(p1 / p0), (t1 - t0).total_seconds())
        for (t0, p0), (t1, p1) in zip(series, series[1:])
        if p0 > 0 and p1 > 0 and 0 < (t1 - t0).total_seconds() <= 30
    ]
    elapsed = sum(seconds for _, seconds in pairs)
    if elapsed < 60:
        return None
    variance = sum(ret * ret for ret, _ in pairs) / elapsed * ANNUAL_TRADING_SECONDS
    return math.sqrt(variance) if variance > 1e-10 else None


class RegimeReclaimMixin:
    """Replace only PAPER/SHADOW entry/exit logic; retain existing account risk."""

    position_strategy = POSITION_NAME
    paper_target_placeholder = 1e99  # Schema requires a positive target; NEVER executed by this strategy.

    def _direction_for_entry(self, forecast, cycles: tuple[MarketCycle, ...]) -> str | None:
        self._last_reclaim = detect_regime_reclaim(cycles)
        return None if self._last_reclaim is None else self._last_reclaim.right

    def _fresh_feed_rows(self, latest: MarketCycle) -> tuple[QuoteRow, ...]:
        return tuple(row for row in super()._fresh_feed_rows(latest)
                     if row.spread_fraction <= MAX_SPREAD)

    def _candidate_regime_allowed(self, row, greeks, cycles: tuple[MarketCycle, ...]) -> bool:
        rv = realized_vol_annualized(cycles)
        iv = greeks.implied_volatility
        return (rv is not None and math.isfinite(iv) and iv > 0
                and iv / rv <= MAX_IV_TO_RV and row.spread_fraction <= MAX_SPREAD)

    def _select_ensemble_opportunity(self, latest, previous, cycles, settled_cash, receive_age, eastern):
        # Intentionally bypass optional AI overrides; the research signal remains
        # Regime Reclaim, followed by the existing execution-aware EV and risk veto.
        selected = EnsemblePaperAutoTrader._select_ensemble_opportunity(
            self, latest, previous, cycles, settled_cash, receive_age, eastern
        )
        if selected is None:
            return None
        signal, opportunity, forecast = selected
        variant = self._last_reclaim.variant
        return replace(signal, reason=f"{SIGNAL_NAME} variant={variant}; {signal.reason}"), opportunity, forecast

    def _manage_position(self, position, now: datetime, mode: str):
        # Never change the exit plan of a legacy position when deploying mid-trade.
        if str(position.get("strategy")) != POSITION_NAME:
            return super()._manage_position(position, now, mode)
        symbol = str(position["symbol"])
        quote = self.market_reader.latest_quote(symbol)
        if quote is None:
            return {"state": "POSITION_WAITING_QUOTE", "reason": "no held-option quote",
                    "mode": mode, "position": position, "last_signal": None,
                    "last_action": None, "exit_profile": EXIT_NAME}
        age = (now - quote.cycle).total_seconds()
        delay = quote.feed_delay_seconds
        if (age < 0 or age > self.settings.max_data_age_seconds or delay is None
                or delay < 0 or delay > _feed_delay_limit() or quote.bid <= 0):
            return {"state": "POSITION_FEED_DELAYED",
                    "reason": "refusing paper exit without a fresh executable bid",
                    "mode": mode, "position": position, "last_signal": None,
                    "last_action": None, "exit_profile": EXIT_NAME}
        cost = float(position["average_cost"])
        qty = int(position["quantity"])
        net_bid = max(0.0, quote.bid * 100.0 - self.settings.fee_per_contract)
        peak = self.account_store.record_exit_peak(symbol, net_bid)
        held = max(0.0, (now - _parse_datetime(str(position["opened_at"]))).total_seconds())
        until_close = self._minutes_to_close(now.astimezone(EASTERN))
        reason = None
        if net_bid <= cost * (1.0 - HARD_LOSS):
            reason = "regime_hard_loss_35pct"
        elif peak > cost and net_bid <= peak * (1.0 - TRAIL):
            reason = "regime_trail_30pct"
        elif held >= min(MAX_HOLD_SECONDS, int(position.get("max_hold_seconds") or MAX_HOLD_SECONDS)):
            reason = "regime_max_hold_10m"
        elif until_close <= self.settings.force_exit_minutes_before_close:
            reason = "regime_session_close"

        if reason is not None:
            closed = self.account_store.close_long(
                symbol=symbol, quantity=qty, fill_price=net_bid,
                closed_at=now, trade_date=now.astimezone(EASTERN).date(), reason=reason,
            )
            return {"state": "POSITION_CLOSED", "reason": reason, "mode": mode,
                    "last_signal": None, "last_action": "SELL", "closed_trade": closed,
                    "exit_profile": EXIT_NAME}
        return {"state": "POSITION_OPEN", "reason": "regime reclaim: no profit target, tracking 30% trail",
                "mode": mode, "last_signal": None, "last_action": None,
                "exit_profile": EXIT_NAME,
                "position": {**position, "mark_value": net_bid,
                             "unrealized_pnl": (net_bid - cost) * qty,
                             "peak_exit_value": peak, "held_seconds": held,
                             "quote_received_at": quote.cycle.isoformat()}}

    @staticmethod
    def _finish(now: datetime, values: dict[str, object]) -> dict[str, object]:
        return EnsemblePaperAutoTrader._finish(
            now, {**values, "strategy": SIGNAL_NAME, "exit_profile": EXIT_NAME}
        )


class RegimeReclaimPaperTrader(RegimeReclaimMixin, EnsemblePaperAutoTrader):
    """Native fresh-timestamp PAPER/SHADOW trader; no live order methods."""


class RegimeReclaimDelayedPaperTrader(RegimeReclaimMixin, DelayedSimulationPaperAutoTrader):
    """Time-shifted delayed-feed PAPER/SHADOW trader; never accepted as live data."""
