from __future__ import annotations

from datetime import date

from engine.data import load_canonical_csv, write_canonical_csv
from engine.providers.databento_history import build_frames_from_databento_rows


def test_databento_rows_normalize_and_roundtrip(tmp_path):
    trade_date = date(2026, 10, 2)
    definitions = [
        {
            "raw_symbol": "SPY   261002C00750000",
            "expiration": "2026-10-02",
            "instrument_class": "C",
            "strike_price": 750.0,
        },
        {
            "raw_symbol": "SPY   261002P00750000",
            "expiration": "2026-10-02",
            "instrument_class": "P",
            "strike_price": 750.0,
        },
    ]
    stock = [
        {"ts_event": "2026-10-02T19:29:00+00:00", "close": 750.0, "volume": 100000},
        {"ts_event": "2026-10-02T19:30:00+00:00", "close": 750.2, "volume": 120000},
    ]
    options = [
        {
            "ts_event": "2026-10-02T19:30:00+00:00",
            "symbol": "SPY   261002C00750000",
            "bid_px_00": 1.00,
            "ask_px_00": 1.10,
        },
        {
            "ts_event": "2026-10-02T19:30:00+00:00",
            "symbol": "SPY   261002P00750000",
            "bid_px_00": 0.80,
            "ask_px_00": 0.90,
        },
    ]

    frames = build_frames_from_databento_rows(
        trade_date=trade_date,
        definition_rows=definitions,
        option_rows=options,
        underlying_rows=stock,
    )

    assert len(frames) == 1
    assert frames[0].market.spot == 750.2
    assert {quote.right for quote in frames[0].options} == {"call", "put"}
    assert all(quote.ask > quote.bid >= 0 for quote in frames[0].options)
    assert all(quote.implied_volatility > 0 for quote in frames[0].options)

    path = tmp_path / "spy.csv"
    rows = write_canonical_csv(frames, path)
    assert rows == 2
    with path.open("r", encoding="utf-8") as handle:
        loaded = load_canonical_csv(handle)

    assert len(loaded) == 1
    assert loaded[0].timestamp == frames[0].timestamp
    assert [q.symbol for q in loaded[0].options] == [q.symbol for q in frames[0].options]
