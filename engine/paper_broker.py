from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, List


@dataclass
class Position:
    quantity: int = 0
    average_cost: float = 0.0


@dataclass(frozen=True)
class Settlement:
    settle_date: date
    amount: float


def next_business_day(day: date) -> date:
    candidate = day + timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


@dataclass
class PaperBroker:
    """Cash-account paper broker with T+1 sale-proceeds settlement.

    All dollar prices are per contract, already including the option multiplier.
    Fills are deliberately conservative: buys fill only when limit >= ask; sells
    fill only when limit <= bid. Sale proceeds become settled on the next
    business day and cannot be recycled immediately.
    """

    settled_cash: float
    positions: Dict[str, Position] = field(default_factory=dict)
    pending_settlements: List[Settlement] = field(default_factory=list)
    realized_pnl: float = 0.0

    def settle(self, today: date) -> float:
        released = sum(s.amount for s in self.pending_settlements if s.settle_date <= today)
        self.pending_settlements = [
            s for s in self.pending_settlements if s.settle_date > today
        ]
        self.settled_cash += released
        return released

    def buy_limit(
        self,
        symbol: str,
        quantity: int,
        limit_price: float,
        ask_price: float,
    ) -> bool:
        if quantity <= 0 or limit_price <= 0 or ask_price <= 0:
            return False
        if limit_price < ask_price:
            return False

        fill_price = ask_price
        cost = fill_price * quantity
        if cost > self.settled_cash + 1e-9:
            return False

        current = self.positions.get(symbol, Position())
        new_quantity = current.quantity + quantity
        new_average = (
            (current.average_cost * current.quantity + fill_price * quantity)
            / new_quantity
        )
        self.positions[symbol] = Position(new_quantity, new_average)
        self.settled_cash -= cost
        return True

    def sell_limit(
        self,
        symbol: str,
        quantity: int,
        limit_price: float,
        bid_price: float,
        trade_date: date,
    ) -> bool:
        if quantity <= 0 or limit_price <= 0 or bid_price <= 0:
            return False
        if limit_price > bid_price:
            return False

        current = self.positions.get(symbol)
        if current is None or quantity > current.quantity:
            return False

        fill_price = bid_price
        proceeds = fill_price * quantity
        self.realized_pnl += (fill_price - current.average_cost) * quantity

        remaining = current.quantity - quantity
        if remaining == 0:
            del self.positions[symbol]
        else:
            self.positions[symbol] = Position(remaining, current.average_cost)

        self.pending_settlements.append(
            Settlement(next_business_day(trade_date), proceeds)
        )
        return True

    @property
    def unsettled_cash(self) -> float:
        return sum(s.amount for s in self.pending_settlements)
