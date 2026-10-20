from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone

import pytest

from engine.burst_research import CausalBurstConfig, _affordable_call, _percentile, load_research_directory
from engine.data import HistoricalFrame, write_canonical_csv
from engine.market import MarketSnapshot, OptionQuote


def _quote(symbol: str, *, ask: float, bid: float, delta: float) -> OptionQuote:
    return OptionQuote(
        symbol=symbol,
        right="call",
        strike=700.0,
        bid=bid,
        ask=ask,
        delta=delta,
        gamma=0.05,
        theta=-1.0,
        vega=0.03,
        implied_volatility=0.20,
        volume=1000,
        open_interest=3000,
        underlying_price=700.0,
        minutes_to_expiry=120.0,
    )


def _frame() -> HistoricalFrame:
    return HistoricalFrame(
        timestamp=datetime(2026, 10, 2, 15, 0, tzinfo=timezone.utc),
        market=MarketSnapshot(
            spot=700.0,
            bid=699.99,
            ask=700.01,
            realized_volatility=0.18,
            implied_volatility=0.20,
            volume_ratio=1.2,
            minutes_to_close=60.0,
        ),
        options=(
            _quote("WIDE", bid=0.50, ask=1.00, delta=0.50),
            _quote("DELTA40", bid=0.94, ask=1.00, delta=0.40),
            _quote("DELTA50", bid=0.94, ask=1.00, delta=0.50),
            _quote("EXPENSIVE", bid=1.94, ask=2.00, delta=0.50),
        ),
    )


def test_percentile_interpolates_without_future_state():
    assert _percentile([1.0, 2.0, 3.0, 4.0], 0.50) == pytest.approx(2.5)


def test_contract_selection_respects_cash_spread_and_target_delta():
    chosen = _affordable_call(
        _frame(),
        settled_cash=120.0,
        config=CausalBurstConfig(
            minimum_prior_accelerations=20,
            acceleration_history=20,
            max_spread_fraction=0.10,
        ),
    )
    assert chosen is not None
    assert chosen.symbol == "DELTA50"


def test_research_directory_prefers_gzip_copy(tmp_path):
    frame = _frame()
    plain = tmp_path / "spy_0dte_2026-10-02.csv"
    compressed = tmp_path / "spy_0dte_2026-10-02.csv.gz"
    assert write_canonical_csv((frame,), plain) == 4
    assert write_canonical_csv((frame,), compressed) == 4

    loaded = load_research_directory(tmp_path)
    assert len(loaded) == 1
    assert loaded[0].timestamp == frame.timestamp
    assert len(loaded[0].options) == 4


def test_research_directory_can_be_date_scoped(tmp_path):
    first = replace(_frame(), timestamp=datetime(2026, 9, 25, 15, 0, tzinfo=timezone.utc))
    later = replace(_frame(), timestamp=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc))
    assert write_canonical_csv((first,), tmp_path / "spy_0dte_2026-09-25.csv.gz") == 4
    assert write_canonical_csv((later,), tmp_path / "spy_0dte_2026-09-28.csv.gz") == 4

    loaded = load_research_directory(
        tmp_path,
        start_date=date(2026, 9, 25),
        end_date=date(2026, 9, 25),
    )
    assert len(loaded) == 1
    assert loaded[0].timestamp.date() == date(2026, 9, 25)
