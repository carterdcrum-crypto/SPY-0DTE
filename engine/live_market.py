"""Production quotes in an isolated, unshifted live clock. No paper feed fallback."""
from __future__ import annotations

import math
import re
import os
import logging
from pathlib import Path
from collections import deque
from dataclasses import replace
from datetime import date, datetime, timezone

from .paper_autotrader import MarketCycle, QuoteRow
from .providers.webull_free import WebullFreeDataProvider, _nearest_contracts, _first
from .webull import PRODUCTION_API_HOST


def parse_occ_symbol(symbol: str) -> tuple[str, str, str, float]:
    match = re.fullmatch(r"(SPY)(\d{6})([CP])(\d{8})", symbol.strip().upper())
    if match is None:
        raise ValueError("a standard SPY OCC option symbol is required")
    underlying, expiry, right, strike = match.groups()
    expiration = date(2000+int(expiry[:2]), int(expiry[2:4]), int(expiry[4:])).isoformat()
    return underlying, expiration, "CALL" if right == "C" else "PUT", int(strike)/1000


def position_symbol(row: dict) -> str:
    symbol = str(row.get("symbol", ""))
    if re.fullmatch(r"SPY\d{6}[CP]\d{8}", symbol):
        parse_occ_symbol(symbol)
        return symbol
    if symbol != "SPY":
        raise ValueError("unmanaged broker instrument")
    legs = row.get("legs")
    if not isinstance(legs, list) or len(legs) != 1:
        raise ValueError("only a single standard option leg is supported")
    leg = legs[0]
    if str(leg.get("symbol")) != "SPY" or leg.get("option_type") not in {"CALL", "PUT"}:
        raise ValueError("unmanaged option leg")
    for key in ("option_contract_multiplier", "option_contract_deliverable"):
        if str(leg.get(key, "100")) not in {"100", "100.0"}:
            raise ValueError("nonstandard option contract")
    strike = float(leg["option_exercise_price"])
    if not math.isfinite(strike) or strike <= 0:
        raise ValueError("invalid option strike")
    expiry = date.fromisoformat(leg["option_expire_date"])
    return f"SPY{expiry:%y%m%d}{leg['option_type'][0]}{round(strike*1000):08d}"


class LiveDataProvider(WebullFreeDataProvider):
    def __init__(self, app_key: str, app_secret: str):
        from webull.core.client import ApiClient
        from webull.data.data_client import DataClient
        client = ApiClient(app_key, app_secret, "us", connect_timeout=3, timeout=8,
                           auto_retry=False, token_check_duration_seconds=10, token_check_interval_seconds=2)
        client.set_stream_logger(log_level=logging.CRITICAL)
        client.add_endpoint("us", PRODUCTION_API_HOST)
        token_dir = os.environ.get("WEBULL_OPENAPI_TOKEN_DIR", "/data/webull-openapi")
        Path(token_dir).mkdir(parents=True, exist_ok=True)
        client.set_token_dir(token_dir)
        super().__init__(data_client=DataClient(client), active_contract_limit=20, contract_refresh_seconds=60)
        self.pinned_symbols: set[str] = set()

    def select_contracts(self, contracts, spot):
        # Held contracts retain a quote slot even if they move away from the money.
        pinned = tuple(row for row in contracts if str(_first(row, "symbol", "option_symbol", "optionSymbol")) in self.pinned_symbols)
        others = tuple(row for row in contracts if row not in pinned)
        return (*pinned, *_nearest_contracts(others, spot=spot, limit=max(0, 20-len(pinned))))

    def collect_once(self, *args, **kwargs):
        return tuple(replace(s, source="webull_production") for s in super().collect_once(*args, **kwargs))


class LiveMarketReader:
    def __init__(self):
        self.cycles = deque(maxlen=90)
        self.quotes: dict = {}

    def ingest(self, snapshots, now: datetime):
        valid = {}
        for s in snapshots:
            try:
                _, expiry, right, strike = parse_occ_symbol(s.option_symbol)
                numbers = (s.bid, s.ask, s.strike, s.underlying_price, s.feed_delay_seconds)
                if not all(math.isfinite(float(v)) for v in numbers):
                    continue
                if s.source != "webull_production" or s.timestamp_quality != "option_quote_timestamp":
                    continue
                if s.underlying_timestamp is None or s.expiration.isoformat() != expiry or s.strike != strike or s.right != right.lower():
                    continue
                if s.bid < 0 or s.ask <= 0 or s.ask < s.bid or s.underlying_price <= 0:
                    continue
                if any(not -0.25 <= (now-t).total_seconds() <= 3 for t in (s.timestamp, s.received_at, s.underlying_timestamp)):
                    continue
                valid[s.option_symbol] = s
            except (TypeError, ValueError, OverflowError):
                continue
        self.quotes = valid  # Never retain a previous quote when this collection fails.
        rows = tuple(QuoteRow(
            cycle=s.received_at, symbol=s.option_symbol, expiration=s.expiration.isoformat(),
            right=s.right, strike=s.strike, bid=s.bid, ask=s.ask,
            underlying_price=s.underlying_price, volume=s.volume, open_interest=s.open_interest,
            delta=None if s.greeks is None else s.greeks.delta,
            feed_delay_seconds=max(0.0, (now-s.timestamp).total_seconds()),
        ) for s in valid.values())
        if rows:
            received = min(row.cycle for row in rows)
            if not self.cycles or received > self.cycles[0].received_at:
                self.cycles.appendleft(MarketCycle(received, rows))

    def latest_cycles(self, limit=2):
        if not self.cycles:
            return ()
        day = self.cycles[0].rows[0].expiration
        return tuple(c for c in self.cycles if c.rows[0].expiration == day)[:limit]

    def latest_quote(self, symbol):
        if symbol not in self.quotes or not self.cycles:
            return None
        return next((q for q in self.cycles[0].rows if q.symbol == symbol), None)

    def fresh_quote(self, symbol: str, now: datetime):
        snapshot = self.quotes.get(symbol)
        if snapshot is None or any(not -0.25 <= (now-t).total_seconds() <= 3
                                   for t in (snapshot.timestamp, snapshot.received_at, snapshot.underlying_timestamp)):
            return None
        return self.latest_quote(symbol)
