"""Regression tests: causal opening close-range + frequency without profit targets."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import pytest

from engine.or_retest_frequency import (
    POLICIES, FROZEN_TRAIL, FROZEN_HARD_STOP, FROZEN_MAX_MIN,
    range_events, near_retest, confirmed, active, accept,
    candidate_quality, schedule_trades, metrics,
)


def day(prices, vol=1.7):
    start=datetime(2026,10,6,13,30,tzinfo=timezone.utc)  # 9:30 ET
    return [SimpleNamespace(
        timestamp=start+timedelta(minutes=i),
        market=SimpleNamespace(spot=px,volume_ratio=vol,
            implied_volatility=.21,realized_volatility=.18),
        options=(),
    ) for i,px in enumerate(prices)]


def test_exit_fixed_no_take_profit_and_consistent_time():
    assert FROZEN_TRAIL==30
    assert FROZEN_HARD_STOP==35
    assert FROZEN_MAX_MIN==10
    assert len(POLICIES)>=9
    assert "retest_5m_morning" in POLICIES
    assert "retest_5m_all_day" in POLICIES


def test_separate_bar_close_break_retest_confirm_sequence_is_causal():
    # range five completed closes 100-100.4, break at 9:35,
    # retest near the boundary at 9:36, confirm at 9:37.
    prices=[100,100.1,100.2,100.3,100.4,100.5,100.405,100.55,
            100.6,100.65,100.7]
    f=day(prices)
    events=range_events(f,5)
    direct=[e for e in events if e["type"]=="direct"]
    retest=[e for e in events if e["type"]=="retest"]
    assert direct and retest
    assert direct[0]["signal_at"]=="2026-10-06T13:36:00+00:00"
    assert retest[0]["signal_at"]=="2026-10-06T13:38:00+00:00"
    assert retest[0]["retest_observed_at"]=="2026-10-06T13:37:00+00:00"
    # Future completed bars cannot change any earlier emitted event.
    f[8].market.spot=1e8
    assert [e for e in range_events(f,5) if e["signal_at"]
            <"2026-10-06T13:38:01+00:00"][:2]==events[:2]


def test_no_breakout_before_opening_closes_and_disallows_fake_retest():
    assert range_events(day([100.0]*5),5)==[]
    assert near_retest(100.41,100.40,"call")
    assert not near_retest(99,100.40,"call")
    assert confirmed(100.6,100.405,100.4,"call")
    assert not confirmed(100.35,100.405,100.4,"call")
    with pytest.raises(ValueError):
        accept({"signal_at":"2026-10-06T13:38:00+00:00",
                "range_minutes":5,"type":"retest","volume_ratio":1.7},{},
               "insider_secrets_strategy")


def test_time_window_and_quality_flags_are_asof_only():
    e={"signal_at":"2026-10-06T13:38:00+00:00",
       "range_minutes":5,"type":"retest","volume_ratio":1.7}
    q={
       "option_spread_fraction":.04,
       "proxy_delta_5min_directional_move_usd":25,
       "entry_quote_roundtrip_friction_proxy_usd":10,
       "option_iv_to_spy_realized_vol_proxy_ratio":1.15,
    }
    assert accept(e,q,"retest_5m_morning")
    assert accept(e,q,"retest_5m_morning_rvol150")
    assert accept(e,q,"retest_5m_morning_cost_quality")
    assert accept(e,q,"retest_5m_morning_volatility_quality")
    assert accept(e,q,"retest_5m_all_day")
    assert not accept(e,q,"retest_15m_morning")
    e["signal_at"]="2026-10-06T16:38:00+00:00"  # 12:38 EDT
    assert not accept(e,q,"retest_5m_morning")
    assert accept(e,q,"retest_5m_all_day")
    q["option_iv_to_spy_realized_vol_proxy_ratio"]=3.0
    assert not accept(e,q,"retest_5m_morning_volatility_quality")
    assert active(datetime.fromisoformat(e["signal_at"]),
                  __import__("datetime").time(15,25))


def test_option_quote_cost_filters_do_not_consult_exit_outcome():
    f=day([600+.04*i for i in range(30)])
    q=SimpleNamespace(implied_volatility=.19,delta=.52,bid=1.03,
                      ask=1.05,spread_fraction=.019)
    first=candidate_quality(f,20,q,"call")
    f[21].market.spot=1000000
    assert first==candidate_quality(f,20,q,"call")
    assert first["estimated_entry_debit_per_contract_usd"]>100


def test_single_option_position_can_reenter_after_previous_exit(monkeypatch):
    import engine.or_retest_frequency as mod
    start=datetime(2026,10,6,13,40,tzinfo=timezone.utc)
    def candidate(offset):
        t=start+timedelta(minutes=offset)
        return {"event":{
            "signal_at":(t-timedelta(minutes=1)).isoformat(),
            "type":"retest","range_minutes":5,"right":"call",
            "volume_ratio":1.8},
            "entry_time":t,"quality":{
                "option_spread_fraction":.02,
                "proxy_delta_5min_directional_move_usd":20.,
                "entry_quote_roundtrip_friction_proxy_usd":6.,
                "option_iv_to_spy_realized_vol_proxy_ratio":1.05,
                "estimated_entry_debit_per_contract_usd":90.,
            },
            "quote":SimpleNamespace(ask=.8),"symbol":f"Q{offset}",
            "quote_index":1,"frames":(),
            "day":"2026-10-06"}
    def path(_,idx,symbol):
        offset=int(symbol[1:])
        t=start+timedelta(minutes=offset)
        return [(t+timedelta(minutes=j),.8)
                for j in (1,2,3,4,5,6,7,8,9,10)]
    def simulated(ask,path,trail):
        assert trail==30
        return {"exit_time":path[1][0].isoformat(),
                "pnl_usd":6.0,"return_on_premium_pct":6.,
                "exit_reason":"trailing_stop"}
    monkeypatch.setattr(mod,"_sampled_path",path)
    monkeypatch.setattr(mod,"simulate",simulated)
    from collections import defaultdict
    rows=schedule_trades([candidate(0),candidate(1),
                          candidate(3),candidate(4),candidate(6)],
                         "retest_5m_morning",defaultdict(int))
    assert [r["symbol"] for r in rows]==["Q0","Q3","Q6"]
    assert all(r["exit_reason"]=="trailing_stop" for r in rows)


def test_100pct_weekly_not_fabricated_from_trade_sum():
    s=metrics([],["2026-10-06"],"retest_5m_morning","all_inspected")
    assert s["trades"]==0
    assert s["win_rate_pct"] is None
    assert s["maximum_trades_in_one_day"]==0
    assert "account" in s["note"].lower()
