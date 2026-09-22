from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ServiceKind(str, Enum):
    FREE = "free"
    PAID = "paid"


@dataclass(frozen=True)
class RuntimePolicy:
    """Global cost/safety policy for external services.

    The default policy is intentionally zero-cost. Paid data or AI adapters must
    be explicitly enabled by configuration; importing an optional provider is
    never enough to authorize spend.
    """

    allow_paid_services: bool = False
    allow_live_orders: bool = False
    monthly_external_budget_usd: float = 0.0

    def authorize_service(
        self,
        *,
        service_name: str,
        kind: ServiceKind,
        estimated_monthly_cost_usd: float = 0.0,
    ) -> None:
        if estimated_monthly_cost_usd < 0:
            raise ValueError("estimated cost cannot be negative")
        if kind is ServiceKind.FREE:
            return
        if not self.allow_paid_services:
            raise PermissionError(f"paid service disabled by runtime policy: {service_name}")
        if estimated_monthly_cost_usd > self.monthly_external_budget_usd:
            raise PermissionError(
                f"service exceeds configured external budget: {service_name}"
            )


FREE_FIRST_POLICY = RuntimePolicy()
