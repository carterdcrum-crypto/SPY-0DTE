from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Sequence, Tuple

from .data import HistoricalFrame


@dataclass(frozen=True)
class WalkForwardSplit:
    train: Tuple[HistoricalFrame, ...]
    validation: Tuple[HistoricalFrame, ...]
    test: Tuple[HistoricalFrame, ...]


def walk_forward_splits(
    frames: Sequence[HistoricalFrame],
    *,
    train: timedelta,
    validation: timedelta,
    test: timedelta,
    purge: timedelta,
    step: timedelta | None = None,
) -> Tuple[WalkForwardSplit, ...]:
    """Create rolling, chronological, purged train/validation/test windows.

    `purge` inserts a gap between train->validation and validation->test so
    labels near a boundary cannot leak future outcomes into the next phase.
    """

    if not frames:
        return ()
    if min(train.total_seconds(), validation.total_seconds(), test.total_seconds()) <= 0:
        raise ValueError("train/validation/test durations must be positive")
    if purge.total_seconds() < 0:
        raise ValueError("purge cannot be negative")
    step = step or test
    if step.total_seconds() <= 0:
        raise ValueError("step must be positive")

    ordered = tuple(sorted(frames, key=lambda f: f.timestamp))
    start = ordered[0].timestamp
    end_of_data = ordered[-1].timestamp
    out: list[WalkForwardSplit] = []

    cursor = start
    while True:
        train_start = cursor
        train_end = train_start + train
        validation_start = train_end + purge
        validation_end = validation_start + validation
        test_start = validation_end + purge
        test_end = test_start + test

        if test_start > end_of_data:
            break

        train_frames = tuple(f for f in ordered if train_start <= f.timestamp < train_end)
        validation_frames = tuple(f for f in ordered if validation_start <= f.timestamp < validation_end)
        test_frames = tuple(f for f in ordered if test_start <= f.timestamp < test_end)

        if train_frames and validation_frames and test_frames:
            out.append(WalkForwardSplit(train_frames, validation_frames, test_frames))

        cursor += step
        if cursor > end_of_data:
            break

    return tuple(out)
