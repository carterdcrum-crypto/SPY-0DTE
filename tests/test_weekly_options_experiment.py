"""Synthetic unit fixtures exercise the EXECUTION ENGINE, not market profits.

No fixture P&L is described as an empirical SPY options backtest.
"""
from __future__ import annotations

import csv
import json
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from engine.weekly_options_data import (
    NY, Bar, Quote, SessionData, available_sessions, audit, load_session,
)
from engine.weekly_options_experiment import (
    ExperimentConfig, _opening_range, _candidate, _select_quote,
    run_simulation,
)
from engine.weekly_options_cli import (
    CANDIDATES, _split, weekly_table, block_bootstrap,
    run_development, run_holdout, full_session_calendar,
)


DAY=date(2026,10,8)


def at(day=DAY, hour=9, minute=30, second=0):
    return datetime.combine(day,time(hour,minute,second),NY).astimezone(timezone.utc)


def occ(day=DAY,right="call"):
    return "SPY"+day.strftime("%y%m%d")+("C" if right=="call" else "P")+"00700000"


def bar(day=DAY,i=0,*,close=700.00,vol=1000,high=None,low=None,op=None):
    return Bar(at(day,9,30)+timedelta(minutes=i),
               close if op is None else op,
               close+.03 if high is None else high,
               close-.03 if low is None else low,close,vol)


def quote(day=DAY,ts=None,right="call",bid=.95,ask=1.00,
          delta=None,bid_size=30,ask_size=30,expiry=None,symbol=None):
    return Quote(
        observed_at=ts or at(day,9,45,55),
        symbol=symbol or occ(day,right),
        expiry=expiry or day,right=right,strike=700,
        bid=bid,ask=ask,delta=(.5 if right=="call" else -.5) if delta is None else delta,
        bid_size=bid_size,ask_size=ask_size,volume=100,open_interest=200,
    )


def case(day=DAY, *, breakout=True, with_exit=True):
    bars=[bar(day,i,close=700) for i in range(15)]
    if breakout:
        bars.append(bar(day,15,close=700.12,high=700.15,low=700.01,op=700.01))
    else:
        bars.append(bar(day,15,close=700.00))
    qs=[
        quote(day,at(day,9,45,55)),
        quote(day,at(day,9,46,5),bid=.97,ask=1.02),
    ]
    if with_exit:
        qs.extend((
            quote(day,at(day,9,47,0),bid=1.80,ask=1.85),
            quote(day,at(day,9,47,5),bid=1.75,ask=1.82),
        ))
    return SessionData(day,tuple(bars),tuple(qs),at(day,16,0))


def test_early_close_and_daylight_saving_verification():
    from engine.session_calendar import session_close
    assert session_close(date(2026,11,27))==time(13,0)
    assert at(date(2026,3,9),9,30).hour==13  # EDT
    assert at(date(2026,11,30),9,30).hour==14 # EST
    assert session_close(date(2026,11,26)) is None


def test_opening_range_needs_all_15_completed_bars():
    initial=[bar(i=i) for i in range(15)]
    assert _opening_range(initial,DAY)==pytest.approx((700.03,699.97))
    assert _opening_range(initial[:-1],DAY) is None
    assert _opening_range(initial[1:],DAY) is None


def test_orb_signal_cannot_peek_future_quote_and_no_intrabar_stop_fill():
    cfg=ExperimentConfig(strategy="orb_simple",risk_fraction=.02)
    original=case()
    run=run_simulation((original,),starting_cash=10000,cfg=cfg)
    assert run.completed_trades==1
    assert run.orders_attempted==2 and run.order_fills==2
    assert run.canceled_orders==0
    trade=run.trade_ledger[0]
    assert trade.entry_at==at(DAY,9,46,5).isoformat() # next observed quote after bar close
    assert trade.entry_quote_at==trade.entry_at
    assert trade.exit_at==at(DAY,9,47,5).isoformat() # next after stop/target trigger
    assert trade.entry_ask_fill==pytest.approx(1.03) # ask 1.02 + 1 adverse tick
    assert trade.exit_bid_fill==pytest.approx(1.74) # bid 1.75 - 1 adverse tick
    expected_pnl=(1.74-1.03)*100-2*.68
    assert trade.realized_pnl==pytest.approx(expected_pnl)
    assert run.ending_equity==pytest.approx(10000+expected_pnl)
    assert run.fees_paid==pytest.approx(1.36)
    assert run.daily_equity[-1].unsettled_proceeds>0
    assert run.daily_equity[-1].equity_reconciles
    assert all(mark.reconciles for mark in run.intraday_marks)
    assert run.intraday_max_drawdown>=0

    # No quote at/before the signal means cannot select a contract from
    # future information even when the same forward fill exists.
    future_only=replace(original,quotes=tuple(q for q in original.quotes
                                                if q.observed_at>at(DAY,9,46)))
    none=run_simulation((future_only,),starting_cash=10000,cfg=cfg)
    assert none.completed_trades==0
    assert none.orders_attempted==0


def test_unfilled_close_is_zero_recovery_and_no_fictional_exit_fee():
    cfg=ExperimentConfig(strategy="orb_simple",risk_fraction=.02)
    run=run_simulation((case(with_exit=False),),starting_cash=10000,cfg=cfg)
    assert run.completed_trades==1
    t=run.trade_ledger[0]
    assert t.exit_quote_at is None
    assert t.exit_bid_fill==0.0
    assert t.exit_reason=="no_executable_close_quote_zero_recovery"
    assert t.total_fees==pytest.approx(.68)
    assert run.fees_paid==pytest.approx(.68)
    assert t.realized_pnl==-t.entry_debit
    assert run.forced_worthless_exits==1
    assert run.missing_quote_marks==1
    assert run.ending_equity==pytest.approx(10000-t.entry_debit)


def test_capped_small_cash_account_refuses_fractional_0dte_options():
    result=run_simulation((case(),),starting_cash=300,
        cfg=ExperimentConfig(strategy="orb_simple",risk_fraction=.02))
    assert result.orders_attempted==result.order_fills==result.completed_trades==0
    assert result.ending_equity==300


def test_next_observed_quote_must_respect_debit_budget_and_time_limit():
    base=case()
    # Quote jumps after the signal -> reject, rather than overspend 2%.
    qs=list(base.quotes)
    qs[1]=replace(qs[1],bid=4.90,ask=5.00)
    gap=run_simulation((replace(base,quotes=tuple(qs)),),starting_cash=10000,
                      cfg=ExperimentConfig(strategy="orb_simple",risk_fraction=.02))
    assert gap.orders_attempted>=1
    assert gap.order_fills==0
    assert gap.canceled_orders>=1
    assert gap.ending_equity==10000
    # A quote after 90-sec wait is not eligible for retroactive entry.
    qs=[base.quotes[0],replace(base.quotes[1],observed_at=at(DAY,9,48,0))]
    late=run_simulation((replace(base,quotes=tuple(qs)),),starting_cash=10000,
        cfg=ExperimentConfig(strategy="orb_simple",risk_fraction=.02))
    assert late.order_fills==0 and late.canceled_orders>=1


def test_quote_selector_filters_same_day_expiry_delta_liquidity_and_determinism():
    cfg=ExperimentConfig()
    now=at(DAY,9,46)
    good=quote(ts=at(DAY,9,45,50))
    bad=quote(ts=at(DAY,9,45,51),delta=.2,right="put")
    selected=_select_quote({good.symbol:good,bad.symbol:bad},now,"call",cfg,700)
    assert selected==good
    assert _select_quote({bad.symbol:bad},now,"call",cfg,700) is None
    assert _select_quote({good.symbol:replace(good,ask_size=0)},now,"call",cfg,700) is None
    assert _select_quote({good.symbol:replace(good,observed_at=at(DAY,9,40))},now,"call",cfg,700) is None
    with pytest.raises(ValueError,match="mismatch"):
        replace(good,expiry=date(2026,10,9))


def test_date_scoped_io_requires_real_ohlcv_and_explicit_observed_option_quotes(tmp_path:Path):
    assert not audit(tmp_path)["usable_for_this_experiment"]
    day=str(DAY)
    b=tmp_path/f"spy_bars_{day}.csv"
    q=tmp_path/f"spy_option_quotes_{day}.csv"
    b.write_text("ts_start,open,high,low,close,volume\n"
                 f"{at().isoformat()},700,701,699,700.5,1400\n")
    q.write_text("observed_at,symbol,expiration,right,strike,bid,ask,delta,bid_size,ask_size,volume,open_interest,schema\n"
                 f"{at(DAY,9,31).isoformat()},{occ()},{day},call,700,.95,1,.5,5,7,100,200,cbbo-1m\n")
    with pytest.raises(ValueError,match="CBBO-1m"):
        load_session(tmp_path,DAY)
    q.write_text(q.read_text().replace("cbbo-1m","cbbo-1s"))
    s=load_session(tmp_path,DAY)
    assert s.bars[0].available_at==at(DAY,9,31)
    assert s.quotes[0].expiry==DAY
    assert audit(tmp_path)["usable_for_this_experiment"]


def test_no_skipped_trading_sessions_and_no_cherrypicked_dates():
    with pytest.raises(ValueError,match="MISSING_SESSION"):
        full_session_calendar((date(2026,10,5),date(2026,10,8)))


def test_walk_forward_embargo_holdout_nonintersection_and_not_enough_samples():
    days=[date(2026,9,1)+timedelta(days=i) for i in range(35)]
    folds,hold=_split(days,train=8,val=5,test=3,holdout=5,purge=1)
    used=set()
    for training,val,test in folds:
        assert max(training)<min(val)<max(val)<min(test)<max(test)<min(hold)
        assert not(set(training)&set(val)) and not(set(val)&set(test))
        used.update(test)
    assert not used.intersection(hold)
    with pytest.raises(ValueError,match="insufficient"):
        _split(days[:7],train=8,val=5,test=3,holdout=5,purge=1)


def test_iso_week_includes_no_trade_days_and_bootstrap_is_reproducible():
    sessions=(case(date(2026,10,8),breakout=False),
              case(date(2026,10,9),breakout=False))
    result=run_simulation(sessions,starting_cash=10000,cfg=ExperimentConfig())
    weeks=weekly_table(result)
    assert len(weeks)==1
    assert weeks[0]["sessions"]==2
    assert weeks[0]["account_return"]==0
    assert weeks[0]["closed_trades_in_week"]==0
    assert block_bootstrap([-.1,.2,0,.03],seed=5)==block_bootstrap([-.1,.2,0,.03],seed=5)


def test_rvol_uses_prior_completed_dates_not_same_day_volume():
    bars=[bar(i=i) for i in range(15)]+[
        bar(i=15,close=700.12,high=700.15,low=700.01,op=700.01,vol=999999),
    ]
    range_=_opening_range(bars[:15],DAY)
    assert _candidate("orb_filtered",bars,range_,700.01,700.0,{},1.25) is None
    # Prior minute-of-day volume makes positive confirmation eligible.
    key=9*60+45
    assert _candidate("orb_filtered",bars,range_,700.01,700.0,{key:[500,600,550]},1.25) is not None


def test_all_parameters_declared_once_no_massive_overfit_grid():
    assert len(CANDIDATES)==9
    assert {(c.strategy,c.risk_fraction) for c in CANDIDATES}=={
        (s,r) for s in ("orb_simple","orb_filtered","vwap_reclaim") for r in (.005,.01,.02)
    }


def test_holdout_requires_prior_freeze_even_when_files_exist(tmp_path:Path):
    from types import SimpleNamespace
    args=SimpleNamespace(cash=10000)
    with pytest.raises(FileNotFoundError,match="development"):
        run_holdout(tmp_path,tmp_path,[],args)
