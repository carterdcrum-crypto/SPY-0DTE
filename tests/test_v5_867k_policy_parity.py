"""Regression contract for the originally reported $867,017.60 V5 replay.

These tests assert identical *decision policy*, not real-world replication of
the archived result. The replay lacked executable NBBO sizes and full OHLCV.
"""
import inspect
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from engine.live_autonomy import LiveExecutor
from engine.v4_operational import ET, WebullMinuteBarCache
from engine.v5_archive_proxy import close_proxy_signals
from engine.v5_close_only_operational import (
    V5_LATEST_ENTRY_MINUTES, V5_MAX_HOLD_MINUTES,
    V5_MIN_COMPLETED_BARS, paper_candidate
)
from engine.v5_close_only_live_strategy import V5CloseOnlyLiveStrategy
from engine.v5_close_only_paper import V5CloseOnlyPaperTrader
from engine.paper_account import PaperAccountStore
from engine.paper_autotrader import PaperAutoSettings
from engine.v5_drawdown_sizing import drawdown_fraction
from engine import v5_close_only_operational as operational
from quantconnect.v5_core import Signal


def _inputs(now):
    # Exactly 37 valid completed one-minute bars after 9:30 ET.
    bars = [
        SimpleNamespace(
            start=now-timedelta(minutes=V5_MIN_COMPLETED_BARS-i),
            available_at=now-timedelta(minutes=V5_MIN_COMPLETED_BARS-i-1),
            volume=200,
            close=600.0,
        )
        for i in range(V5_MIN_COMPLETED_BARS)
    ]
    quote = SimpleNamespace(
        expiration=now.astimezone(ET).date(),right="call",
        source="webull_production",
        timestamp_quality="option_quote_timestamp",
        timestamp=now-timedelta(seconds=1),
        received_at=now-timedelta(seconds=1),
        underlying_timestamp=now-timedelta(seconds=1),
        bid=1.04,ask=1.05,bid_size=9,ask_size=9,
        greeks=SimpleNamespace(delta=.5,implied_volatility=.2),
        option_symbol="SPY261023C00600000",underlying_price=600.0,
    )
    return bars,quote


def _fake_signals(monkeypatch):
    monkeypatch.setattr(operational, "close_proxy_signals",
        lambda frames,i,challenger: (
            Signal("call","volatility_expansion_CLOSE_PROXY",1.75,5.,.5),))
    monkeypatch.setattr(operational,"_rv",lambda bars,i: .2)
    monkeypatch.setattr(operational,"last_spy_bar_is_fresh",lambda bars,now: True)


def test_v5_reuses_same_original_three_signal_proxy():
    assert operational.close_proxy_signals is close_proxy_signals
    assert V5_MIN_COMPLETED_BARS == 37
    assert V5_LATEST_ENTRY_MINUTES == 26
    assert V5_MAX_HOLD_MINUTES == 18
    assert WebullMinuteBarCache().minimum_history_bars == 55


def test_v5_live_and_paper_bar_cache_start_before_55(tmp_path):
    live=V5CloseOnlyLiveStrategy(SimpleNamespace(quotes={}),str(tmp_path/"live.audit"))
    assert live.bar_cache.minimum_history_bars == 37
    paper=V5CloseOnlyPaperTrader(
        mode_getter=lambda:"PAPER",
        market_reader=SimpleNamespace(path=str(tmp_path/"source.sqlite")),
        account_store=PaperAccountStore(tmp_path/"paper.sqlite"),
        settings=PaperAutoSettings(),
    )
    assert paper.bar_cache.minimum_history_bars == 37


def test_v5_candidate_can_enter_at_original_first_historical_bar(monkeypatch):
    _fake_signals(monkeypatch)
    now=datetime(2026,10,23,14,7,tzinfo=timezone.utc)  # 10:07 ET
    bars,quote=_inputs(now)
    actual,meta=paper_candidate(now,bars,[quote],equity=1000,settled=1000,
                                peak=1000,risk_fraction=1.0,
                                risk_opening_equity=1000)
    assert actual is not None,meta
    assert actual["setup"]=="volatility_expansion_CLOSE_PROXY"
    assert actual["quantity"]==9
    too_early,meta=paper_candidate(now,bars[1:],[quote],equity=1000,
                                  settled=1000,peak=1000)
    assert too_early is None
    assert meta["state"]=="WAITING_REAL_COMPLETED_SPY_M1"


def test_last_entry_exactly_26_minutes_before_regular_close(monkeypatch):
    _fake_signals(monkeypatch)
    inside=datetime(2026,10,23,19,33,59,tzinfo=timezone.utc) # 15:33:59 ET
    bars,quote=_inputs(inside)
    candidate,meta=paper_candidate(inside,bars,[quote],equity=1000,
                                  settled=1000,peak=1000)
    assert candidate is not None,meta
    closed=inside+timedelta(seconds=1)
    bars2,quote2=_inputs(closed)
    candidate,meta=paper_candidate(closed,bars2,[quote2],equity=1000,
                                  settled=1000,peak=1000)
    assert candidate is None and meta["state"]=="ENTRY_WINDOW_CLOSED"


def test_cash_first_100_70_40_15_risk_bands():
    assert [drawdown_fraction(x,1000) for x in (1000,970,920,880)] == [
        1.0,.70,.40,.15
    ]


def test_v5_broker_submit_rechecks_same_latest_entry(monkeypatch):
    # Exercise the actual _submit gate. The fake store must never be touched.
    now=datetime(2026,10,23,19,34,tzinfo=timezone.utc) # 15:34 ET
    executor=LiveExecutor.__new__(LiveExecutor)
    executor.clock=lambda:now
    executor.mode_getter=lambda:"LIVE"
    executor.writes_allowed=lambda:True
    executor.market=SimpleNamespace(fresh_quote=lambda symbol, clock:object())
    executor.strategy=SimpleNamespace(
        unrestricted_v5_live=True,_minutes_to_close=lambda t:26.)
    executor.store=SimpleNamespace(reserve=lambda *a,**k:
        (_ for _ in ()).throw(AssertionError("must never reserve")))
    order=SimpleNamespace(side="BUY")
    result=executor._submit(order,symbol="SPY",reason="test",
                            signal_key="test",policy={"generation":1},now=now)
    assert result["state"]=="ENTRY_WINDOW_CLOSED"


def test_no_v5_extra_180_second_delay_vs_original_proxy():
    assert "_in_cooldown" not in inspect.getsource(V5CloseOnlyPaperTrader.tick)
    assert "orders and not self._unrestricted_v5()" in inspect.getsource(LiveExecutor.tick)
