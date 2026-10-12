"""A no-time-limit research exit still sells before option expiration."""
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
import pytest

from engine.regime_hold_sensitivity import (
    simulate_unlimited, _stop_exit, liquidation_cutoff, quote_path, selection,
    backtest, START_CASH,
)

T=datetime(2026,10,12,13,50,tzinfo=timezone.utc)


def test_no_fixed_timeout_allows_20_minute_gain():
    cutoff=T+timedelta(minutes=30)
    path=[(T+timedelta(minutes=1),1.20),
          (T+timedelta(minutes=10),1.25),
          (T+timedelta(minutes=20),2.10),
          (T+timedelta(minutes=26),1.35)]
    outcome=simulate_unlimited(1.0,path,cutoff)
    assert outcome["exit_at"]==T+timedelta(minutes=26)
    assert outcome["exit_reason"]=="trailing_stop"
    assert outcome["pnl_usd"]>0


def test_unquoted_cutoff_is_unscorable_not_last_favorable_price():
    cutoff=T+timedelta(minutes=30)
    path=[(T+timedelta(minutes=1),1.1),
          (T+timedelta(minutes=10),1.2)]
    assert simulate_unlimited(1.0,path,cutoff) is None


def test_hard_stop_cannot_be_overridden_by_lack_of_timer():
    outcome=simulate_unlimited(1.0,[(T+timedelta(minutes=1),.40)],
                               T+timedelta(minutes=45))
    assert outcome["exit_reason"]=="independent_hard_stop_35pct"


def test_session_cutoff_never_holds_through_expiration():
    assert liquidation_cutoff(date(2026,10,12)).hour==15
    assert liquidation_cutoff(date(2026,10,12)).minute==45
    assert liquidation_cutoff(date(2026,11,27)).hour==12
    assert liquidation_cutoff(date(2026,11,27)).minute==45


def test_final_quote_required_near_cutoff_only_if_no_stop():
    cutoff=T+timedelta(minutes=30)
    path=[(T+timedelta(minutes=1),1.10),
          (cutoff,1.20)]
    x=simulate_unlimited(1.0,path,cutoff)
    assert x["exit_reason"]=="mandatory_pre_expiry_liquidation"


def test_without_real_archive_no_profits_or_drawdown_are_invented(tmp_path):
    with pytest.raises(ValueError,match="archive missing"):
        backtest(tmp_path,tmp_path/"out")


def test_first_future_quote_only_minute_resolution_and_cost_filter(monkeypatch):
    import engine.regime_hold_sensitivity as mod
    def quote(symbol,side,delta,spread,ask):
        return SimpleNamespace(symbol=symbol,right=side,delta=delta,
                               spread_fraction=spread,bid=ask*(1-spread/2)/(1+spread/2),ask=ask,
                               implied_volatility=.2,strike=600)
    frame_time=T-timedelta(minutes=35)
    frames=[]
    for i in range(43):
        frames.append(SimpleNamespace(
            timestamp=frame_time+timedelta(minutes=i),
            options=(),market=SimpleNamespace(
                spot=600,realized_volatility=.2,
                volume_ratio=1.0)))
    at=frames[35].timestamp+timedelta(minutes=1)
    earliest=quote("A","call",.5,.02,1)
    later=quote("B","call",.5,.01,1)
    frames[36].options=(earliest,)
    frames[37].options=(later,)
    monkeypatch.setattr(mod,"identify_setups",lambda x:[{
        "at":at,"side":"call","setup":"trend_reclaim",
        "trend_bps":3,"efficiency":.3,"volume_ratio":1,
    }])
    entries=selection(frames)
    assert len(entries)==1
    assert entries[0]["quote"].symbol=="A"
