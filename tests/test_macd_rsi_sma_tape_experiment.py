"""MACD/RSI/SMA plus close action / volume / real tape causal tests."""
from __future__ import annotations

from dataclasses import replace
from datetime import date,datetime,time,timedelta,timezone
from pathlib import Path
import csv

import pytest

from engine.data import HistoricalFrame
from engine.market import MarketSnapshot
from engine.macd_rsi_sma_tape_experiment import (
    TapeTrade,TAPE_POLICIES,POLICIES,_rsi_wilder,_ema_seeded,
    _macd,_cross_recent,_sma_at,read_verified_trade_tape,tape_at,
    session_opportunities,accepts,run_study,
)

ET=timezone(timedelta(hours=-4))


def session(day=date(2026,8,12),*,post_entry_reverse=False,gap=False):
    first=datetime.combine(day,time(9,30),ET)
    rows=[]
    for i in range(155):
        if i<96:
            # Flat/gently oscillating earlier minute closes: a clear
            # single close-based breakout at i=96.
            px=700+(.002 if i%2 else 0)
        elif i==96:
            px=700.60
        elif i<101:
            px=700.62
        elif post_entry_reverse and i>=103:
            px=699.50
        else:
            px=700.62+(i-100)*.003
        when=first+timedelta(minutes=i+(1 if gap and i>=98 else 0))
        rows.append(HistoricalFrame(timestamp=when,options=(),
            market=MarketSnapshot(spot=px,bid=px-.01,ask=px+.01,
                realized_volatility=.2,implied_volatility=.25,
                volume_ratio=1.9 if i==96 else 1.0,
                minutes_to_close=390-i)))
    return rows


def first_late(rows):
    a=session_opportunities(rows)
    return next(x for x in a if x.breakout_signal_at==(
        rows[96].timestamp+timedelta(minutes=1)).isoformat())


def test_indicators_are_causal_and_standard_wilder_and_seeded():
    x=[100.0]*30
    assert _rsi_wilder(x)[14]==50.
    assert _sma_at(x,20)[19]==100.
    assert _sma_at(x,50)[-1] is None
    increasing=list(map(float,range(1,120)))
    assert _rsi_wilder(increasing)[-1]==100.
    macd,signal=_macd(increasing)
    assert signal[33] is not None
    assert macd[-1]>0 and signal[-1]>0
    assert _ema_seeded([1.,2.,3.,4.,5.],3)[2]==2.


def test_macd_cross_detects_only_recent_confirmed_completed_bars():
    m=[None,None, -1.0, -0.5, -0.1,0.1,0.3,0.4,0.5,0.6,0.7]
    s=[None,None,  0.0,  0.0,  0.0,0.0,0.0,0.0,0.0,0.0,0.0]
    assert _cross_recent(m,s,7,True)
    assert not _cross_recent(m,s,10,True)
    assert not _cross_recent(m,s,7,False)


def test_future_prices_cannot_change_indicator_votes_and_tape_disabled_by_default():
    old=first_late(session())
    evil=first_late(session(post_entry_reverse=True))
    for attr in ("macd","macd_signal","macd_cross_aligned_recent",
                 "rsi14","sma20","sma50","indicator_confluence",
                 "close_price_action_confirmation","volume_ratio_at_original_breakout"):
        assert getattr(old,attr)==getattr(evil,attr)
    assert old.signed_10min_spy_close_move_bps>0
    assert evil.signed_10min_spy_close_move_bps<0
    assert old.tape_available is False
    assert old.tape_signed_imbalance is None
    for model in POLICIES:
        assert accepts(old,model)==accepts(evil,model)
    assert all(not accepts(old,x) for x in TAPE_POLICIES)


def test_tape_requires_REAL_trades_and_correct_aggressor_timestamp():
    rows=session()
    ev=first_late(rows)
    confirmation=datetime.fromisoformat(ev.confirmed_at)
    eligible=[
        TapeTrade(confirmation-timedelta(seconds=3*i),"B",100,700.62)
        for i in range(12)
    ]
    up=first_late(rows)  # independent baseline
    from engine.macd_rsi_sma_tape_experiment import tape_at
    tick=tape_at(eligible,confirmation)
    assert tick.available
    assert tick.trades==12 and tick.buy_size==1200
    assert tick.sell_size==0 and tick.signed_imbalance==1.
    future=[
        TapeTrade(confirmation+timedelta(minutes=1),"A",1_000_000,700.61)
        for _ in range(12)
    ]
    assert tape_at(eligible+future,confirmation)==tick
    assert not tape_at(None,confirmation).available
    assert not tape_at(eligible[:5],confirmation).available
    combined=session_opportunities(rows,tape=eligible+future)
    entry=next(e for e in combined if e.breakout_signal_at==ev.breakout_signal_at)
    assert entry.tape_available and entry.tape_direction_confirmed
    assert entry.tape_sell_size==0


def test_fixed_combinations_work_without_future_outcome_dependent_filters():
    o=first_late(session())
    rich=replace(o,indicator_confluence=True,
                 close_price_action_confirmation=True,
                 volume_ratio_at_original_breakout=2,
                 tape_available=True,tape_direction_confirmed=True)
    assert all(accepts(rich,p) for p in POLICIES)
    adverse=replace(rich,signed_10min_spy_close_move_bps=-100000)
    assert all(accepts(rich,p)==accepts(adverse,p) for p in POLICIES)
    assert accepts(replace(rich,tape_available=False),"macd_rsi_sma_price_action_volume_proxy")
    assert not accepts(replace(rich,tape_available=False),"full_fusion_true_tape_plus_volume")
    assert not accepts(replace(rich,volume_ratio_at_original_breakout=.1),
                       "full_fusion_true_tape_plus_volume")
    with pytest.raises(ValueError):
        accepts(rich,"data_mined_strategy")


def test_uninterrupted_minute_candles_required_for_macd_sma_rsi():
    broken=session(gap=True)
    events=session_opportunities(broken)
    assert not any(x.breakout_signal_at==(
        broken[96].timestamp+timedelta(minutes=1)).isoformat()
                   for x in events)


def test_real_tape_file_rejects_missing_provenance_or_unsigned_quotes(tmp_path):
    f=tmp_path/"trades.csv"
    with f.open("w",newline="") as out:
        w=csv.DictWriter(out,fieldnames=[
            "ts_event","symbol","action","side","size","price"])
        w.writeheader()
        w.writerow({"ts_event":"2026-08-12T10:59:00-04:00",
                    "symbol":"SPY","action":"T","side":"B",
                    "size":100,"price":700.})
    data=read_verified_trade_tape(f)
    assert len(data["2026-08-12"])==1
    text=f.read_text()
    f.write_text(text.replace(",T,B,",",Q,B,"))
    with pytest.raises(ValueError,match="aggressor TRADE"):
        read_verified_trade_tape(f)
    f.write_text(text.replace("-04:00",""))
    with pytest.raises(ValueError,match="timezone-aware"):
        read_verified_trade_tape(f)


def test_full_replay_flags_tape_as_not_available_and_all_pnl_null():
    day=date(2026,8,12)
    samples=[*session(day),*session(date(2026,9,10))]
    result=run_study(samples)
    assert result["sessions"]==2
    assert result["frames"]==310
    assert result["research_only"]
    assert not result["tape_actual_aggressor_source_supplied"]
    assert not result["continuation_adaptive_gate_enabled"]
    for period,data in result["periods"].items():
        for policy,metric in data.items():
            assert metric["option_pnl"] is None
            assert metric["account_max_drawdown"] is None
            assert metric["actual_option_fills"] is None
            assert metric["account_return"] is None
            if policy in TAPE_POLICIES:
                assert metric["status"]=="UNAVAILABLE_VERIFIED_SPY_TAPE"
                assert metric["signals_selected"] is None
    assert len(result["events"])>=2


def test_reject_nonchronological_sessions():
    with pytest.raises(ValueError,match="not chronological"):
        run_study([*session(date(2026,9,10)),*session(date(2026,8,12))])
