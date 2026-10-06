from __future__ import annotations

import argparse
from datetime import date, timedelta
from pathlib import Path

from engine.data import write_canonical_csv
from engine.providers.databento_history import (
    DatabentoHistoryConfig,
    DatabentoHistoryProvider,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill SPY 0DTE historical NBBO data into the repo's canonical research format."
    )
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD, inclusive")
    parser.add_argument("--output", required=True, help="Destination canonical CSV")
    parser.add_argument(
        "--strike-distance-pct",
        type=float,
        default=0.08,
        help="Maximum distance from SPY spot, e.g. 0.08 = +/-8%%",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    if end < start:
        raise SystemExit("--end must be on or after --start")

    provider = DatabentoHistoryProvider(
        config=DatabentoHistoryConfig(max_strike_distance_pct=args.strike_distance_pct)
    )

    frames = []
    day = start
    while day <= end:
        if day.weekday() < 5:
            fetched = provider.fetch_day(day)
            frames.extend(fetched)
            print(f"{day}: {len(fetched)} frames")
        day += timedelta(days=1)

    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = write_canonical_csv(frames, path)
    print(f"wrote {rows} option-quote rows across {len(frames)} frames to {path}")


if __name__ == "__main__":
    main()
