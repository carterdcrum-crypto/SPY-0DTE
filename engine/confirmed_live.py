from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .broker import OptionOrderRequest
from .live_order_ticket import LiveOrderAuthorization, LiveOrderTicketStore
from .tradier_live import TradierLiveClient, TradierLiveError


class LiveSafetyError(RuntimeError):
    pass


@dataclass(frozen=True)
class LiveSafetyPolicy:
    max_contracts: int
    max_order_debit: float
    confirmation_ttl_seconds: int = 45

    @classmethod
    def from_env(cls) -> "LiveSafetyPolicy":
        raw_contracts = os.environ.get("LIVE_MAX_CONTRACTS", "").strip()
        raw_debit = os.environ.get("LIVE_MAX_ORDER_DEBIT", "").strip()
        raw_ttl = os.environ.get("LIVE_CONFIRMATION_TTL_SECONDS", "45").strip()
        if not raw_contracts or not raw_debit:
            raise LiveSafetyError(
                "LIVE_MAX_CONTRACTS and LIVE_MAX_ORDER_DEBIT must be configured before live preparation"
            )
        try:
            max_contracts = int(raw_contracts)
            max_order_debit = float(raw_debit)
            ttl = int(raw_ttl)
        except ValueError as exc:
            raise LiveSafetyError("live safety limits must be numeric") from exc
        if max_contracts < 1 or max_contracts > 100:
            raise LiveSafetyError("LIVE_MAX_CONTRACTS must be between 1 and 100")
        if max_order_debit <= 0:
            raise LiveSafetyError("LIVE_MAX_ORDER_DEBIT must be positive")
        if ttl < 5 or ttl > 120:
            raise LiveSafetyError("LIVE_CONFIRMATION_TTL_SECONDS must be between 5 and 120")
        return cls(
            max_contracts=max_contracts,
            max_order_debit=max_order_debit,
            confirmation_ttl_seconds=ttl,
        )

    def validate(self, order: OptionOrderRequest) -> None:
        if order.quantity > self.max_contracts:
            raise LiveSafetyError("order quantity exceeds configured live contract ceiling")
        if order.position_intent == "BUY_TO_OPEN":
            debit = float(order.limit_price) * 100.0 * int(order.quantity)
            if debit > self.max_order_debit:
                raise LiveSafetyError("order debit exceeds configured live dollar ceiling")


@dataclass(frozen=True)
class PreparedLiveOrder:
    ticket_token: str
    preview: dict[str, Any]
    expires_in_seconds: int


class ConfirmedLiveCoordinator:
    """Prepare, authorize, and reconcile production orders without autonomous submit.

    The same strategy/risk stack may create OptionOrderRequest objects for PAPER
    and LIVE. This coordinator owns the production-only boundary: account checks,
    user-defined hard rails, broker preview, exact-order authorization, and
    reconciliation reads. A broker transmission call is deliberately not part of
    this class, so the autonomous paper loop cannot submit real orders by calling
    it directly.
    """

    def __init__(
        self,
        *,
        client: TradierLiveClient,
        ticket_store: LiveOrderTicketStore,
        policy: LiveSafetyPolicy,
    ) -> None:
        self.client = client
        self.ticket_store = ticket_store
        self.policy = policy

    @classmethod
    def from_env(cls) -> "ConfirmedLiveCoordinator":
        db_path = os.environ.get("LIVE_TICKET_DB_PATH", "").strip()
        if not db_path:
            control = Path(os.environ.get("CONTROL_DB_PATH", "/data/spy_control.sqlite"))
            db_path = str(control.with_name("spy_live_tickets.sqlite"))
        return cls(
            client=TradierLiveClient.from_env(),
            ticket_store=LiveOrderTicketStore(db_path),
            policy=LiveSafetyPolicy.from_env(),
        )

    def prepare(
        self,
        order: OptionOrderRequest,
        *,
        user_subject: str,
        explicit_confirmation: str,
    ) -> PreparedLiveOrder:
        self.policy.validate(order)
        profile = self.client.account_profile()
        if not profile.is_active:
            raise LiveSafetyError("Tradier account is not active")
        if not profile.is_cash:
            raise LiveSafetyError("live coordinator is restricted to cash accounts")
        if profile.account_number != order.account_id:
            raise LiveSafetyError("order account does not match authenticated brokerage account")

        preview = self.client.preview_option_order(order)
        broker_preview = preview.get("order")
        if not isinstance(broker_preview, dict):
            raise TradierLiveError("Tradier preview response is missing order data")
        if str(broker_preview.get("status") or "").lower() != "ok":
            raise TradierLiveError("Tradier preview did not accept the order")
        if broker_preview.get("result") is False:
            raise TradierLiveError("Tradier preview rejected the order")

        ticket = self.ticket_store.issue(
            order,
            user_subject=user_subject,
            explicit_confirmation=explicit_confirmation,
            ttl_seconds=self.policy.confirmation_ttl_seconds,
        )
        return PreparedLiveOrder(
            ticket_token=ticket,
            preview=preview,
            expires_in_seconds=self.policy.confirmation_ttl_seconds,
        )

    def consume_authorization(
        self,
        ticket_token: str,
        order: OptionOrderRequest,
        *,
        user_subject: str,
    ) -> LiveOrderAuthorization:
        self.policy.validate(order)
        return self.ticket_store.consume(
            ticket_token,
            order,
            user_subject=user_subject,
        )

    def reconcile(self) -> dict[str, Any]:
        profile = self.client.account_profile()
        account_id = profile.account_number
        return {
            "account": profile.as_dict(),
            "balances": self.client.balances(account_id),
            "positions": self.client.positions(account_id),
            "orders": self.client.account_orders(account_id, limit=250),
        }
