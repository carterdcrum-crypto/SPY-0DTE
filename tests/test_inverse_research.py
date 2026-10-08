"""Same-market-signal, opposite-option-direction research tests."""
from datetime import datetime, timezone
from dataclasses import replace

from engine.backtest import BacktestContext
from engine.data import HistoricalFrame
from engine.inverse_research import InvertedAdaptiveVaultStrategy, _matched_put
from engine.market import MarketSnapshot, OptionQuote


def option(symbol, right, bid, ask, delta=-0.55, strike=700.0):
    return OptionQuote(
        symbol=symbol, right=right, strike=strike,
        bid=bid, ask=ask, delta=delta, gamma=0.02, theta=-1.0,
        vega=0.03, implied_volatility=0.20, volume=1000,
        open_interest=3000, underlying_price=700.0, minutes_to_expiry=120,
    )


def test_same_strike_put_is_selected_without_fake_inverse_prices():
    call=option("C700","call",1.0,1.10,0.45)
    put=option("P700","put",1.30,1.40,-0.55)
    other=option("P701","put",1.2,1.25,-0.45,strike=701)
    assert _matched_put(call,(other,put,call)) is put
    assert _matched_put(call,(other,call)) is None


def test_inverse_signal_buys_put_and_obeys_its_actual_spread_and_budget():
    call=option("C700","call",1.0,1.10,0.45)
    put=option("P700","put",1.30,1.40,-0.55)
    strategy=InvertedAdaptiveVaultStrategy(starting_cash=300)
    strategy._current_options=(call,put)
    signal=strategy._entry_signal(call,270.0)
    assert signal.action=="open"
    assert signal.option_symbol=="P700"
    assert signal.quantity==1
    assert signal.max_total_cost==270.0
    assert signal.reason.endswith("ROCKET_VAULT")
    too_expensive=strategy._entry_signal(call,100)
    assert too_expensive.action=="hold"
    strategy._current_options=(call,replace(put,bid=0.5,ask=1.4))
    assert strategy._entry_signal(call,270).action=="hold"
    assert strategy.inverse_entries_without_put==2


def test_unchanged_call_only_parent_does_not_enter_put_without_call():
    from engine.event_alpha import _select_call, EventAlphaConfig
    put=option("P700","put",1.3,1.4)
    frame=HistoricalFrame(
        timestamp=datetime(2026,9,9,14,tzinfo=timezone.utc),
        market=MarketSnapshot(
            spot=700,bid=699.99,ask=700.01,
            realized_volatility=0.2,implied_volatility=0.2,
            volume_ratio=1,minutes_to_close=300,data_age_seconds=0.1,
        ),
        options=(put,),
    )
    assert _select_call(frame,settled_cash=1000,config=EventAlphaConfig()) is None


def test_exact_inverse_uses_real_put_entry_and_exit_quotes(monkeypatch):
    """The opposite of a losing call is NOT the negative of call P&L."""
    from datetime import timedelta
    from engine.backtest import BacktestConfig, BacktestSignal, run_backtest
    from engine.inverse_research import replay_original_trades_as_puts
    import engine.inverse_research as inv

    base=datetime(2026,9,9,14,tzinfo=timezone.utc)
    market=MarketSnapshot(
        spot=700,bid=699.99,ask=700.01,
        realized_volatility=.2,implied_volatility=.2,
        volume_ratio=1,minutes_to_close=300,data_age_seconds=.1,
    )
    call=option("C700","call",1.0,1.05,0.45)
    put=option("P700","put",1.95,2.00,-.55)
    frames=[
        HistoricalFrame(base,market,(call,put)),
        HistoricalFrame(base+timedelta(minutes=1),market,(call,put)),
        HistoricalFrame(base+timedelta(minutes=2),market,(
            replace(call,bid=.75,ask=.80),replace(put,bid=3,ask=3.1),
        )),
    ]

    class TradeOnce:
        def __init__(self): self.n=0
        def decide(self,frame,context):
            self.n+=1
            if self.n==1:
                return BacktestSignal("open","C700",1)
            if self.n==2:
                return BacktestSignal("close")
            return BacktestSignal("hold")

    baseline=run_backtest(frames,TradeOnce(),config=BacktestConfig(
        starting_cash=300,fee_per_contract=0,slippage_spread_fraction=0,
        adverse_ticks_per_side=0,
    ))
    assert len(baseline.trades)==1
    assert baseline.metrics.ending_equity==270
    monkeypatch.setattr(inv,"iter_research_directory",lambda *args,**kwargs: iter(frames))
    mirror=replay_original_trades_as_puts(
        "/ignored",baseline,starting_cash=300,adverse_ticks_per_side=0,
    )
    assert mirror.original_trades==mirror.mirrored_trades==1
    assert mirror.missing_put_quote_pairs==0
    assert mirror.ending_cash_equity==400  # actual put premium +100, not merely +30

    costly_frames=[
        frames[0],replace(frames[1],options=(
            call,replace(put,bid=4.95,ask=5.0),
        )),frames[2],
    ]
    monkeypatch.setattr(inv,"iter_research_directory",lambda *args,**kwargs: iter(costly_frames))
    blocked=replay_original_trades_as_puts(
        "/ignored",baseline,starting_cash=300,adverse_ticks_per_side=0,
    )
    assert blocked.unaffordable_put_trades==1
    assert blocked.ending_cash_equity==300
