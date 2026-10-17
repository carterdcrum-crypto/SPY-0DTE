"""Read one authoritative collector option-quote cycle for LIVE decisions.

No second Webull options snapshot poller: the collector owns production REST
quoting while live execution consumes the same persisted, timestamped feed.
Rows must be native production option quotes; never synthesize timestamps.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .greeks import ModelGreeks
from .providers.webull_free import FreeOptionSnapshot


def collector_live_quotes(path: str | Path, now: datetime, *, max_age_seconds: float = 3.0):
    """Return only the most recent *complete* fresh production collector cycle."""
    if now.tzinfo is None:
        raise ValueError("current timestamp must be timezone-aware")
    if not Path(path).is_file():
        return ()
    try:
        with sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True, timeout=1.0) as con:
            con.row_factory = sqlite3.Row
            latest = con.execute(
                """SELECT received_at FROM option_snapshots
                   WHERE received_at IS NOT NULL
                   ORDER BY received_at DESC LIMIT 1"""
            ).fetchone()
            if latest is None:
                return ()
            received = datetime.fromisoformat(str(latest["received_at"]))
            if received.tzinfo is None or not -0.25 <= (now - received).total_seconds() <= max_age_seconds:
                return ()
            rows = con.execute(
                """SELECT * FROM option_snapshots
                   WHERE received_at = ? AND source = 'webull_production'
                     AND timestamp_quality = 'option_quote_timestamp'""",
                (latest["received_at"],),
            ).fetchall()
    except (sqlite3.Error, ValueError, OSError):
        return ()

    quotes = []
    for row in rows:
        try:
            market_ts = datetime.fromisoformat(str(row["timestamp"]))
            underlying_raw = row["underlying_timestamp"]
            if not underlying_raw:
                continue
            underlying_ts = datetime.fromisoformat(str(underlying_raw))
            if any(ts.tzinfo is None or not -0.25 <=
                   (now-ts).total_seconds() <= max_age_seconds
                   for ts in (market_ts, received, underlying_ts)):
                continue
            if not (row["bid"] >= 0 and row["ask"] > 0 and row["ask"] >= row["bid"]):
                continue
            greek = None
            if all(row[k] is not None for k in ("implied_volatility", "delta", "gamma", "theta", "vega")):
                greek = ModelGreeks(
                    implied_volatility=float(row["implied_volatility"]),
                    delta=float(row["delta"]),
                    gamma=float(row["gamma"]),
                    theta_per_day=float(row["theta"]),
                    vega_per_vol_point=float(row["vega"]),
                    model=str(row["greek_model"] or "black_scholes_approximation"),
                )
            quotes.append(FreeOptionSnapshot(
                timestamp=market_ts, received_at=received,
                timestamp_quality=str(row["timestamp_quality"]),
                feed_delay_seconds=float(row["feed_delay_seconds"] or 0),
                option_symbol=str(row["option_symbol"]),
                expiration=datetime.fromisoformat(str(row["expiration"])).date(),
                right=str(row["right"]), strike=float(row["strike"]),
                bid=float(row["bid"]), ask=float(row["ask"]),
                bid_size=int(row["bid_size"]), ask_size=int(row["ask_size"]),
                underlying_price=float(row["underlying_price"]),
                minutes_to_expiry=float(row["minutes_to_expiry"]),
                volume=int(row["volume"]), open_interest=int(row["open_interest"]),
                greeks=greek, source="webull_production",
                underlying_timestamp=underlying_ts,
            ))
        except (ValueError, TypeError, OverflowError, KeyError):
            continue
    return tuple(quotes)
