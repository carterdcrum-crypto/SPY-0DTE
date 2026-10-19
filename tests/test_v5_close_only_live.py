"""Requested close-only V5 cash-only LIVE profile. Fail-closed execution tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from engine.v5_close_only_live_strategy import V5CloseOnlyLiveStrategy
from engine.v5_close_only_operational import PROFILE, paper_candidate, paper_exit_reason
from engine.live_market import LiveMarketReader
from engine.weekly_options_data import Bar
from engine.v4_operational import _rv


def bars():
    start=datetime(2026,10,13,13,30,tzinfo=timezone.utc) # 09:30 ET
    result=[]
    for i in range(72):
        close=500.+i*.14
        result.append(Bar(start+timedelta(minutes=i),close-.03,
                          close+.04,close-.06,close,
                          60000 if i==70 else 15000))
    return result


def quote(now,bars, **changes):
    rv=_rv(bars,len(bars)-3)
    attrs=dict(source="webull_production",
               timestamp_quality="option_quote_timestamp",
               timestamp=now,received_at=now,underlying_timestamp=now,
               expiration=now.astimezone(__import__("zoneinfo").ZoneInfo(
                   "America/New_York")).date(),
               option_symbol="SPY261013C00500000",
               right="call",strike=500.,
               bid=.45,ask=.46,bid_size=22,ask_size=22,
               underlying_price=510.,
               greeks=SimpleNamespace(delta=.50,implied_volatility=rv*.7))
    attrs.update(changes)
    return SimpleNamespace(**attrs)


def test_close_only_unrestricted_proposal_has_no_fixed_three_contract_cap():
    data=bars()
    now=data[-1].available_at+timedelta(seconds=1)
    q=quote(now,data)
    p,d=paper_candidate(now,data,[q],equity=1000,settled=1000,peak=1000)
    assert p is not None,d
    assert p["quantity"]>3
    assert p["quantity"]<=min(q.bid_size,q.ask_size)
    assert p["debit_per_contract"]*p["quantity"]<=1000
    assert d["full_premium_risk_usd"]<=1000


def test_live_profile_generates_only_real_quote_verified_candidates():
    data=bars()
    now=data[-1].available_at+timedelta(seconds=1)
    q=quote(now,data)
    market=LiveMarketReader()
    market.quotes={q.option_symbol:q}
    live=V5CloseOnlyLiveStrategy(market,"/tmp/no_orders.sqlite")
    live.bar_cache.bars=data
    account={"equity":1000,"settled_cash":1000}
    daily={"high_watermark":1000,"halt_reason":None}
    limits=SimpleNamespace(max_contracts=3,max_account_exposure_pct=.30)
    p,d=live.entry(now,account,daily,limits)
    assert p is not None,d
    assert p["reason"].startswith(PROFILE+";")
    assert p["quantity"]>3 # executor enforces owner maximum 3
    assert p["model_proposed_cash_depth_quantity"]==p["quantity"]
    assert d["broker_settled_cash_and_nbbo_depth_enforced"] is True


def test_delayed_quote_fails_closed_before_live():
    data=bars()
    now=data[-1].available_at+timedelta(seconds=1)
    stale=quote(now,data,timestamp=now-timedelta(seconds=915))
    p,d=paper_candidate(now,data,[stale],equity=1000,settled=1000,peak=1000)
    assert p is None
    assert d["state"]=="NO_EXECUTABLE_DEPTH_OR_FRESH_OPTION_QUOTE"


def test_v5_live_existing_position_uses_price_hard_stop_and_observed_trail():
    data=bars()
    now=data[-1].available_at+timedelta(seconds=1)
    q=quote(now,data,bid=.30,ask=.36)
    market=LiveMarketReader()
    market.quotes={q.option_symbol:q}
    live=V5CloseOnlyLiveStrategy(market,"/tmp/no_orders.sqlite")
    live.bar_cache.bars=data
    position={"symbol":q.option_symbol,"average_price":.55,
              "peak_exit_value":56,
              "opened_at":(now-timedelta(minutes=2)).isoformat(),
              "entry_reason":PROFILE+"; momentum_ignition_CLOSE_PROXY"}
    reason=live.exit(position,now)
    assert reason=="v5_paper_stop_35pct"


def test_v5_ignores_legacy_daily_stop_without_bypassing_broker_cash():
    data=bars()
    now=data[-1].available_at+timedelta(seconds=1)
    q=quote(now,data)
    market=LiveMarketReader()
    market.quotes={q.option_symbol:q}
    live=V5CloseOnlyLiveStrategy(market,"/tmp/no_orders.sqlite")
    live.bar_cache.bars=data
    p,d=live.entry(now,{"equity":1000,"settled_cash":1000},
                   {"high_watermark":1000,"halt_reason":"daily_loss_stop"},
                   SimpleNamespace(max_contracts=100,max_account_exposure_pct=.35))
    assert p is not None and d["state"]=="SIGNAL"
    assert p["quantity"]>0


def test_v5_diagnostics_reveals_only_genuine_affordable_contracts():
    data=bars()
    now=data[-1].available_at+timedelta(seconds=1)
    q=quote(now,data)
    candidate,decision=paper_candidate(now,data,[q],equity=1000,settled=25,peak=1000)
    assert candidate is None
    assert decision["state"]=="NO_EXECUTABLE_DEPTH_OR_FRESH_OPTION_QUOTE"
    info=decision["entry_diagnostics"]
    assert info["signal_detected"] is True
    assert info["data_ready"] is True
    assert info["settled_cash_usd"]==25
    assert info["cheapest_qualified_contract_usd"]==47.65
    assert info["cheapest_qualified_ask"]==.46
    assert info["rejection_code"]=="insufficient_settled_cash"
    assert info["rejection_counts"]["insufficient_settled_cash"]==1


def test_v5_diagnostics_separates_stale_spread_depth_and_delta():
    data=bars()
    now=data[-1].available_at+timedelta(seconds=1)
    changes=[
        ({"timestamp":now-timedelta(seconds=10)}, "stale_quote"),
        ({"ask":.54}, "wide_spread"),
        ({"bid_size":0}, "missing_depth"),
        ({"greeks":SimpleNamespace(delta=.72,implied_volatility=_rv(data,len(data)-3)*.7)},
         "delta_out_of_range"),
        ({"greeks":None}, "missing_greeks"),
    ]
    for item,expected in changes:
        p,diag=paper_candidate(now,data,[quote(now,data,**item)],
                               equity=1000,settled=1000,peak=1000)
        assert p is None,expected
        info=diag["entry_diagnostics"]
        assert diag["state"]=="NO_EXECUTABLE_DEPTH_OR_FRESH_OPTION_QUOTE"
        assert info["rejection_code"]==expected
        assert info["rejection_counts"][expected]==1
        assert info["cheapest_qualified_contract_usd"] is None


def test_v5_no_close_only_signal_is_not_confused_with_quote_or_cash_failure():
    data=bars()
    b=data[70]
    data[70]=Bar(b.start,b.open,b.high,b.low,b.close,15000)
    now=data[-1].available_at+timedelta(seconds=1)
    p,diag=paper_candidate(now,data,[quote(now,data)],
                           equity=1000,settled=1000,peak=1000)
    assert p is None
    assert diag["state"]=="NO_CLOSE_ONLY_V5_SIGNAL"
    info=diag["entry_diagnostics"]
    assert info["data_ready"] is True
    assert info["signal_detected"] is False
    assert info["cheapest_qualified_contract_usd"] is None
    assert info["rejection_counts"]=={}
