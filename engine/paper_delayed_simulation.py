from __future__ import annotations

import logging
import statistics
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from .paper_account import PaperAccountStore
from .paper_autotrader import (
    MarketCycle,
    PaperAutoSettings,
    QuoteRow,
    SnapshotReader,
    _paper_db_path,
    _paper_starting_cash,
    _publish,
)
from .paper_dynamic_autotrader import risk_config_from_env
from .paper_ensemble_autotrader import (
    RISK_PROFILE,
    SIGNAL_STRATEGY,
    EnsemblePaperAutoTrader,
    _feed_delay_limit,
)

log = logging.getLogger("spy0dte.paper.delayed")
DATA_MODE = "DELAYED_SIMULATION"


def representative_feed_delay(cycle: MarketCycle | None) -> float | None:
    if cycle is None:
        return None
    delays = [
        float(row.feed_delay_seconds)
        for row in cycle.rows
        if row.feed_delay_seconds is not None and float(row.feed_delay_seconds) >= 0.0
    ]
    if not delays:
        return None
    return float(statistics.median(delays))


def _neutralize_row(row: QuoteRow) -> QuoteRow:
    # A known delayed quote is treated as fresh inside the time-shifted paper
    # tape. Unknown timestamps stay unknown and therefore still fail closed.
    if row.feed_delay_seconds is None or float(row.feed_delay_seconds) < 0.0:
        return row
    return replace(row, feed_delay_seconds=0.0)


def _neutralize_cycle(cycle: MarketCycle) -> MarketCycle:
    return MarketCycle(
        received_at=cycle.received_at,
        rows=tuple(_neutralize_row(row) for row in cycle.rows),
    )


class DelayedSimulationReader:
    """Expose a delayed market feed as a coherent time-shifted paper tape.

    This wrapper never changes prices. It only removes the source-delay penalty
    after the caller shifts the paper clock backward by that same delay. This is
    intentionally suitable only for SHADOW/PAPER research, never LIVE orders.
    """

    def __init__(self, inner: SnapshotReader) -> None:
        self.inner = inner

    def latest_cycles(self, limit: int = 2):
        return tuple(_neutralize_cycle(cycle) for cycle in self.inner.latest_cycles(limit))

    def latest_quote(self, symbol: str):
        row = self.inner.latest_quote(symbol)
        return None if row is None else _neutralize_row(row)


class DelayedSimulationPaperAutoTrader(EnsemblePaperAutoTrader):
    """Run the ensemble against delayed quotes on their own simulated clock."""

    def __init__(
        self,
        *,
        mode_getter: Callable[[], str],
        market_reader: SnapshotReader,
        account_store: PaperAccountStore,
        settings: PaperAutoSettings,
        risk_config,
        event_multiplier_getter: Callable[[], float] | None = None,
    ) -> None:
        self.source_market_reader = market_reader
        super().__init__(
            mode_getter=mode_getter,
            market_reader=DelayedSimulationReader(market_reader),
            account_store=account_store,
            settings=settings,
            risk_config=risk_config,
            event_multiplier_getter=event_multiplier_getter,
        )

    def _source_delay(self) -> float | None:
        positions = self.account_store.positions()
        if positions:
            symbol = str(positions[0].get("symbol") or "")
            if symbol:
                quote = self.source_market_reader.latest_quote(symbol)
                if (
                    quote is not None
                    and quote.feed_delay_seconds is not None
                    and float(quote.feed_delay_seconds) >= 0.0
                ):
                    return float(quote.feed_delay_seconds)

        cycles = self.source_market_reader.latest_cycles(1)
        return representative_feed_delay(cycles[0] if cycles else None)

    def tick(self, now: datetime | None = None) -> dict[str, object]:
        actual_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        source_delay = self._source_delay()

        # Unknown source timestamps are not normalized. The underlying ensemble
        # will keep its timestamp-unknown/freshness block in place.
        if source_delay is None:
            result = super().tick(actual_now)
            result.update(
                {
                    "data_mode": "DELAY_TIMESTAMP_UNKNOWN",
                    "source_feed_delay_seconds": None,
                    "market_time": None,
                    "last_tick": actual_now.isoformat(),
                }
            )
            _publish(**result)
            return result

        market_now = actual_now - timedelta(seconds=max(0.0, source_delay))
        result = super().tick(market_now)
        delayed = source_delay > _feed_delay_limit()
        data_mode = DATA_MODE if delayed else "REALTIME_COMPATIBLE"

        result.update(
            {
                "data_mode": data_mode,
                "source_feed_delay_seconds": source_delay,
                "market_time": market_now.isoformat(),
                "last_tick": actual_now.isoformat(),
            }
        )
        if delayed:
            original_reason = str(result.get("reason") or "")
            result["reason"] = (
                f"delayed-paper simulation ({source_delay:.1f}s behind live); "
                f"{original_reason}"
            )

        # Correct the public status after the base class publishes its simulated
        # clock. The app sees the real service tick plus the explicit market_time.
        _publish(**result)
        return result


def run_forever(mode_getter: Callable[[], str]) -> None:
    settings = PaperAutoSettings.from_env()
    risk_config = risk_config_from_env()
    market_path = Path(__import__("os").environ.get("COLLECTOR_DB_PATH", "/data/spy_0dte.sqlite"))
    account = PaperAccountStore(
        _paper_db_path(),
        default_starting_cash=_paper_starting_cash(),
    )
    trader = DelayedSimulationPaperAutoTrader(
        mode_getter=mode_getter,
        market_reader=SnapshotReader(market_path),
        account_store=account,
        settings=settings,
        risk_config=risk_config,
    )
    _publish(
        enabled=True,
        state="STARTING",
        reason=(
            "zero-cost delayed-tape SHADOW/PAPER loop starting; source delay is "
            "time-shifted and never treated as live execution data"
        ),
        strategy=SIGNAL_STRATEGY,
        risk_profile=RISK_PROFILE,
        data_mode=DATA_MODE,
    )
    log.info(
        "delayed paper simulator started signal=%s risk=%s tick=%.2fs market_db=%s",
        SIGNAL_STRATEGY,
        RISK_PROFILE,
        settings.tick_seconds,
        market_path,
    )
    while True:
        started = time.monotonic()
        try:
            trader.tick()
        except Exception as exc:
            log.exception("delayed paper simulation tick failed")
            _publish(
                enabled=True,
                state="ERROR",
                reason=f"{type(exc).__name__}: {exc}",
                data_mode=DATA_MODE,
                last_tick=datetime.now(timezone.utc).isoformat(),
            )
        elapsed = time.monotonic() - started
        time.sleep(max(0.05, settings.tick_seconds - elapsed))
