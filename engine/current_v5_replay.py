"""One isolated no-purchase current-V5 archival comparison, then resume watcher."""
from __future__ import annotations
import contextlib
import io
import json
import os
from pathlib import Path
from . import v5_archive_proxy as replay
from .v4_operational import FEE

def main():
    root = Path(os.environ.get("RESEARCH_DATA_DIR", "/data/research"))
    output = root / "current_v5_2026_10_23"
    # Match operational per-side fees for both comparison arms.
    replay.FEE = FEE
    for cash in (195.11, 1000.0):
        for label, current in (("before", False), ("current", True)):
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    report = replay.run(
                        root, output / str(cash) / label,
                        starting_cash=cash, arms=(replay.ARMS[-1],),
                        current_setup=current)
                result = report["results"][replay.ARMS[-1]]
                scalar = {k: v for k, v in result.items()
                          if not isinstance(v, (list, dict))}
                print("CURRENT_V5_BACKTEST " + json.dumps({
                    "variant": label, "starting_cash": cash,
                    "sessions": report["sessions"],
                    "first_day": report["first_day"],
                    "last_day": report["last_day"],
                    "iv_ratio_cap": None if current else 1.5,
                    "max_hold_minutes": None if current else 18,
                    "fee_per_side": FEE,
                    "status": report["status"],
                    "results": scalar,
                    "missing_depth": True,
                    "exact_live_replay": False,
                    "broker_orders": 0, "data_purchases": 0,
                }, sort_keys=True), flush=True)
            except Exception as exc:
                print("CURRENT_V5_BACKTEST_BLOCKED " + json.dumps({
                    "variant": label, "starting_cash": cash,
                    "error_type": type(exc).__name__, "detail": str(exc)[:400],
                    "broker_orders": 0, "data_purchases": 0,
                }, sort_keys=True), flush=True)
    os.execvp("python", ["python", "-m", "engine.forward_watch"])

if __name__ == "__main__":
    main()
