from __future__ import annotations

from datetime import datetime, timedelta, timezone

from engine.backtest import BacktestSignal
from engine.data import HistoricalFrame
from engine.market import MarketSnapshot, OptionQuote
from engine.robust_validation import (
    StrategyCandidate,
    dataset_fingerprint,
    evaluate_locked_holdout,
    locked_holdout_partition,
    walk_forward_candidate_selection,
)


SYMBOL = "SPY260123C00600000"


def _frame(day: int) -> HistoricalFrame:
    timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=day)
    option_mid = 1.0 + 0.05 * day
    option = OptionQuote(
        symbol=SYMBOL,
        right="call",
        strike=600.0,
        bid=max(0.01, option_mid - 0.01),
        ask=option_mid + 0.01,
        delta=0.50,
        gamma=0.02,
        theta=-0.10,
        vega=0.05,
        implied_volatility=0.20,
        volume=1000,
        open_interest=5000,
        underlying_price=600.0 + day,
        minutes_to_expiry=120.0,
    )
    return HistoricalFrame(
        timestamp=timestamp,
        market=MarketSnapshot(
            spot=600.0 + day,
            bid=599.99 + day,
            ask=600.01 + day,
            realized_volatility=0.15,
            implied_volatility=0.20,
            volume_ratio=1.0,
            minutes_to_close=120.0,
        ),
        options=(option,),
    )


class RoundTripStrategy:
    def decide(self, frame, context):
        if context.position is None:
            return BacktestSignal("open", SYMBOL, 1, "synthetic entry")
        return BacktestSignal("close", reason="synthetic exit")


class HoldStrategy:
    def decide(self, frame, context):
        return BacktestSignal("hold")


def _round_trip(_train):
    return RoundTripStrategy()


def _hold(_train):
    return HoldStrategy()


def test_locked_holdout_has_embargo_and_stable_dataset_fingerprint():
    frames = tuple(_frame(day) for day in range(100))
    partition = locked_holdout_partition(
        frames,
        holdout=timedelta(days=15),
        purge=timedelta(days=2),
    )

    assert partition.development
    assert partition.locked_holdout
    assert partition.development[-1].timestamp < partition.locked_holdout[0].timestamp
    assert (
        partition.locked_holdout[0].timestamp - partition.development[-1].timestamp
        >= timedelta(days=3)
    )
    assert partition.dataset_fingerprint == dataset_fingerprint(frames)
    assert len(partition.dataset_fingerprint) == 64


def test_walk_forward_selects_on_validation_and_reports_outer_tests():
    frames = tuple(_frame(day) for day in range(90))
    candidates = (
        StrategyCandidate("hold", _hold, complexity=0),
        StrategyCandidate("round-trip", _round_trip, complexity=1),
    )

    evaluation = walk_forward_candidate_selection(
        frames,
        candidates,
        train=timedelta(days=20),
        validation=timedelta(days=8),
        test=timedelta(days=8),
        purge=timedelta(days=1),
        step=timedelta(days=8),
        minimum_trades=1,
    )

    assert evaluation.folds
    assert evaluation.champion_candidate == "round-trip"
    assert all(fold.selected_candidate == "round-trip" for fold in evaluation.folds)
    assert evaluation.total_test_trades > 0
    assert evaluation.median_test_return > 0
    assert evaluation.worst_test_return > 0

    for fold in evaluation.folds:
        assert fold.split.train[-1].timestamp < fold.split.validation[0].timestamp
        assert fold.split.validation[-1].timestamp < fold.split.test[0].timestamp


def test_near_tied_candidates_prefer_lower_complexity():
    frames = tuple(_frame(day) for day in range(70))
    candidates = (
        StrategyCandidate("simple", _round_trip, complexity=1),
        StrategyCandidate("complex", _round_trip, complexity=10),
    )

    evaluation = walk_forward_candidate_selection(
        frames,
        candidates,
        train=timedelta(days=20),
        validation=timedelta(days=8),
        test=timedelta(days=8),
        purge=timedelta(days=1),
        step=timedelta(days=8),
        minimum_trades=1,
        selection_tolerance=0.001,
    )

    assert all(fold.selected_candidate == "simple" for fold in evaluation.folds)
    assert evaluation.champion_candidate == "simple"


def test_locked_holdout_is_evaluated_separately_after_selection():
    frames = tuple(_frame(day) for day in range(100))
    partition = locked_holdout_partition(
        frames,
        holdout=timedelta(days=15),
        purge=timedelta(days=2),
    )
    candidate = StrategyCandidate("round-trip", _round_trip, complexity=1)

    holdout = evaluate_locked_holdout(partition, candidate)

    assert holdout.candidate == "round-trip"
    assert holdout.dataset_fingerprint == partition.dataset_fingerprint
    assert holdout.result.metrics.trades > 0
