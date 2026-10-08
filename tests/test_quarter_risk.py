"""25%-per-order and 100%-per-day budget regression tests."""
from datetime import datetime, timedelta, timezone

import pytest

from engine.backtest import BacktestContext, PositionView, BacktestConfig, BacktestSignal, run_backtest
from engine.data import HistoricalFrame
from engine.market import MarketSnapshot, OptionQuote
from engine.quarter_risk import QuarterRiskConfig, QuarterRiskStrategy, nonoverlapping_weekly_returns


def _option(*, right="put", symbol="P", bid=1.00, ask=1.05, strike=700):
    return OptionQuote(
        symbol=symbol,right=right,strike=strike,bid=bid,ask=ask,
        delta=-0.45 if right=="put" else 0.45,gamma=0.02,theta=-1,vega=0.03,
        implied_volatility=.20,volume=1000,open_interest=2000,
        underlying_price=700,minutes_to_expiry=120,
    )


def _frame(minute=0, day=8, *, options=None):
    return HistoricalFrame(
        timestamp=datetime(2026,10,day,14,0,tzinfo=timezone.utc)+timedelta(minutes=minute),
        market=MarketSnapshot(
            spot=700,bid=699.99,ask=700.01,realized_volatility=.2,
            implied_volatility=.2,volume_ratio=1,minutes_to_close=300,
            data_age_seconds=.1,
        ),options=options if options is not None else (_option(),),
    )


def _context(equity,settled=None,position=None):
    return BacktestContext(
        equity=equity,settled_cash=equity if settled is None else settled,
        unsettled_cash=0.0,position=position,
    )


def test_per_trade_budget_uses_total_equity_not_max_contracts():
    s=QuarterRiskStrategy(starting_cash=1000)
    s.decide(_frame(),_context(1000))
    assert s._entry_budget(_context(1000))==pytest.approx(250)
    signal=s._entry_signal(_option(),250)
    assert signal.action=="open" and signal.quantity==2
    assert signal.max_total_cost==pytest.approx(250)
    # Even if the cash account has 900 settled, still only 250 per entry.
    assert s._entry_budget(_context(1000,settled=900))==pytest.approx(250)


def test_put_and_call_mode_use_real_quotes_not_reversed_call_prices():
    frame=_frame(options=(_option(),_option(right="call",symbol="C",bid=2,ask=2.1)))
    put=QuarterRiskStrategy(starting_cash=1000)
    call=QuarterRiskStrategy(starting_cash=1000,quarter_config=QuarterRiskConfig(side="call"))
    assert put._select_entry_option(frame,250).symbol=="P"
    assert call._select_entry_option(frame,250).symbol=="C"
    assert put._select_entry_option(frame,75) is None


def test_day_cap_counts_gross_filled_premiums_even_after_positions_closed():
    s=QuarterRiskStrategy(starting_cash=1000)
    s.decide(_frame(),_context(1000))
    for i in range(4):
        pos=PositionView("P",2,1.25,250,_frame(minute=1+i*2).timestamp)
        s.decide(_frame(minute=1+i*2),_context(1000,settled=750,position=pos))
        s.decide(_frame(minute=2+i*2),_context(1000,settled=750))
    assert s.filled_entry_count==4
    assert s.total_entry_premium==1000
    assert s._daily_premiums_spent==1000
    assert s.max_daily_spend_fraction==1
    assert s._entry_budget(_context(1000,settled=1000))==0
    s.decide(_frame(day=9),_context(900,settled=900))
    assert s._daily_premiums_spent==0
    assert s._entry_budget(_context(900,settled=900)) <=225


def test_day_cap_raises_if_a_broken_order_bypasses_execution_budget():
    s=QuarterRiskStrategy(starting_cash=1000)
    s.decide(_frame(),_context(1000))
    huge=PositionView("P",10,1.01,1010,_frame(minute=1).timestamp)
    with pytest.raises(ValueError,match="daily gross premium"):
        s.decide(_frame(minute=1),_context(1000,settled=0,position=huge))


def test_actual_next_frame_fill_will_not_exceed_25pct_if_price_gaps():
    class Trader:
        i=0
        def decide(self,frame,context):
            self.i+=1
            if self.i==1:return BacktestSignal("open","P",2,max_total_cost=250)
            if context.position:return BacktestSignal("close")
            return BacktestSignal("hold")
    frames=[
        _frame(minute=0),
        _frame(minute=1,options=(_option(bid=1.40,ask=1.45),)),
        _frame(minute=2),
    ]
    result=run_backtest(frames,Trader(),config=BacktestConfig(starting_cash=1000))
    assert result.metrics.trades==0
    assert result.metrics.ending_equity==1000


@pytest.mark.parametrize("kwargs",[
    dict(per_trade_equity_fraction=0),
    dict(per_trade_equity_fraction=1.1),
    dict(daily_gross_premium_fraction=0),
    dict(daily_gross_premium_fraction=1.1),
    dict(side="straddle"),
    dict(maximum_contracts=0),
])
def test_invalid_allocations_refused(kwargs):
    with pytest.raises(ValueError):
        QuarterRiskConfig(**kwargs)


def test_insufficient_budget_causes_no_exposure_not_fractional_contract():
    s=QuarterRiskStrategy(starting_cash=300)
    s.decide(_frame(),_context(300))
    budget=s._entry_budget(_context(300))
    assert budget==75
    assert s._select_entry_option(_frame(),budget) is None


def test_guarded_mode_cuts_losing_long_put_at_observed_bid():
    s=QuarterRiskStrategy(starting_cash=1000,quarter_config=QuarterRiskConfig(guard_enabled=True))
    s.decide(_frame(minute=0),_context(1000))
    position=PositionView("P",2,1.25,250,_frame(minute=1).timestamp)
    x=s.decide(
        _frame(minute=1,options=(_option(bid=.74,ask=.80),)),
        _context(900,settled=750,position=position),
    )
    assert x.action=="close"
    assert x.reason=="quarter_guard_premium_stop"
    assert s.stop_triggers==1


def test_guarded_mode_blocks_new_order_after_daily_or_weekly_realized_loss(monkeypatch):
    s=QuarterRiskStrategy(starting_cash=1000,quarter_config=QuarterRiskConfig(guard_enabled=True))
    s.decide(_frame(minute=0),_context(1000))
    import engine.adaptive_vault as av
    original=av.RocketVaultEventAlphaStrategy.decide
    from engine.backtest import BacktestSignal
    monkeypatch.setattr(av.RocketVaultEventAlphaStrategy,"decide",lambda *_: BacktestSignal("open","P",1,max_total_cost=200))
    try:
        day_paused=s.decide(_frame(minute=1),_context(890))
        assert day_paused.action=="hold"
        assert s.entry_pauses==1
        # No pause after a small realized loss.
        ok=s.decide(_frame(minute=2),_context(950))
        assert ok.action=="open"
        # Week starts with 1000, so a 16% loss at next day's opening
        # still prevents entries even though new session's day floor resets.
        next_day=s.decide(_frame(day=9),_context(840))
        assert next_day.action=="hold"
        assert s.entry_pauses==2
    finally:
        monkeypatch.setattr(av.RocketVaultEventAlphaStrategy,"decide",original)


def test_invalid_loss_guard_parameters():
    for name in ("stop_loss_of_paid_premium","daily_loss_pause_fraction","weekly_loss_pause_fraction"):
        with pytest.raises(ValueError):
            QuarterRiskConfig(**{name:0})
