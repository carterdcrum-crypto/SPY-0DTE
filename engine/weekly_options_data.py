"""Strict observed-time inputs for executable SPY 0DTE research.

Expected per-session CSV or CSV.GZ:
  spy_bars_YYYY-MM-DD.csv[.gz]:
    ts_start,open,high,low,close,volume
  spy_option_quotes_YYYY-MM-DD.csv[.gz]:
    observed_at,symbol,expiration,right,strike,bid,ask,delta,bid_size,ask_size,volume,open_interest,schema

ts_start is the START of a completed one-minute bar, not its publication
time. The strategy may read it only at ts_start + 60 seconds.
observed_at is the quote's *receive/availability* timestamp, not the
minute-bucket start; cbbo-1m minute bucket timestamps are not accepted as
actual quote observation times. These two feeds must be independently sourced.
"""
from __future__ import annotations

import csv
import gzip
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, time, timezone
from pathlib import Path
from typing import Iterator
from zoneinfo import ZoneInfo

from .session_calendar import session_close

NY = ZoneInfo("America/New_York")
OCC = re.compile(r"^SPY(\d{6})([CP])(\d{8})$")
ACCEPTED_SCHEMAS = frozenset({"cmbp-1", "cbbo-1s", "tcbbo", "nbbo-event", "test-fixture"})


def aware(raw: str) -> datetime:
    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("timestamps must include timezone/UTC offset")
    return dt.astimezone(timezone.utc)


@dataclass(frozen=True)
class Bar:
    start: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int

    @property
    def available_at(self) -> datetime:
        return self.start + timedelta(minutes=1)

    @property
    def typical(self) -> float:
        return (self.high + self.low + self.close) / 3

    def __post_init__(self) -> None:
        if self.start.tzinfo is None:
            raise ValueError("bar start must be timezone aware")
        if not (math.isfinite(self.open) and math.isfinite(self.high)
                and math.isfinite(self.low) and math.isfinite(self.close)):
            raise ValueError("invalid OHLC")
        if min(self.open, self.low, self.close) <= 0 or not self.low <= min(self.open, self.close) <= max(self.open, self.close) <= self.high:
            raise ValueError("inconsistent OHLC")
        if self.volume < 0:
            raise ValueError("negative volume")


@dataclass(frozen=True)
class Quote:
    observed_at: datetime
    symbol: str
    expiry: date
    right: str
    strike: float
    bid: float
    ask: float
    delta: float
    bid_size: int
    ask_size: int
    volume: int
    open_interest: int

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None:
            raise ValueError("quote time must include timezone")
        symbol = self.symbol.replace(" ", "")
        m = OCC.fullmatch(symbol)
        if not m:
            raise ValueError(f"not an actual SPY OCC symbol: {self.symbol}")
        parsed_expiry = datetime.strptime(m.group(1), "%y%m%d").date()
        parsed_right = "call" if m.group(2) == "C" else "put"
        parsed_strike = int(m.group(3)) / 1000
        if parsed_expiry != self.expiry or parsed_right != self.right or abs(parsed_strike - self.strike) > 1e-7:
            raise ValueError("OCC symbol / expiration / right / strike mismatch")
        if not all(map(math.isfinite, (self.strike, self.bid, self.ask, self.delta))):
            raise ValueError("nonfinite option quote")
        if self.bid < 0 or self.ask < self.bid or self.ask <= 0 or abs(self.delta) > 1:
            raise ValueError("invalid option market")
        if min(self.bid_size, self.ask_size, self.volume, self.open_interest) < 0:
            raise ValueError("negative market liquidity")


@dataclass(frozen=True)
class SessionData:
    day: date
    bars: tuple[Bar, ...]
    quotes: tuple[Quote, ...]
    close_at: datetime

    def __post_init__(self) -> None:
        if any(b.start.astimezone(NY).date() != self.day for b in self.bars):
            raise ValueError("bar date mismatch")
        if any(q.observed_at.astimezone(NY).date() != self.day or q.expiry != self.day for q in self.quotes):
            raise ValueError("quote session or 0DTE expiry mismatch")
        if any(a.start >= b.start for a, b in zip(self.bars,self.bars[1:])):
            raise ValueError("bars not strictly chronological")
        if any(a.observed_at > b.observed_at for a,b in zip(self.quotes,self.quotes[1:])):
            raise ValueError("quotes not chronological")


def _open_file(p: Path):
    return gzip.open(p, "rt", newline="") if p.suffix == ".gz" else p.open("r", newline="")


def _get_file(root: Path, stem: str) -> Path | None:
    for suffix in (".csv.gz", ".csv"):
        candidate = root / (stem + suffix)
        if candidate.is_file():
            return candidate
    return None


def load_session(root: str | Path, day: date) -> SessionData:
    root = Path(root)
    close = session_close(day)
    if close is None:
        raise ValueError(f"{day} not a verified open exchange session")
    raw_bars = _get_file(root, f"spy_bars_{day}")
    raw_quotes = _get_file(root, f"spy_option_quotes_{day}")
    if raw_bars is None or raw_quotes is None:
        missing = ("spy_bars" if raw_bars is None else "") + (" spy_option_quotes" if raw_quotes is None else "")
        raise FileNotFoundError(f"{day}: missing required real timestamped inputs: {missing.strip()}")
    session_open = datetime.combine(day,time(9,30),NY)
    session_end = datetime.combine(day,close,NY)
    bars: list[Bar] = []
    with _open_file(raw_bars) as f:
        reader = csv.DictReader(f)
        required = {"ts_start", "open", "high", "low", "close", "volume"}
        if not required.issubset(reader.fieldnames or ()):
            raise ValueError(f"{raw_bars.name}: missing real OHLCV columns {required - set(reader.fieldnames or ())}")
        for row in reader:
            b = Bar(aware(row["ts_start"]),float(row["open"]),float(row["high"]),
                    float(row["low"]),float(row["close"]),int(row["volume"]))
            if session_open <= b.start.astimezone(NY) and b.available_at.astimezone(NY) <= session_end:
                bars.append(b)
    quotes: list[Quote] = []
    with _open_file(raw_quotes) as f:
        reader = csv.DictReader(f)
        required = {"observed_at","symbol","expiration","right","strike","bid","ask","delta",
                    "bid_size","ask_size","volume","open_interest","schema"}
        if not required.issubset(reader.fieldnames or ()):
            raise ValueError(f"{raw_quotes.name}: missing quote fields {required - set(reader.fieldnames or ())}")
        for row in reader:
            if row["schema"].strip().lower() not in ACCEPTED_SCHEMAS:
                raise ValueError("only event-observed real quotes accepted; CBBO-1m timestamps mark minute buckets, not quotes")
            expiry = date.fromisoformat(row["expiration"])
            # Other expirations genuinely existed, but must not be selected
            # for a same-day strategy; reject any rows that falsely claim 0DTE.
            if expiry != day:
                continue
            q = Quote(
                aware(row["observed_at"]),row["symbol"].replace(" ",""),expiry,
                row["right"].strip().lower(),float(row["strike"]),float(row["bid"]),
                float(row["ask"]),float(row["delta"]),int(row["bid_size"]),
                int(row["ask_size"]),int(row["volume"]),int(row["open_interest"]),
            )
            if session_open <= q.observed_at.astimezone(NY) <= session_end:
                quotes.append(q)
    return SessionData(day,tuple(sorted(bars,key=lambda b:b.start)),
                       tuple(sorted(quotes,key=lambda q:(q.observed_at,q.symbol))),
                       session_end.astimezone(timezone.utc))


def available_sessions(root: str | Path) -> tuple[date, ...]:
    root = Path(root)
    bars = {date.fromisoformat(p.name[9:19]) for p in root.glob("spy_bars_*.csv*")}
    quotes = {date.fromisoformat(p.name[18:28]) for p in root.glob("spy_option_quotes_*.csv*")}
    return tuple(sorted(bars & quotes))


def audit(root: str | Path) -> dict:
    root = Path(root)
    bars = list(root.glob("spy_bars_*.csv*"))
    quotes = list(root.glob("spy_option_quotes_*.csv*"))
    legacy = list(root.glob("spy_0dte_*.csv*"))
    days = available_sessions(root)
    return {
        "source":str(root),"true_ohlcv_files":len(bars),
        "timestamped_option_quote_files":len(quotes),
        "paired_sessions":len(days),"legacy_minute_snapshot_files":len(legacy),
        "usable_for_this_experiment":bool(days),
        "blocker":None if days else (
            "Missing real SPY 1-minute OHLCV bars AND/OR event-observed SPY "
            "0DTE OCC option quotes with executable bid/ask and timestamps. "
            "Legacy spy_0dte minute close/sampled CBBO files omit full OHLCV "
            "and exact quote-observation timestamps; cannot backtest ORB/VWAP honestly."
        ),
    }
