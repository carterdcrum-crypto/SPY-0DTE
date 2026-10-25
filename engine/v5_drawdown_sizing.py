"""Durable as-of EOD-equity risk throttle shared by V5 PAPER and LIVE.

Mirrors the archived 'full_cash_with_dd_throttle' fractional tiers. Trading
can use at most settled buying power and current displayed option depth.
The research model used true historical session-close equity; operational
storage records the last observed *flat* account equity per session instead
and never claims an exact exchange-close account mark.

Never reset history on deployment. Only the paper-account reset clears its
own separate simulated-account history.
"""
from __future__ import annotations

import math
import sqlite3
from datetime import date
from pathlib import Path


def drawdown_fraction(opening_equity: float, prior_peak: float) -> float:
    """Research policy: 100/70/40/15% of opening equity at 2/5/10% DD."""
    if not all(math.isfinite(float(x)) for x in (opening_equity, prior_peak)):
        raise ValueError("nonfinite risk equity")
    if opening_equity <= 0 or prior_peak <= 0:
        raise ValueError("risk equity must be positive")
    decline = max(0., 1 - opening_equity / prior_peak)
    if decline < .02:
        return 1.0
    if decline < .05:
        return .70
    if decline < .10:
        return .40
    return .15


def observe_flat_session(path: str | Path, session: date, equity: float) -> dict:
    """Persist session opening and last flat equity, returning causal budget.

    Called only when the account has no open position. A new session's peak is
    computed from *prior* observed flat equity, never from future observations
    or today's unrealized position marks. Holding premium is never counted as
    spendable cash. SQLite is on the service's persistent /data volume.
    """
    if not isinstance(session, date) or not math.isfinite(float(equity)) or equity <= 0:
        raise ValueError("valid session and flat equity required")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(path), timeout=10) as db:
        db.execute("PRAGMA busy_timeout=10000")
        db.execute("BEGIN IMMEDIATE")
        db.execute("""
            CREATE TABLE IF NOT EXISTS v5_equity_risk_days (
                trading_date TEXT PRIMARY KEY,
                opening_equity REAL NOT NULL CHECK(opening_equity>0),
                last_flat_equity REAL NOT NULL CHECK(last_flat_equity>0)
            )
        """)
        day = session.isoformat()
        db.execute("""
            INSERT OR IGNORE INTO v5_equity_risk_days
            (trading_date,opening_equity,last_flat_equity)
            VALUES (?,?,?)
        """, (day, float(equity), float(equity)))
        db.execute("""
            UPDATE v5_equity_risk_days SET last_flat_equity=?
            WHERE trading_date=?
        """, (float(equity), day))
        row = db.execute("""
            SELECT opening_equity FROM v5_equity_risk_days WHERE trading_date=?
        """, (day,)).fetchone()
        historical = db.execute("""
            SELECT MAX(last_flat_equity) FROM v5_equity_risk_days
            WHERE trading_date < ?
        """, (day,)).fetchone()[0]
    opening = float(row[0])
    peak = max(opening, float(historical) if historical is not None else opening)
    fraction = drawdown_fraction(opening, peak)
    return {
        "opening_equity": round(opening, 4),
        "prior_peak_equity": round(peak, 4),
        "fraction": fraction,
        "drawdown_pct": round(100*max(0., 1-opening/peak), 4),
        "policy": "V5_FULL_CASH_EOD_DRAWDOWN_THROTTLE",
        "valuation_method": "prior_session_last_observed_flat_equity",
    }
