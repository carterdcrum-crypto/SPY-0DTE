from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from .paper_autotrader import EASTERN, _parse_datetime
from .paper_dynamic_exit import (
    DynamicExitMetrics,
    _confirmation_ticks,
    _minimum_expected_return,
    _minimum_hold_seconds,
    _minimum_lower_edge,
    _minimum_positive_probability,
    _reversal_support_threshold,
)
from .paper_exit_audit import ExitAuditStore
from .paper_ensemble_autotrader import _feed_delay_limit

log = logging.getLogger("spy0dte.paper.exit_v2")
SAFER_EXIT_PROFILE = "ensemble_dynamic_exit_v2"
SOFT_EXIT_REASONS = frozenset({"direction_reversal", "edge_decay"})


def _strong_reversal_support_threshold() -> float:
    from .paper_dynamic_autotrader import _env_float

    return _env_float(
        "PAPER_DYNAMIC_EXIT_STRONG_REVERSAL_SUPPORT",
        0.40,
        minimum=0.10,
        maximum=0.49,
    )


def _minimum_reversal_model_quality() -> float:
    from .paper_dynamic_autotrader import _env_float

    return _env_float(
        "PAPER_DYNAMIC_EXIT_MIN_REVERSAL_MODEL_QUALITY",
        0.50,
        minimum=0.0,
        maximum=1.0,
    )


def _edge_failed(metrics: DynamicExitMetrics) -> bool:
    return (
        metrics.expected_return <= _minimum_expected_return()
        and metrics.lower_confidence_edge <= _minimum_lower_edge()
        and metrics.positive_return_probability < _minimum_positive_probability()
    )


def classify_safer_dynamic_exit(metrics: DynamicExitMetrics) -> str | None:
    """Require a meaningful reversal, not a one-number wobble around 50/50.

    A moderate directional flip only becomes a reversal exit when the option's
    remaining execution-aware edge has also failed. A very strong reversal can
    stand on its own only when the ensemble's agreement/calibration/regime
    quality is not itself weak. This keeps noisy direction estimates from
    dominating the richer option P&L model.
    """

    edge_failed = _edge_failed(metrics)
    model_quality = min(
        metrics.agreement_score,
        metrics.calibration_score,
        metrics.regime_match_score,
    )
    strong_reversal = (
        metrics.direction_support <= _strong_reversal_support_threshold()
        and model_quality >= _minimum_reversal_model_quality()
    )
    corroborated_reversal = (
        metrics.direction_support < _reversal_support_threshold()
        and edge_failed
    )
    if strong_reversal or corroborated_reversal:
        return "direction_reversal"
    return "edge_decay" if edge_failed else None


@dataclass(frozen=True)
class ExitConfirmationState:
    reason: str
    count: int
    last_cycle: datetime


class SaferDynamicExitPolicyMixin:
    """Drop-in v2 policy for the existing ensemble dynamic-exit trader.

    Confirmation counts *distinct market snapshots*, not 1-second engine ticks.
    It also records post-exit counterfactual marks for PAPER research. The audit
    ledger is observational only and never changes trading/account state.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._exit_confirmations: dict[str, ExitConfirmationState] = {}
        self._exit_audit_store = ExitAuditStore(self.account_store.path)

    def _update_exit_audits(self, now: datetime) -> None:
        for item in self._exit_audit_store.pending_marks(now):
            quote = self.market_reader.latest_quote(item.symbol)
            if quote is None or quote.bid <= 0.0:
                continue
            hypothetical_exit_value = max(
                0.0,
                quote.bid * 100.0 - self.settings.fee_per_contract,
            )
            mark = self._exit_audit_store.record_mark(
                item,
                observed_at=now,
                hypothetical_exit_value=hypothetical_exit_value,
            )
            log.info(
                "paper exit audit symbol=%s reason=%s horizon=%ss actual=%.2f "
                "hypothetical=%.2f pnl_delta=%+.2f hypothetical_pnl=%+.2f",
                item.symbol,
                item.reason,
                item.horizon_seconds,
                item.actual_exit_value,
                hypothetical_exit_value,
                float(mark["pnl_delta_vs_actual"]),
                float(mark["hypothetical_realized_pnl"]),
            )

    def tick(self, now: datetime | None = None):
        tick_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        self._update_exit_audits(tick_now)
        return super().tick(now)

    def _close_position(self, *, position, quote, now: datetime, reason: str):
        symbol = str(position["symbol"])
        quantity = int(position["quantity"])
        actual_exit_value = max(
            0.0,
            quote.bid * 100.0 - self.settings.fee_per_contract,
        )
        result = super()._close_position(
            position=position,
            quote=quote,
            now=now,
            reason=reason,
        )
        self._exit_confirmations.pop(symbol, None)
        if reason in SOFT_EXIT_REASONS:
            closed = result.get("closed_trade") or {}
            audit_id = self._exit_audit_store.record_exit(
                symbol=symbol,
                reason=reason,
                exited_at=now,
                quantity=quantity,
                actual_exit_value=actual_exit_value,
                actual_realized_pnl=float(closed.get("realized_pnl") or 0.0),
                strategy=(
                    None
                    if position.get("strategy") is None
                    else str(position.get("strategy"))
                ),
            )
            result["exit_audit_id"] = audit_id
        result["exit_profile"] = SAFER_EXIT_PROFILE
        return result

    def _manage_position(self, position, now: datetime, mode: str):
        symbol = str(position["symbol"])
        quote = self.market_reader.latest_quote(symbol)
        if quote is None:
            return {
                "state": "POSITION_WAITING_QUOTE",
                "reason": f"waiting for a quote for open paper position {symbol}",
                "mode": mode,
                "last_signal": None,
                "last_action": None,
                "position": position,
                "exit_profile": SAFER_EXIT_PROFILE,
            }

        receive_age = max(0.0, (now - quote.cycle).total_seconds())
        feed_delay = quote.feed_delay_seconds
        limit = _feed_delay_limit()
        if (
            feed_delay is None
            or feed_delay > limit
            or receive_age > self.settings.max_data_age_seconds
        ):
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
                "exit_profile": SAFER_EXIT_PROFILE,
            }

        quantity = int(position["quantity"])
        average_cost = float(position["average_cost"])
        stop_price = float(position.get("stop_price") or 0.0)
        target_price = float(position.get("target_price") or float("inf"))
        opened_text = position.get("opened_at")
        opened_at = _parse_datetime(str(opened_text)) if opened_text else now
        hard_max_hold = int(
            position.get("max_hold_seconds") or self.settings.max_hold_seconds
        )
        held_seconds = max(0.0, (now - opened_at).total_seconds())
        exit_value = max(
            0.0,
            quote.bid * 100.0 - self.settings.fee_per_contract,
        )
        unrealized = (exit_value - average_cost) * quantity
        minutes_to_close = self._minutes_to_close(now.astimezone(EASTERN))

        hard_reason: str | None = None
        if exit_value <= stop_price:
            hard_reason = "stop_loss"
        elif exit_value >= target_price:
            hard_reason = "take_profit"
        elif held_seconds >= hard_max_hold:
            hard_reason = "hard_max_hold"
        elif minutes_to_close <= self.settings.force_exit_minutes_before_close:
            hard_reason = "session_close"
        if hard_reason is not None:
            result = self._close_position(
                position=position,
                quote=quote,
                now=now,
                reason=hard_reason,
            )
            result["mode"] = mode
            return result

        dynamic_metrics: DynamicExitMetrics | None = None
        soft_reason: str | None = None
        if held_seconds >= _minimum_hold_seconds():
            dynamic_metrics = self._remaining_edge(symbol, now)
            if dynamic_metrics is not None:
                soft_reason = classify_safer_dynamic_exit(dynamic_metrics)

        if soft_reason is None:
            self._exit_confirmations.pop(symbol, None)
            streak = 0
            distinct_snapshot = False
        else:
            previous = self._exit_confirmations.get(symbol)
            if previous is None or previous.reason != soft_reason:
                streak = 1
                distinct_snapshot = True
                self._exit_confirmations[symbol] = ExitConfirmationState(
                    reason=soft_reason,
                    count=streak,
                    last_cycle=quote.cycle,
                )
            elif quote.cycle > previous.last_cycle:
                streak = previous.count + 1
                distinct_snapshot = True
                self._exit_confirmations[symbol] = ExitConfirmationState(
                    reason=soft_reason,
                    count=streak,
                    last_cycle=quote.cycle,
                )
            else:
                streak = previous.count
                distinct_snapshot = False

        if soft_reason is not None and streak >= _confirmation_ticks():
            result = self._close_position(
                position=position,
                quote=quote,
                now=now,
                reason=soft_reason,
            )
            result["mode"] = mode
            result["dynamic_exit"] = {
                **(dynamic_metrics.as_dict() if dynamic_metrics else {}),
                "confirmation_snapshots": streak,
                "required_confirmation_snapshots": _confirmation_ticks(),
                "hard_max_hold_seconds": hard_max_hold,
            }
            return result

        if dynamic_metrics is None:
            dynamic_reason = (
                "minimum hold window"
                if held_seconds < _minimum_hold_seconds()
                else "waiting for enough model history to reassess remaining edge"
            )
        elif soft_reason is None:
            dynamic_reason = "remaining execution-aware edge still supports holding"
        else:
            dynamic_reason = (
                f"{soft_reason} distinct-snapshot confirmation "
                f"{streak}/{_confirmation_ticks()}"
            )

        return {
            "state": "POSITION_OPEN",
            "reason": dynamic_reason,
            "mode": mode,
            "last_signal": None,
            "last_action": None,
            "position": {
                **position,
                "mark_value": exit_value,
                "unrealized_pnl": unrealized,
                "held_seconds": held_seconds,
                "quote_received_at": quote.cycle.isoformat(),
                "hard_max_hold_seconds": hard_max_hold,
                "dynamic_exit": {
                    **(dynamic_metrics.as_dict() if dynamic_metrics else {}),
                    "candidate_reason": soft_reason,
                    "confirmation_snapshots": streak,
                    "required_confirmation_snapshots": _confirmation_ticks(),
                    "new_snapshot_confirmed": distinct_snapshot,
                },
            },
            "exit_profile": SAFER_EXIT_PROFILE,
        }
