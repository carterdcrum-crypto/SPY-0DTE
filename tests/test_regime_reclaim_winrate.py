"""Tests for strictly as-of high-win-rate candidate research, frozen exits, cash."""
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
from collections import Counter

import pytest

from engine.regime_reclaim_winrate import (
    START_CASH,TRAIL_PCT,HARD_STOP_PCT,MAX_HOLD_MIN,ARMS,
    ema,identify_setups,accept,asof_cost_ok,run_one_session,
    cash_summary,summarize,
)


def synthetic(prices,vol=1.8):
    at=datetime(2026,10,6,13,30,tzinfo=timezone.utc)
    return [SimpleNamespace(
        timestamp=at+timedelta(minutes=i),
        market=SimpleNamespace(spot=p,volume_ratio=vol,
            realized_volatility=.18,implied_volatility=.2)
    ) for i,p in enumerate(prices)]


def test_frozen_target_free_no_partial_exits_and_1000_cash():
    assert START_CASH==1000.
    assert TRAIL_PCT==30 and HARD_STOP_PCT==35 and MAX_HOLD_MIN==10
    assert len(ARMS)==8


def test_ema_does_not_use_future():
    assert ema([100]*30,8)==100
    assert ema([100,110],8)>100
    with pytest.raises(ValueError):
        ema([],8)


def test_signal_time_and_future_spy_candles_cannot_alter_earlier_candidates():
    prices=[600+0.065*i for i in range(100)]
    prices[38]=prices[37]-.5
    prices[39]=prices[37]+.15
    frames=synthetic(prices)
    before=identify_setups(frames[:43])
    assert all(e["at"]<=frames[42].timestamp+timedelta(minutes=1)
               for e in before)
    frames[48].market.spot=99999
    earlier=[e for e in identify_setups(frames)
             if e["at"]<=frames[42].timestamp+timedelta(minutes=1)]
    assert earlier==before


def test_eligibility_only_uses_asof_entry_quote_and_completed_spy_features():
    quote=SimpleNamespace(spread_fraction=.012,implied_volatility=.19)
    market=SimpleNamespace(realized_volatility=.17)
    assert asof_cost_ok(quote,market)
    quote.spread_fraction=.07
    assert not asof_cost_ok(quote,market)
    quote.spread_fraction=.01
    e={"at":datetime(2026,10,6,14,2,tzinfo=timezone.utc),
       "setup":"trend_reclaim","volume_ratio":1.8}
    candidate={"event":e,"quote":quote,"quote_market":market}
    assert accept(candidate,"trend_reclaim")
    assert accept(candidate,"trend_reclaim_volume_150")
    assert accept(candidate,"trend_reclaim_cost_aware")
    assert accept(candidate,"trend_reclaim_morning")
    assert not accept(candidate,"stretch_reclaim")
    with pytest.raises(ValueError):
        accept(candidate,"guaranteed_100pct_wins")


def test_cash_reserve_and_next_day_proceeds_not_recycled(monkeypatch):
    import engine.regime_reclaim_winrate as mod
    entry=datetime(2026,10,6,14,3,tzinfo=timezone.utc)
    frames=synthetic([600+.03*i for i in range(70)])
    q=SimpleNamespace(ask=3.99,spread_fraction=.012,
                      implied_volatility=.2)
    qm=SimpleNamespace(realized_volatility=.2)
    def c(index):
        return {"event":{"at":entry+timedelta(minutes=12*index),
                 "setup":"trend_reclaim","volume_ratio":1.8,"side":"call"},
                "quote":q,"quote_market":qm,
                "entry_at":entry+timedelta(minutes=12*index),
                "entry_debit_usd":400.65,"symbol":f"CONTRACT{index}",
                "option_index":index}
    def fakepath(frames,idx,symbol):
        when=entry+timedelta(minutes=12*idx)
        return [(when+timedelta(minutes=2),4.)]
    def fakesim(ask,trace,trail):
        assert trail==30
        return {"pnl_usd":150.,
               "exit_time":trace[0][0].isoformat(),
               "exit_reason":"trailing_stop","return_on_premium_pct":35.}
    monkeypatch.setattr(mod,"_sampled_path",fakepath)
    monkeypatch.setattr(mod,"simulate",fakesim)
    diagnostics=Counter()
    actual,settled,unsettled=run_one_session(
        frames,[c(0),c(1),c(2)],"trend_reclaim",1000.,diagnostics)
    assert len(actual)==2
    assert diagnostics["trend_reclaim_insufficient_settled_cash"]==1
    assert round(settled,2)==198.70
    assert round(unsettled,2)==1101.30
    # Both proceeds are inaccessible until the NEXT trading session.
    assert actual[0]["pnl_usd"]==150


def test_null_high_winrate_with_zero_trades_and_1000_control():
    assert summarize([],["2026-10-06"],"trend_reclaim","all_inspected")["win_rate_pct"] is None
    assert cash_summary([],1000)["ending_equity_usd"]==1000
