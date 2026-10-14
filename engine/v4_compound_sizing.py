"""V4 quantitative position-size challenger. PAPER by default; LIVE opt-in only.

Preserves the original V4 signal and exits. Entire long-option debit is treated
as at risk, regardless of any stop. This is a sizing hypothesis, NOT an edge
estimate or a calibrated Kelly model.
"""
from __future__ import annotations

import math


def adaptive_quantity(*, score: int, equity: float, settled: float, peak: float,
                      debit: float, starting_cash: float = 1000.0,
                      exposure: float = .35, max_contracts: int = 100,
                      bid_size: int = 100, ask_size: int = 100) -> int:
    """Compounding from verified equity; throttle after drawdown, protect gains.

    No fixed 3-contract ceiling. Final executable broker cash/depth/daily-stop
    checks remain independent and authoritative.
    """
    vals = (equity, settled, peak, debit, starting_cash, exposure)
    if not all(math.isfinite(float(x)) for x in vals):
        return 0
    if (equity <= 0 or settled <= 0 or peak <= 0 or debit <= 0
            or starting_cash <= 0 or not 0 < exposure <= 1
            or max_contracts < 1 or bid_size < 1 or ask_size < 1):
        return 0
    base = 3 if score >= 5 else 2 if score >= 4 else 1
    # The score bucket is not a probability: do not pyramid weak signals.
    growth = max(1.0, equity / starting_cash) if score >= 4 else 1.0
    target = int(math.floor(base * growth + 1e-9))
    drawdown = max(0.0, 1.0 - equity / peak)
    if drawdown >= .04:
        target = 1
    elif drawdown >= .02:
        target = min(target, base)

    # Hypothetical profit vault: reserve 25% of accumulated gains from the
    # exposure calculation. Never reinvest unsettled option sale proceeds.
    protected = .25 * max(0.0, equity - starting_cash)
    riskable_equity = max(0.0, equity - protected)
    # Each contract can lose its entire premium even if the stop is 35%.
    premium_budget = min(settled, equity * exposure, riskable_equity * exposure)
    affordable = int((premium_budget + 1e-8) // debit)
    return max(0, min(target, int(max_contracts), int(bid_size),
                      int(ask_size), affordable))


def unrestricted_research_quantity(*, settled: float, debit: float) -> int:
    """Research stress arm ONLY: spend all settled cash. Never use for live."""
    if not (math.isfinite(settled) and math.isfinite(debit)) or debit <= 0:
        return 0
    return max(0, int((settled + 1e-8) // debit))
