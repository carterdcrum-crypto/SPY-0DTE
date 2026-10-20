"""One-shot, owner-authorized low-debit live broker round-trip diagnostic.

This is NOT the V5 quantitative signal, and is intentionally restricted to
one single-leg SPY option buy on the authorized date. It does not bypass
the owner's existing cash-account LIVE switch or live execution permission.
Only durable order journal fills are considered evidence of execution.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass
from datetime import date, datetime, time

from .broker import OptionOrderRequest
from .live_market import parse_occ_symbol
from .session_calendar import EASTERN

TAG = "owner_authorized_one_shot_exit_test"
FEE_CUSHION_USD = 5.0
MAX_PREMIUM_USD = 20.0


@dataclass(frozen=True)
class TestConfig:
    enabled: bool
    session: date | None
    maximum_total_usd: float = 25.0

    @classmethod
    def from_env(cls):
        enabled = os.environ.get("LIVE_ONE_SHOT_TEST", "").strip().lower() == "true"
        session_str = os.environ.get("LIVE_ONE_SHOT_TEST_DATE", "").strip()
        if not enabled:
            return cls(False, None)
        if not session_str:
            raise ValueError("one-shot test date is required")
        session = date.fromisoformat(session_str)
        limit = float(os.environ.get("LIVE_ONE_SHOT_TEST_MAX_TOTAL_USD", "25"))
        # This one authorized diagnostic may never spend more than $25 in
        # planned premium/fee reserve, even if an environment value is wrong.
        if not math.isfinite(limit) or limit <= FEE_CUSHION_USD or limit > 25:
            raise ValueError("one-shot test budget must be above reserve and <= $25")
        return cls(True, session, limit)

    def timing(self, now: datetime) -> str | None:
        eastern = now.astimezone(EASTERN)
        if self.session is None or eastern.date() < self.session:
            return "ONE_SHOT_WAITING_SESSION"
        if eastern.date() > self.session:
            return "ONE_SHOT_EXPIRED_NO_MORE_BUYS"
        # Keep away from open volatility, session close and expiry.
        if not time(9, 55) <= eastern.time() < time(15, 30):
            return "ONE_SHOT_WAITING_SAFE_WINDOW"
        return None


def used_before(orders: list[dict]) -> bool:
    # The durable LIVE journal is the source of truth. UNKNOWN submissions
    # count as USED; no retrying an ambiguous POST under a new client ID.
    return any(
        order.get("request", {}).get("side") == "BUY"
        and str(order.get("reason") or "").startswith(TAG)
        for order in orders
    )


def eligible_option(quotes: dict, now: datetime, account_cash: float,
                    config: TestConfig):
    """Pick one genuinely executable, already-collected option quote.

    No second market-data API stream; never synthesize quote size/timestamps.
    Low-priced and narrow-spread quotes may not exist in the near-money feed.
    In that case it is safer to make NO order than manufacture liquidity.
    """
    options = []
    maximum = min(config.maximum_total_usd, account_cash)
    cap = min(MAX_PREMIUM_USD, maximum - FEE_CUSHION_USD)
    if cap <= 0:
        return None
    for q in quotes.values():
        try:
            underlying, expiry, right, strike = parse_occ_symbol(q.option_symbol)
            if underlying != "SPY" or expiry != now.astimezone(EASTERN).date().isoformat():
                continue
            if q.source != "webull_production" or q.timestamp_quality != "option_quote_timestamp":
                continue
            if any(t is None or t.tzinfo is None or not -0.25 <= (now - t).total_seconds() <= 3
                   for t in (q.timestamp, q.received_at, q.underlying_timestamp)):
                continue
            bid, ask = float(q.bid), float(q.ask)
            if not all(math.isfinite(v) for v in (bid, ask, strike)):
                continue
            if bid < .05 or ask <= bid or ask > cap / 100:
                continue
            if int(q.bid_size) < 5 or int(q.ask_size) < 5:
                continue
            if int(q.volume) < 100 or int(q.open_interest) < 100:
                continue
            spread = (ask - bid) / ((ask + bid) / 2)
            if spread > .10:  # 1-cent ticks alone exceed V5's 3% spread at $0.20.
                continue
            premium = math.ceil(ask * 100 - 1e-9) / 100
            if 100 * premium + FEE_CUSHION_USD > maximum + 1e-9:
                continue
            # Liquidity first: narrow spread, then depth/volume, then price.
            options.append((spread, -min(q.bid_size, q.ask_size),
                            -q.volume, -premium, q.option_symbol,
                            right, strike, premium))
        except (ValueError, TypeError, OverflowError, ZeroDivisionError):
            continue
    if not options:
        return None
    _, _, _, _, symbol, right, strike, price = min(options)
    return {"symbol": symbol, "right": right, "strike": strike, "price": price}


def broker_preview_permits(payload: dict) -> bool:
    """Fail closed on explicit broker rejections or empty/malformed previews."""
    if not isinstance(payload, dict) or not payload:
        return False
    if any(payload.get(k) is False for k in ("success", "result", "approved", "is_passed")):
        return False
    if payload.get("error_code") or payload.get("errors"):
        return False
    if str(payload.get("status", "")).lower() in ("rejected", "failed", "error", "denied"):
        return False
    return True
