"""V5 full cash with EOD-equity drawdown throttling.

Matches the historical full_cash_with_dd_throttle capital bands without
increasing broker settled buying power, quote depth, or existing order limits.
"""
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from engine.paper_account import PaperAccountStore
from engine.v5_drawdown_sizing import drawdown_fraction, observe_flat_session
from engine import v5_close_only_operational as operational
from quantconnect.v5_core import Signal


@pytest.mark.parametrize("equity,peak,fraction", [
    (1000,1000,1.0),
    (990,1000,1.0),
    (980,1000,.70),
    (960,1000,.70),
    (950,1000,.40),
    (920,1000,.40),
    (900,1000,.15),
    (700,1000,.15),
])
def test_drawdown_bands(equity,peak,fraction):
    assert drawdown_fraction(equity,peak)==fraction


def test_invalid_equity_fails_closed():
    with pytest.raises(ValueError):
        drawdown_fraction(0,1000)
    with pytest.raises(ValueError):
        drawdown_fraction(float("nan"),1000)


def test_flat_peak_persists_and_no_intraday_risk_reset(tmp_path):
    db=tmp_path/"v5live.sqlite"
    t1=date(2026,10,22)
    t2=date(2026,10,23)
    a=observe_flat_session(db,t1,1000)
    assert a["fraction"]==1.0
    observe_flat_session(db,t1,1500)  # observed flat at previous session end
    b=observe_flat_session(db,t2,1350)
    assert b["opening_equity"]==1350
    assert b["prior_peak_equity"]==1500
    assert b["fraction"]==.15
    # Same-session P&L changes must not change that session's quantity budget.
    c=observe_flat_session(db,t2,1600)
    assert c["opening_equity"]==1350
    assert c["fraction"]==.15
    # Persist across new store calls/processes.
    d=observe_flat_session(db,date(2026,10,26),1560)
    assert d["prior_peak_equity"]==1600
    assert d["fraction"]==.70


def test_paper_reset_clears_only_paper_throttle_epoch(tmp_path):
    p=tmp_path/"paper.sqlite"
    store=PaperAccountStore(p, default_starting_cash=1000)
    observe_flat_session(p,date(2026,10,22),2000)
    store.reset(300)
    r=observe_flat_session(p,date(2026,10,23),300)
    assert r["opening_equity"]==300
    assert r["prior_peak_equity"]==300
    assert r["fraction"]==1.0


def test_candidate_cannot_override_depth_settled_cash_or_risk(monkeypatch):
    # Synthetic observations are ONLY for unit-testing screening/sizing,
    # never inserted into the real historical Railway backtest.
    now=datetime(2026,10,23,14,30,tzinfo=timezone.utc)
    starts=[now-timedelta(minutes=58-i) for i in range(57)]
    bars=[SimpleNamespace(start=start,volume=200,close=600.0,
                          available_at=now-timedelta(seconds=90))
          for start in starts]
    q=SimpleNamespace(expiration=now.date(),right="call",
                      source="webull_production",
                      timestamp_quality="option_quote_timestamp",
                      timestamp=now-timedelta(seconds=1),
                      received_at=now-timedelta(seconds=1),
                      underlying_timestamp=now-timedelta(seconds=1),
                      bid=1.04,ask=1.05,bid_size=9,ask_size=9,
                      greeks=SimpleNamespace(delta=.5,implied_volatility=.2),
                      option_symbol="SPY261023C00600000",underlying_price=600.)
    monkeypatch.setattr(operational,"close_proxy_signals",
        lambda frames,i,challenger: (Signal("call","volatility_expansion_CLOSE_PROXY",1.7,5.,.5),))
    monkeypatch.setattr(operational,"_rv",lambda bars,i: .2)
    monkeypatch.setattr(operational,"last_spy_bar_is_fresh",lambda bars,now: True)
    full,_=operational.paper_candidate(now,bars,[q],equity=1000,
                       settled=1000,peak=1000,risk_fraction=1,
                       risk_opening_equity=1000)
    reduced,_=operational.paper_candidate(now,bars,[q],equity=1000,
                       settled=1000,peak=1000,risk_fraction=.4,
                       risk_opening_equity=1000)
    assert full is not None and reduced is not None
    assert full["quantity"]==9  # depth-limited even with full cash
    assert reduced["quantity"]==3  # 40% of opening equity, whole contracts
    assert reduced["risk_budget_usd"]==400.
