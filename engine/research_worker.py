from __future__ import annotations

import os
import time
from datetime import date
from pathlib import Path

from .data import write_canonical_csv
from .providers.databento_history import DatabentoHistoryProvider


def run_smoke_backfill() -> Path:
    raw_date = os.getenv("RESEARCH_SMOKE_DATE")
    if not raw_date:
        raise RuntimeError("RESEARCH_SMOKE_DATE is required")
    trade_date = date.fromisoformat(raw_date)

    output_dir = Path(os.getenv("RESEARCH_DATA_DIR", "/data/research"))
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"spy_0dte_{trade_date.isoformat()}.csv"

    provider = DatabentoHistoryProvider()
    frames = provider.fetch_day(trade_date)
    rows = write_canonical_csv(frames, output)
    print(
        f"research smoke complete: date={trade_date.isoformat()} "
        f"frames={len(frames)} rows={rows} output={output}"
    )
    if not frames or rows == 0:
        raise RuntimeError("Databento smoke fetch returned no usable SPY 0DTE quotes")
    return output


def main() -> None:
    run_smoke_backfill()
    if os.getenv("RESEARCH_KEEP_ALIVE", "false").lower() in {"1", "true", "yes"}:
        print("research worker idle; smoke dataset persisted")
        while True:
            time.sleep(3600)


if __name__ == "__main__":
    main()
