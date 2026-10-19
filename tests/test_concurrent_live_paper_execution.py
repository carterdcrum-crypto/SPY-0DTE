"""End-to-end simulated V5 buy/exit while primary mode remains LIVE.

This test cannot reach broker APIs or transmit real orders. Tests that PAPER
cash settles once, and the owner stopping new PAPER entries never touches LIVE.
"""
from datetime import datetime, timedelta, timezone, date
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from engine import api, api_v2
from engine.paper_account import PaperAccountStore
from engine.paper_autotrader import PaperAutoSettings, SnapshotReader
from engine.v5_close_only_paper import V5CloseOnlyPaperTrader
from engine.v4_operational import _rv
from engine.weekly_options_data import Bar


def test_concurrent_paper_entry_exit_and_settlement_with_live_mode(monkeypatch,tmp_path):
    monkeypatch.setenv("CONTROL_DB_PATH",str(tmp_path/"control.sqlite"))
    control=api.ControlStore(tmp_path/"control.sqlite")
    control.set_mode(api.TradingMode.LIVE)
    control.set_paper_armed(True)
    assert api_v2._strategy_mode()=="PAPER"
    paper=PaperAccountStore(tmp_path/"paper.sqlite",default_starting_cash=1000)
    start=datetime(2026,10,13,13,30,tzinfo=timezone.utc)
    bars=[]
    for i in range(72):
        close=500.+i*.14
        bars.append(Bar(start+timedelta(minutes=i),close-.03,
                        close+.04,close-.06,close,
                        60000 if i==70 else 15000))
    now=bars[-1].available_at+timedelta(seconds=1)
    rv=_rv(bars,len(bars)-3)
    option="SPY261013C00500000"
    def quote(at,bid=.45,ask=.46):
        return SimpleNamespace(
            source="webull_production",
            timestamp_quality="option_quote_timestamp",
            timestamp=at,received_at=at,underlying_timestamp=at,
            expiration=at.astimezone(ZoneInfo("America/New_York")).date(),
            option_symbol=option,right="call",strike=500.,
            bid=bid,ask=ask,bid_size=22,ask_size=22,
            underlying_price=510.,
            greeks=SimpleNamespace(delta=.5,implied_volatility=rv*.7),
        )

    trader=V5CloseOnlyPaperTrader(
        mode_getter=api_v2._strategy_mode,
        market_reader=SnapshotReader(tmp_path/"collector.sqlite"),
        account_store=paper,settings=PaperAutoSettings(),
        production_provider=None,bar_provider=None,
    )
    trader.bar_cache.bars=bars
    trader.bar_cache.error=None
    monkeypatch.setattr(trader.bar_cache,"refresh",lambda provider,at: bars)
    monkeypatch.setattr(trader,"_quotes",lambda at,position=None: (quote(at),))
    opened=trader.tick(now)
    assert opened["state"]=="POSITION_OPENED",opened
    assert opened["mode"]=="PAPER"
    assert opened["risk"]["live_broker_orders"] is False
    assert api._control_store().get_mode() is api.TradingMode.LIVE
    assert paper.snapshot().open_positions==1
    assert paper.snapshot().buy_count==1

    # Stop new PAPER trades while LIVE mode stays selected, yet manage and
    # exit already-owned simulated options as normal.
    control.set_paper_armed(False)
    assert api_v2._strategy_mode()=="SHADOW"
    later=now+timedelta(seconds=2)
    monkeypatch.setattr(trader,"_quotes",lambda at,position=None:
                        (quote(at,bid=.25,ask=.26),))
    closed=trader.tick(later)
    assert closed["state"]=="POSITION_CLOSED",closed
    assert closed["mode"]=="SHADOW"
    snap=paper.snapshot()
    assert snap.open_positions==0
    assert snap.buy_count==1
    assert snap.sell_count==1
    assert snap.realized_pnl<0
    assert snap.unsettled_cash>0
    assert api._control_store().get_mode() is api.TradingMode.LIVE
    before=snap.settled_cash+snap.unsettled_cash
    paper.settle_due(date(2026,10,14))
    after=paper.snapshot()
    assert after.unsettled_cash == pytest.approx(0)
    assert after.settled_cash == pytest.approx(before)
    assert after.realized_pnl == pytest.approx(snap.realized_pnl)
