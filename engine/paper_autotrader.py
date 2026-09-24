from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, time as dt_time, timezone
from pathlib import Path
from typing import Callable, Iterable
from zoneinfo import ZoneInfo

from .paper_account import PaperAccountStore

log = logging.getLogger("spy0dte.paper")
EASTERN = ZoneInfo("America/New_York")
STRATEGY_NAME = "bootstrap_momentum_v1"


@dataclass(frozen=True)
class PaperAutoSettings:
    tick_seconds: float = 1.0
    max_data_age_seconds: float = 10.0
    minimum_spot_move_fraction: float = 0.0005
    minimum_premium_move_fraction: float = 0.02
    minimum_edge_proxy: float = 0.01
    minimum_liquidity_score: float = 0.60
    maximum_spread_fraction: float = 0.08
    maximum_moneyness_fraction: float = 0.025
    maximum_position_fraction: float = 0.90
    maximum_contracts: int = 1
    stop_loss_fraction: float = 0.35
    take_profit_fraction: float = 0.50
    max_hold_seconds: int = 600
    cooldown_seconds: int = 180
    force_exit_minutes_before_close: float = 10.0
    fee_per_contract: float = 0.65
    market_open: dt_time = dt_time(9, 45)
    market_close: dt_time = dt_time(16, 0)

    @classmethod
    def from_env(cls) -> "PaperAutoSettings":
        return cls(
            tick_seconds=_env_float("PAPER_ENGINE_TICK_SECONDS", 1.0, minimum=0.25),
            max_data_age_seconds=_env_float("PAPER_MAX_DATA_AGE_SECONDS", 10.0, minimum=1.0),
            minimum_spot_move_fraction=_env_float(
                "PAPER_MIN_SPOT_MOVE_FRACTION", 0.0005, minimum=0.0
            ),
            minimum_premium_move_fraction=_env_float(
                "PAPER_MIN_PREMIUM_MOVE_FRACTION", 0.02, minimum=0.0
            ),
            minimum_edge_proxy=_env_float("PAPER_MIN_EDGE_PROXY", 0.01, minimum=0.0),
            minimum_liquidity_score=_env_float(
                "PAPER_MIN_LIQUIDITY_SCORE", 0.60, minimum=0.0, maximum=1.0
            ),
            maximum_spread_fraction=_env_float(
                "PAPER_MAX_SPREAD_FRACTION", 0.08, minimum=0.001, maximum=1.0
            ),
            maximum_moneyness_fraction=_env_float(
                "PAPER_MAX_MONEYNESS_FRACTION", 0.025, minimum=0.001, maximum=0.25
            ),
            maximum_position_fraction=_env_float(
                "PAPER_MAX_POSITION_FRACTION", 0.90, minimum=0.01, maximum=1.0
            ),
            maximum_contracts=_env_int("PAPER_MAX_CONTRACTS", 1, minimum=1, maximum=10),
            stop_loss_fraction=_env_float(
                "PAPER_STOP_LOSS_FRACTION", 0.35, minimum=0.01, maximum=0.99
            ),
            take_profit_fraction=_env_float(
                "PAPER_TAKE_PROFIT_FRACTION", 0.50, minimum=0.01, maximum=10.0
            ),
            max_hold_seconds=_env_int("PAPER_MAX_HOLD_SECONDS", 600, minimum=30),
            cooldown_seconds=_env_int("PAPER_COOLDOWN_SECONDS", 180, minimum=0),
            force_exit_minutes_before_close=_env_float(
                "PAPER_FORCE_EXIT_MINUTES_BEFORE_CLOSE", 10.0, minimum=1.0, maximum=120.0
            ),
            fee_per_contract=_env_float("PAPER_FEE_PER_CONTRACT", 0.65, minimum=0.0),
            market_open=_env_time("PAPER_MARKET_OPEN_ET", "09:45"),
            market_close=_env_time("PAPER_MARKET_CLOSE_ET", "16:00"),
        )


@dataclass(frozen=True)
class QuoteRow:
    cycle: datetime
    symbol: str
    expiration: str
    right: str
    strike: float
    bid: float
    ask: float
    underlying_price: float
    volume: int
    open_interest: int
    delta: float | None
    feed_delay_seconds: float | None

    @property
    def mid(self) -> float:
        return max(0.0, (self.bid + self.ask) / 2.0)

    @property
    def spread_fraction(self) -> float:
        mid = self.mid
        return 1.0 if mid <= 0 else max(0.0, self.ask - self.bid) / mid

    @property
    def liquidity_score(self) -> float:
        spread_component = max(0.0, min(1.0, 1.0 - self.spread_fraction / 0.20))
        volume_component = min(1.0, max(0, self.volume) / 500.0)
        oi_component = min(1.0, max(0, self.open_interest) / 2000.0)
        return 0.55 * spread_component + 0.25 * volume_component + 0.20 * oi_component


@dataclass(frozen=True)
class MarketCycle:
    received_at: datetime
    rows: tuple[QuoteRow, ...]

    @property
    def spot(self) -> float:
        return self.rows[0].underlying_price if self.rows else 0.0


@dataclass(frozen=True)
class PaperSignal:
    symbol: str
    right: str
    spot: float
    spot_move_fraction: float
    premium_move_fraction: float
    edge_proxy: float
    liquidity_score: float
    spread_fraction: float
    ask: float
    bid: float
    contract_cost: float
    reason: str

    def as_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "right": self.right,
            "spot": self.spot,
            "spot_move_fraction": self.spot_move_fraction,
            "premium_move_fraction": self.premium_move_fraction,
            "edge_proxy": self.edge_proxy,
            "liquidity_score": self.liquidity_score,
            "spread_fraction": self.spread_fraction,
            "ask": self.ask,
            "bid": self.bid,
            "contract_cost": self.contract_cost,
            "reason": self.reason,
        }


_STATUS_LOCK = threading.Lock()
_STATUS: dict[str, object] = {
    "enabled": False,
    "state": "STOPPED",
    "reason": "paper automation has not started",
    "strategy": STRATEGY_NAME,
    "last_tick": None,
    "last_signal": None,
    "last_action": None,
}


def automation_status() -> dict[str, object]:
    with _STATUS_LOCK:
        return dict(_STATUS)


def _publish(**values: object) -> None:
    with _STATUS_LOCK:
        _STATUS.update(values)


def _env_float(
    name: str,
    default: float,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    value = float(os.environ.get(name, str(default)))
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} must be <= {maximum}")
    return value


def _env_int(
    name: str,
    default: int,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    value = int(os.environ.get(name, str(default)))
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} must be <= {maximum}")
    return value


def _env_time(name: str, default: str) -> dt_time:
    text = os.environ.get(name, default).strip()
    hour_text, minute_text = text.split(":", 1)
    return dt_time(int(hour_text), int(minute_text))


def _parse_datetime(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _paper_db_path() -> str:
    explicit = os.environ.get("PAPER_DB_PATH", "").strip()
    if explicit:
        return explicit
    control_path = os.environ.get("CONTROL_DB_PATH", "").strip()
    if control_path:
        return str(Path(control_path).with_name("spy_paper.sqlite"))
    return "/data/spy_paper.sqlite"


def _paper_starting_cash() -> float:
    value = float(os.environ.get("PAPER_STARTING_CASH", "1000"))
    if value <= 0:
        raise ValueError("PAPER_STARTING_CASH must be positive")
    return value


class SnapshotReader:
    """Read complete collector cycles without coupling execution to the collector."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=2.0)
        connection.row_factory = sqlite3.Row
        return connection

    def latest_cycles(self, limit: int = 2) -> tuple[MarketCycle, ...]:
        if not Path(self.path).exists():
            return ()
        try:
            with self._connect() as connection:
                cycles = connection.execute(
                    """
                    SELECT COALESCE(received_at, timestamp) AS cycle
                    FROM option_snapshots
                    GROUP BY COALESCE(received_at, timestamp)
                    ORDER BY cycle DESC
                    LIMIT ?
                    """,
                    (max(1, int(limit)),),
                ).fetchall()
                out: list[MarketCycle] = []
                for cycle_row in cycles:
                    cycle_text = str(cycle_row["cycle"])
                    rows = connection.execute(
                        """
                        SELECT COALESCE(received_at, timestamp) AS cycle,
                               option_symbol, expiration, right, strike, bid, ask,
                               underlying_price, volume, open_interest, delta,
                               feed_delay_seconds
                        FROM option_snapshots
                        WHERE COALESCE(received_at, timestamp) = ?
                        ORDER BY strike, right, option_symbol
                        """,
                        (cycle_text,),
                    ).fetchall()
                    normalized = tuple(self._row(row) for row in rows)
                    if normalized:
                        out.append(MarketCycle(_parse_datetime(cycle_text), normalized))
                return tuple(out)
        except sqlite3.Error:
            log.exception("failed reading market snapshots for paper trader")
            return ()

    def latest_quote(self, symbol: str) -> QuoteRow | None:
        if not Path(self.path).exists():
            return None
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT COALESCE(received_at, timestamp) AS cycle,
                           option_symbol, expiration, right, strike, bid, ask,
                           underlying_price, volume, open_interest, delta,
                           feed_delay_seconds
                    FROM option_snapshots
                    WHERE option_symbol = ?
                    ORDER BY COALESCE(received_at, timestamp) DESC
                    LIMIT 1
                    """,
                    (symbol,),
                ).fetchone()
            return None if row is None else self._row(row)
        except sqlite3.Error:
            log.exception("failed reading active paper quote")
            return None

    @staticmethod
    def _row(row: sqlite3.Row) -> QuoteRow:
        right = str(row["right"]).strip().lower()
        if right in {"c", "call"}:
            right = "call"
        elif right in {"p", "put"}:
            right = "put"
        return QuoteRow(
            cycle=_parse_datetime(str(row["cycle"])),
            symbol=str(row["option_symbol"]),
            expiration=str(row["expiration"]),
            right=right,
            strike=float(row["strike"]),
            bid=float(row["bid"]),
            ask=float(row["ask"]),
            underlying_price=float(row["underlying_price"]),
            volume=int(row["volume"] or 0),
            open_interest=int(row["open_interest"] or 0),
            delta=None if row["delta"] is None else float(row["delta"]),
            feed_delay_seconds=None
            if row["feed_delay_seconds"] is None
            else float(row["feed_delay_seconds"]),
        )


class PaperAutoTrader:
    """Paper-only orchestration layer.

    The current strategy is intentionally named a bootstrap strategy: it exists
    to exercise the full autonomous paper execution path using observable price
    continuation, while the research forecasting stack is still being wired into
    a production signal provider. It cannot place broker orders.
    """

    def __init__(
        self,
        *,
        mode_getter: Callable[[], str],
        market_reader: SnapshotReader,
        account_store: PaperAccountStore,
        settings: PaperAutoSettings,
    ) -> None:
        self.mode_getter = mode_getter
        self.market_reader = market_reader
        self.account_store = account_store
        self.settings = settings

    def tick(self, now: datetime | None = None) -> dict[str, object]:
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        eastern = current.astimezone(EASTERN)
        mode = self.mode_getter().strip().upper()
        released = self.account_store.settle_due(eastern.date())
        positions = self.account_store.positions()

        if positions:
            result = self._manage_position(positions[0], current, mode)
            result["settled_released"] = released
            return self._finish(current, result)

        if mode not in {"PAPER", "SHADOW"}:
            return self._finish(
                current,
                {
                    "state": "IDLE",
                    "reason": f"paper automation idle while mode={mode}",
                    "mode": mode,
                    "last_signal": None,
                    "last_action": None,
                    "settled_released": released,
                },
            )

        if not self._market_session(eastern):
            return self._finish(
                current,
                {
                    "state": "MARKET_CLOSED",
                    "reason": "waiting for the configured SPY 0DTE session",
                    "mode": mode,
                    "last_signal": None,
                    "last_action": None,
                    "settled_released": released,
                },
            )

        cycles = self.market_reader.latest_cycles(2)
        if len(cycles) < 2:
            return self._finish(
                current,
                {
                    "state": "WAITING_HISTORY",
                    "reason": "need two completed option-chain snapshots before evaluating a paper entry",
                    "mode": mode,
                    "last_signal": None,
                    "last_action": None,
                    "settled_released": released,
                },
            )

        latest, previous = cycles[0], cycles[1]
        age = max(0.0, (current - latest.received_at).total_seconds())
        if age > self.settings.max_data_age_seconds:
            return self._finish(
                current,
                {
                    "state": "WAITING_FRESH_DATA",
                    "reason": f"latest chain snapshot is {age:.1f}s old",
                    "mode": mode,
                    "data_age_seconds": age,
                    "last_signal": None,
                    "last_action": None,
                    "settled_released": released,
                },
            )

        account = self.account_store.snapshot()
        if account.settled_cash <= 0:
            return self._finish(
                current,
                {
                    "state": "NO_SETTLED_CASH",
                    "reason": "paper account has no settled buying power",
                    "mode": mode,
                    "last_signal": None,
                    "last_action": None,
                    "settled_released": released,
                },
            )

        signal = self._select_signal(latest, previous, account.settled_cash)
        if signal is None:
            return self._finish(
                current,
                {
                    "state": "NO_SIGNAL",
                    "reason": "no affordable liquid contract passed the bootstrap paper signal gates",
                    "mode": mode,
                    "last_signal": None,
                    "last_action": None,
                    "settled_released": released,
                },
            )

        if self._in_cooldown(current):
            return self._finish(
                current,
                {
                    "state": "COOLDOWN",
                    "reason": "recent paper execution is still inside the re-entry cooldown",
                    "mode": mode,
                    "last_signal": signal.as_dict(),
                    "last_action": None,
                    "settled_released": released,
                },
            )

        if mode == "SHADOW":
            return self._finish(
                current,
                {
                    "state": "SHADOW_SIGNAL",
                    "reason": "qualified signal observed; SHADOW mode records no simulated position",
                    "mode": mode,
                    "last_signal": signal.as_dict(),
                    "last_action": "WOULD_BUY",
                    "settled_released": released,
                },
            )

        contracts = min(
            self.settings.maximum_contracts,
            int((account.settled_cash * self.settings.maximum_position_fraction) // signal.contract_cost),
        )
        if contracts < 1:
            return self._finish(
                current,
                {
                    "state": "POSITION_TOO_EXPENSIVE",
                    "reason": "qualified contract does not fit the configured paper cash budget",
                    "mode": mode,
                    "last_signal": signal.as_dict(),
                    "last_action": None,
                    "settled_released": released,
                },
            )

        stop = max(0.0, signal.contract_cost * (1.0 - self.settings.stop_loss_fraction))
        target = signal.contract_cost * (1.0 + self.settings.take_profit_fraction)
        position = self.account_store.open_long(
            symbol=signal.symbol,
            quantity=contracts,
            fill_price=signal.contract_cost,
            opened_at=current,
            entry_spot=signal.spot,
            strategy=STRATEGY_NAME,
            stop_price=stop,
            target_price=target,
            max_hold_seconds=self.settings.max_hold_seconds,
            reason=signal.reason,
        )
        log.info(
            "paper buy symbol=%s quantity=%d cost=%.2f spot=%.2f",
            signal.symbol,
            contracts,
            signal.contract_cost,
            signal.spot,
        )
        return self._finish(
            current,
            {
                "state": "POSITION_OPENED",
                "reason": signal.reason,
                "mode": mode,
                "last_signal": signal.as_dict(),
                "last_action": "BUY",
                "position": position,
                "settled_released": released,
            },
        )

    def _manage_position(
        self,
        position: dict[str, object],
        now: datetime,
        mode: str,
    ) -> dict[str, object]:
        symbol = str(position["symbol"])
        quote = self.market_reader.latest_quote(symbol)
        if quote is None:
            return {
                "state": "POSITION_WAITING_QUOTE",
                "reason": f"waiting for a quote for open paper position {symbol}",
                "mode": mode,
                "last_signal": None,
                "last_action": None,
                "position": position,
            }

        quantity = int(position["quantity"])
        average_cost = float(position["average_cost"])
        stop_price = float(position.get("stop_price") or 0.0)
        target_price = float(position.get("target_price") or float("inf"))
        opened_text = position.get("opened_at")
        opened_at = _parse_datetime(str(opened_text)) if opened_text else now
        max_hold = int(position.get("max_hold_seconds") or self.settings.max_hold_seconds)
        held_seconds = max(0.0, (now - opened_at).total_seconds())
        exit_value = max(0.0, quote.bid * 100.0 - self.settings.fee_per_contract)
        unrealized = (exit_value - average_cost) * quantity
        minutes_to_close = self._minutes_to_close(now.astimezone(EASTERN))

        exit_reason: str | None = None
        if exit_value <= stop_price:
            exit_reason = "stop_loss"
        elif exit_value >= target_price:
            exit_reason = "take_profit"
        elif held_seconds >= max_hold:
            exit_reason = "max_hold"
        elif minutes_to_close <= self.settings.force_exit_minutes_before_close:
            exit_reason = "session_close"

        if exit_reason is None:
            return {
                "state": "POSITION_OPEN",
                "reason": "monitoring stop, target, max-hold and session-close exits",
                "mode": mode,
                "last_signal": None,
                "last_action": None,
                "position": {
                    **position,
                    "mark_value": exit_value,
                    "unrealized_pnl": unrealized,
                    "held_seconds": held_seconds,
                    "quote_received_at": quote.cycle.isoformat(),
                },
            }

        closed = self.account_store.close_long(
            symbol=symbol,
            quantity=quantity,
            fill_price=exit_value,
            closed_at=now,
            trade_date=now.astimezone(EASTERN).date(),
            reason=exit_reason,
        )
        log.info(
            "paper sell symbol=%s quantity=%d value=%.2f pnl=%.2f reason=%s",
            symbol,
            quantity,
            exit_value,
            float(closed["realized_pnl"]),
            exit_reason,
        )
        return {
            "state": "POSITION_CLOSED",
            "reason": exit_reason,
            "mode": mode,
            "last_signal": None,
            "last_action": "SELL",
            "closed_trade": closed,
        }

    def _select_signal(
        self,
        latest: MarketCycle,
        previous: MarketCycle,
        settled_cash: float,
    ) -> PaperSignal | None:
        if latest.spot <= 0 or previous.spot <= 0:
            return None
        spot_move = latest.spot / previous.spot - 1.0
        if spot_move >= self.settings.minimum_spot_move_fraction:
            right = "call"
        elif spot_move <= -self.settings.minimum_spot_move_fraction:
            right = "put"
        else:
            return None

        previous_by_symbol = {row.symbol: row for row in previous.rows}
        budget = settled_cash * self.settings.maximum_position_fraction
        candidates: list[tuple[float, PaperSignal]] = []

        for row in latest.rows:
            if row.right != right or row.bid <= 0 or row.ask <= 0:
                continue
            previous_row = previous_by_symbol.get(row.symbol)
            if previous_row is None or previous_row.mid <= 0:
                continue

            moneyness = abs(row.strike - latest.spot) / latest.spot
            if moneyness > self.settings.maximum_moneyness_fraction:
                continue
            if row.delta is not None and not (0.08 <= abs(row.delta) <= 0.70):
                continue
            if row.spread_fraction > self.settings.maximum_spread_fraction:
                continue
            liquidity = row.liquidity_score
            if liquidity < self.settings.minimum_liquidity_score:
                continue

            contract_cost = row.ask * 100.0 + self.settings.fee_per_contract
            if contract_cost > budget + 1e-9:
                continue

            premium_move = row.mid / previous_row.mid - 1.0
            if premium_move < self.settings.minimum_premium_move_fraction:
                continue

            edge_proxy = premium_move - 1.5 * row.spread_fraction
            if edge_proxy < self.settings.minimum_edge_proxy:
                continue

            reason = (
                f"{right}_continuation spot={spot_move:+.3%} premium={premium_move:+.2%} "
                f"edge_proxy={edge_proxy:+.2%} liquidity={liquidity:.2f}"
            )
            signal = PaperSignal(
                symbol=row.symbol,
                right=right,
                spot=latest.spot,
                spot_move_fraction=spot_move,
                premium_move_fraction=premium_move,
                edge_proxy=edge_proxy,
                liquidity_score=liquidity,
                spread_fraction=row.spread_fraction,
                ask=row.ask,
                bid=row.bid,
                contract_cost=contract_cost,
                reason=reason,
            )
            score = edge_proxy + 0.05 * liquidity - 2.0 * moneyness
            candidates.append((score, signal))

        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]

    def _in_cooldown(self, now: datetime) -> bool:
        trades = self.account_store.recent_trades(1)
        if not trades:
            return False
        try:
            last = _parse_datetime(str(trades[0]["timestamp"]))
        except (TypeError, ValueError):
            return False
        return (now - last).total_seconds() < self.settings.cooldown_seconds

    def _market_session(self, eastern: datetime) -> bool:
        if eastern.weekday() >= 5:
            return False
        clock = eastern.timetz().replace(tzinfo=None)
        return self.settings.market_open <= clock < self.settings.market_close

    def _minutes_to_close(self, eastern: datetime) -> float:
        close = datetime.combine(eastern.date(), self.settings.market_close, tzinfo=EASTERN)
        return (close - eastern).total_seconds() / 60.0

    @staticmethod
    def _finish(now: datetime, values: dict[str, object]) -> dict[str, object]:
        payload = {
            "enabled": True,
            "strategy": STRATEGY_NAME,
            "last_tick": now.isoformat(),
            **values,
        }
        _publish(**payload)
        return payload


def run_forever(mode_getter: Callable[[], str]) -> None:
    """Run the autonomous SHADOW/PAPER loop. This function has no live-order path."""
    settings = PaperAutoSettings.from_env()
    market_path = Path(os.environ.get("COLLECTOR_DB_PATH", "/data/spy_0dte.sqlite"))
    account = PaperAccountStore(
        _paper_db_path(),
        default_starting_cash=_paper_starting_cash(),
    )
    trader = PaperAutoTrader(
        mode_getter=mode_getter,
        market_reader=SnapshotReader(market_path),
        account_store=account,
        settings=settings,
    )
    _publish(
        enabled=True,
        state="STARTING",
        reason="autonomous SHADOW/PAPER loop starting",
        strategy=STRATEGY_NAME,
    )
    log.info(
        "paper autotrader started strategy=%s tick=%.2fs market_db=%s",
        STRATEGY_NAME,
        settings.tick_seconds,
        market_path,
    )

    while True:
        started = time.monotonic()
        try:
            trader.tick()
        except Exception as exc:  # keep paper simulation alive across transient errors
            log.exception("paper automation tick failed")
            _publish(
                enabled=True,
                state="ERROR",
                reason=f"{type(exc).__name__}: {exc}",
                last_tick=datetime.now(timezone.utc).isoformat(),
            )
        elapsed = time.monotonic() - started
        time.sleep(max(0.05, settings.tick_seconds - elapsed))
