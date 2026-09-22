from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal


OrderSide = Literal["BUY", "SELL"]
PositionIntent = Literal["BUY_TO_OPEN", "SELL_TO_CLOSE"]
OptionType = Literal["CALL", "PUT"]


@dataclass(frozen=True)
class OptionOrderRequest:
    """Broker-neutral single-leg long-premium option order.

    The engine intentionally permits only opening a long option or closing that
    same long option. Short option intents are excluded from this domain model.
    """

    account_id: str
    client_order_id: str
    underlying: str
    strike_price: float
    expiration_date: str
    option_type: OptionType
    side: OrderSide
    position_intent: PositionIntent
    quantity: int
    limit_price: float

    def __post_init__(self) -> None:
        if self.underlying.upper() != "SPY":
            raise ValueError("execution layer is restricted to SPY")
        if not self.account_id:
            raise ValueError("account_id is required")
        if not self.client_order_id or len(self.client_order_id) > 32:
            raise ValueError("client_order_id must be 1..32 characters")
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")
        if self.limit_price <= 0:
            raise ValueError("limit_price must be positive")
        if self.strike_price <= 0:
            raise ValueError("strike_price must be positive")
        date.fromisoformat(self.expiration_date)

        if self.position_intent == "BUY_TO_OPEN" and self.side != "BUY":
            raise ValueError("BUY_TO_OPEN requires BUY side")
        if self.position_intent == "SELL_TO_CLOSE" and self.side != "SELL":
            raise ValueError("SELL_TO_CLOSE requires SELL side")


@dataclass(frozen=True)
class ReplaceOrderRequest:
    account_id: str
    client_order_id: str
    quantity: int
    limit_price: float

    def __post_init__(self) -> None:
        if not self.account_id or not self.client_order_id:
            raise ValueError("account_id and client_order_id are required")
        if self.quantity <= 0 or self.limit_price <= 0:
            raise ValueError("quantity and limit_price must be positive")
