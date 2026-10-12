"""Forensic labels rely on FUTURE outcomes: never admit trades with these."""
from datetime import datetime,timezone,timedelta
from types import SimpleNamespace

import pytest

from engine.trade_forensics import (
    spot_attribution,session_window,inspect_trade,
    group_statistics,correct_runup,FROZEN_TRAIL_PCT
)


def test_descriptive_market_vs_cost_failure_classes():
    assert spot_attribution("call",100,99,10,-1)=="wrong_underlying_direction"
    assert spot_attribution("put",100,101,10,-1)=="wrong_underlying_direction"
    assert spot_attribution("call",100,101,5,-1)=="gross_mid_gain_erased_by_modeled_execution_costs"
    assert spot_attribution("put",100,99,-4,-8)=="correct_spot_direction_but_option_lost"
    assert spot_attribution("call",100,100,-4,-8)=="flat_underlying_option_lost"
    assert spot_attribution("call",100,101,5,3)=="other_including_winning_trade"
    with pytest.raises(ValueError):
        spot_attribution("up",100,101,5,3)


def test_session_bucket_uses_eastern_local_not_utc():
    assert session_window(datetime(2026,10,6,13,45,tzinfo=timezone.utc))=="open_0930_1029"
    assert session_window(datetime(2026,10,6,14,40,tzinfo=timezone.utc))=="late_morning_1030_1059"
    assert session_window(datetime(2026,10,6,16,0,tzinfo=timezone.utc))=="midday_1100_1259"
    assert session_window(datetime(2026,10,6,18,0,tzinfo=timezone.utc))=="afternoon_1300_1524"


def test_modeled_option_ask_entry_bid_exit_cost_decomposition(monkeypatch):
    from engine import trade_forensics as module
    when=datetime(2026,10,6,13,30,tzinfo=timezone.utc)
    def fakequote(bid,ask):
        return SimpleNamespace(bid=bid,ask=ask,volume=200,
            open_interest=1000,implied_volatility=.2,
            spread_fraction=(ask-bid)/((ask+bid)/2))
    q1=fakequote(1.02,1.05)
    q2=fakequote(.80,.82)
    class Frame:
        def __init__(self,ts,spot,quote):
            self.timestamp=ts
            self.market=SimpleNamespace(spot=spot,volume_ratio=1.7,
                                        realized_volatility=.18)
            self.quote=quote
        def option(self,symbol):
            return self.quote if symbol=="SPY_OPTION" else None
    frames=[
        Frame(when,600,q1),
        Frame(when+timedelta(minutes=1),600.2,q2)
    ]
    entry=when+timedelta(minutes=1)
    exit=when+timedelta(minutes=2)
    ledger={"day":"2026-10-06","arm":"option_friction_only",
            "expert":"direct_5m","side":"call","symbol":"SPY_OPTION",
            "entry_at":entry.isoformat(),"exit_at":exit.isoformat(),
            "pnl_usd":"-28.30"}
    index={f.timestamp+timedelta(minutes=1):i for i,f in enumerate(frames)}
    monkeypatch.setattr(module,"_sampled_path",None,raising=False)
    import engine.exit_policy_comparison as cmp
    monkeypatch.setattr(cmp,"_sampled_path",
                        lambda frames,idx,symbol: [(exit,q2.bid)])
    result=inspect_trade(ledger,frames,index)
    assert result["pnl_usd"]==-28.30
    # Entry and exit mark-to-mid vs ask-buy / bid-sell
    assert result["option_mid_to_mid_usd"]==-22.5
    assert result["modeled_execution_drag_usd"]==5.8
    assert result["quoted_half_spreads_sum_usd"]==2.5
    assert result["fees_and_adverse_slippage_usd"]==3.30
    assert result["underlying_was_correct_over_1bp"] is True
    assert result["post_trade_loss_diagnosis"]=="correct_spot_direction_but_option_lost"
    assert result["historical_exit_reason"]=="hard_max_hold_10min"
    assert FROZEN_TRAIL_PCT==30


def test_loss_driver_report_not_false_profit_forecast():
    examples=[
        {"pnl_usd":-10.,"post_trade_loss_diagnosis":"wrong_underlying_direction",
         "option_mid_to_mid_usd":-5.,"modeled_execution_drag_usd":5.,
         "entry_spread_pct":2., "historical_exit_reason":"trailing_stop",
         "mfe_in_bid_usd_before_exit":8.,"underlying_was_correct_over_1bp":False},
        {"pnl_usd":5.,"post_trade_loss_diagnosis":"other_including_winning_trade",
         "option_mid_to_mid_usd":11.,"modeled_execution_drag_usd":6.,
         "entry_spread_pct":2.,"historical_exit_reason":"trailing_stop",
         "mfe_in_bid_usd_before_exit":6.,"underlying_was_correct_over_1bp":True}
    ]
    assert correct_runup(examples)==1
    out=group_statistics(examples)
    assert out["trades"]==2
    assert out["win_rate_pct"]==50.
    assert out["pnl_usd"]==-5.
    assert out["modeled_execution_drag_total_usd"]==11.
    assert group_statistics([])["win_rate_pct"] is None
