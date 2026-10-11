"""Fixed executable-bid exit rules for SPY long-option positions.

The trailing mark is the best net proceeds per contract observed since entry.
The primary take-profit level is +100% on cost (double the premium).
"""
from __future__ import annotations

import math


def trailing_stop_hit(exit_value: float, peak_value: float) -> bool:
    """True after an executable net bid loses at least 20% from the high-water mark."""
    return peak_value > 0 and exit_value <= peak_value * 0.80 + 1e-9


def profit_exit_quantity(initial_quantity: int, held_quantity: int, already_sold: int = 0) -> int:
    """Sell most at +100%, leave one runner where at least three were bought.

    Two contracts both exit because one contract is not a majority of two.
    A runner remains subject to the same trailing/forced/soft exits.
    """
    initial = max(0, int(initial_quantity))
    held = max(0, int(held_quantity))
    if initial <= 2:
        goal = initial
    else:
        goal = min(initial - 1, math.ceil(initial * 0.75))
    return max(0, min(held, goal - max(0, int(already_sold))))
