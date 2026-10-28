"""One-session V5 volatility limit exception; all executable-quote gates stay active."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from engine import v5_close_only_operational as model
from engine.weekly_options_data import Bar
from zoneinfo import ZoneInfo


def _session(trade_date: date, *, stale=False, bid_size=20):
    eastern = ZoneInfo("America/New_York")
    first = datetime.combine(trade_date, datetime.min.time(), eastern).replace(hour=9, minute=30)
    bars = []
    for i in range(37):
        at = first + timedelta(minutes=i)
        close = 500 + i * 0.14
        bars.append(Bar(at, close - 0.03, close + 0.04,
                        close - 0.06, close, 15000))
    now = bars[-1].available_at + timedelta(seconds=1)
    stamp = now - timedelta(seconds=10) if stale else now
    q = SimpleNamespace(
        expiration=trade_date, right="call", option_symbol="SPY_TEST_OPTION",
        source="webull_production", timestamp_quality="option_quote_timestamp",
        timestamp=stamp, received_at=stamp, underlying_timestamp=stamp,
        bid=.45, ask=.46, bid_size=bid_size, ask_size=20,
        greeks=SimpleNamespace(delta=.5, implied_volatility=.35),
        underlying_price=505.0,
    )
    return now, bars, q


def test_todays_volatility_limit_rolls_back_automatically(monkeypatch):
    monkeypatch.setenv("V5_IV_RV_LIMIT_2_ET_DATE", "2026-10-28")
    eastern = ZoneInfo("America/New_York")
    assert model.max_iv_rv_ratio_for_session(datetime(2026,10,28,12,tzinfo=eastern)) == 2.0
    assert model.max_iv_rv_ratio_for_session(datetime(2026,10,29,12,tzinfo=eastern)) == 1.5
    assert model.max_iv_rv_ratio_for_session(datetime(2026,10,27,12,tzinfo=eastern)) == 1.5
    monkeypatch.delenv("V5_IV_RV_LIMIT_2_ET_DATE")
    assert model.max_iv_rv_ratio_for_session(datetime(2026,10,28,12,tzinfo=eastern)) == 1.5


def test_higher_limit_today_but_not_tomorrow_and_never_ignores_quote_safety(monkeypatch):
    monkeypatch.setenv("V5_IV_RV_LIMIT_2_ET_DATE", "2026-10-28")
    monkeypatch.setattr(model, "_rv", lambda bars, i: .2)
    monkeypatch.setattr(model, "last_spy_bar_is_fresh", lambda bars, now: True)
    monkeypatch.setattr(
        model, "close_proxy_signals",
        lambda frames, i, challenger: (
            SimpleNamespace(side="call", setup="volatility_expansion",
                            trend_bps=5.0, efficiency=.5, rvol=1.8),
        ),
    )
    for trade_date, eligible in [(date(2026,10,28),True), (date(2026,10,29),False)]:
        now, bars, q = _session(trade_date)
        candidate, diag = model.paper_candidate(
            now, bars, [q], equity=1000, settled=1000, peak=1000,
        )
        assert (candidate is not None) == eligible, diag
        assert diag["entry_diagnostics"]["iv_rv_ratio_limit"] == (2.0 if eligible else 1.5)
        if not eligible:
            assert diag["entry_diagnostics"]["rejection_code"] == "iv_out_of_range"

    now, bars, stale = _session(date(2026,10,28), stale=True)
    candidate, diag = model.paper_candidate(now,bars,[stale],equity=1000,settled=1000,peak=1000)
    assert candidate is None
    assert diag["entry_diagnostics"]["rejection_code"] == "stale_quote"

    now,bars,empty_depth = _session(date(2026,10,28),bid_size=0)
    candidate,diag=model.paper_candidate(now,bars,[empty_depth],equity=1000,settled=1000,peak=1000)
    assert candidate is None
    assert diag["entry_diagnostics"]["rejection_code"] == "missing_depth"
