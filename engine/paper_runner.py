"""One supervised runtime for both realtime and delayed PAPER/SHADOW trading."""
from __future__ import annotations

import fcntl
import logging
import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .paper_account import PaperAccountStore
from .paper_autotrader import PaperAutoSettings, SnapshotReader, _paper_db_path, _paper_starting_cash, _publish
from .paper_dynamic_autotrader import risk_config_from_env
from .runtime import WorkerContext

log = logging.getLogger("spy0dte.paper.runner")


@contextmanager
def execution_lock(account_path: str):
    """Prevent overlapping deploys/processes from trading the same paper ledger."""
    path = Path(account_path + ".runner.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def create_trader(mode_getter: Callable[[], str], *, delayed: bool):
    from .paper_ai_delayed_simulation import AIDelayedSimulationPaperAutoTrader
    from .paper_ai_exit_v2 import SaferAIAugmentedDynamicExitTrader

    trader_class = AIDelayedSimulationPaperAutoTrader if delayed else SaferAIAugmentedDynamicExitTrader
    return trader_class(
        mode_getter=mode_getter,
        market_reader=SnapshotReader(Path(os.environ.get("COLLECTOR_DB_PATH", "/data/spy_0dte.sqlite"))),
        account_store=PaperAccountStore(_paper_db_path(), default_starting_cash=_paper_starting_cash()),
        settings=PaperAutoSettings.from_env(),
        risk_config=risk_config_from_env(),
    )


def run_forever(mode_getter: Callable[[], str], context: WorkerContext, *, delayed: bool) -> None:
    with execution_lock(_paper_db_path()):
        trader = create_trader(mode_getter, delayed=delayed)
        failures = 0
        _publish(enabled=True, state="STARTING", reason="recovering persistent paper account and market history")
        context.heartbeat()
        while not context.stop.is_set():
            started = time.monotonic()
            try:
                result = trader.tick()
                failures = 0
                context.heartbeat(last_error=None, consecutive_failures=0, decision_state=result.get("state"))
            except Exception as exc:
                failures += 1
                _publish(
                    enabled=True, state="RECOVERING", last_signal=None, last_action=None,
                    reason=f"paper engine recovering from {type(exc).__name__}; retrying automatically",
                    last_tick=datetime.now(timezone.utc).isoformat(),
                )
                context.heartbeat(state="RECOVERING", last_error=type(exc).__name__)
                log.warning("paper tick failed type=%s consecutive=%d", type(exc).__name__, failures)
                if failures >= 5:
                    raise  # Rebuild readers/models; committed ledger state survives.
            interval = trader.settings.tick_seconds if not failures else min(30.0, 2.0 ** failures)
            context.wait(max(0.05, interval - (time.monotonic() - started)))
