from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from .paper_account import PaperAccountStore
from .paper_ai_autotrader import AIAugmentedDynamicExitTrader, AI_PROFILE
from .paper_autotrader import (
    PaperAutoSettings,
    SnapshotReader,
    _paper_db_path,
    _paper_starting_cash,
    _publish,
)
from .paper_delayed_simulation import (
    DATA_MODE,
    DelayedSimulationReader,
    representative_feed_delay,
)
from .paper_dynamic_autotrader import risk_config_from_env
from .paper_dynamic_exit import EXIT_PROFILE
from .paper_ensemble_autotrader import RISK_PROFILE, SIGNAL_STRATEGY, _feed_delay_limit

log = logging.getLogger("spy0dte.paper.ai_delayed")


class AIDelayedSimulationPaperAutoTrader(AIAugmentedDynamicExitTrader):
    """AI-augmented ensemble running strictly on the delayed paper tape."""

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
        _publish(**result)
        return result


def run_forever(mode_getter: Callable[[], str]) -> None:
    settings = PaperAutoSettings.from_env()
    risk_config = risk_config_from_env()
    market_path = Path(os.environ.get("COLLECTOR_DB_PATH", "/data/spy_0dte.sqlite"))
    account = PaperAccountStore(
        _paper_db_path(),
        default_starting_cash=_paper_starting_cash(),
    )
    trader = AIDelayedSimulationPaperAutoTrader(
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
            "delayed-tape quant ensemble + dynamic risk/exit + optional multi-provider "
            "AI advisory loop starting; AI receives tape-only context"
        ),
        strategy=SIGNAL_STRATEGY,
        risk_profile=RISK_PROFILE,
        exit_profile=EXIT_PROFILE,
        ai_profile=AI_PROFILE,
        ai_advisory=trader.ai_engine.status(),
        data_mode=DATA_MODE,
    )
    log.info(
        "delayed AI paper simulator started signal=%s risk=%s exit=%s ai=%s providers=%s tick=%.2fs",
        SIGNAL_STRATEGY,
        RISK_PROFILE,
        EXIT_PROFILE,
        AI_PROFILE,
        ",".join(provider.name for provider in trader.ai_engine.providers) or "none",
        settings.tick_seconds,
    )
    while True:
        started = time.monotonic()
        try:
            trader.tick()
        except Exception as exc:
            log.exception("delayed AI paper simulation tick failed")
            _publish(
                enabled=True,
                state="ERROR",
                reason=f"{type(exc).__name__}: {exc}",
                data_mode=DATA_MODE,
                exit_profile=EXIT_PROFILE,
                ai_profile=AI_PROFILE,
                last_tick=datetime.now(timezone.utc).isoformat(),
            )
        elapsed = time.monotonic() - started
        time.sleep(max(0.05, settings.tick_seconds - elapsed))
