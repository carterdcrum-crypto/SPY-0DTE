"""Tests for causal, genuine-print SPY tape gate and risk-preserving entries."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from engine.tape_reader import (
    TapePrint,TapeReader,TapeConfig,TapeAwareQuarterStrategy,
    iter_spy_stock_tape,run_tape_research_directory,
)
from engine.backtest import BacktestContext
from engine.data import HistoricalFrame
from engine.market import MarketSnapshot,OptionQuote


T=datetime(2026,10,8,14,31,tzinfo=timezone.utc)


def prints(*,direction="buy",absorb=False,last_age=0,unknown=False):
    events=[]
    # 15 executions (one every ~1.5 seconds) in a 30-second lookback.
    for i in range(15):
        price=700 if absorb else (700+i*.025 if direction=="buy" else 700-i*.025)
        now=T-timedelta(seconds=21-i*1.5+last_age)
        events.append(TapePrint(
            event_time=now-timedelta(milliseconds=10),
            observed_at=now,
            price=price,shares=150,aggressor="unknown" if unknown else direction,
            bid=price-.01,ask=price+.01,
        ))
    return events


def quote(side):
    return OptionQuote(
        symbol="SPY_C" if side=="call" else "SPY_P",
        right=side,strike=700,bid=1,ask=1.05,
        delta=.45 if side=="call" else -.45,gamma=.02,theta=-1,
        vega=.03,implied_volatility=.20,volume=1000,
        open_interest=2000,underlying_price=700,minutes_to_expiry=120,
    )


def frame():
    return HistoricalFrame(
        timestamp=T,
        market=MarketSnapshot(
            spot=700,bid=699.99,ask=700.01,realized_volatility=.20,
            implied_volatility=.20,volume_ratio=1,minutes_to_close=300,
            data_age_seconds=.1,
        ),
        options=(quote("call"),quote("put")),
    )


def account():
    return BacktestContext(settled_cash=1000,unsettled_cash=0,equity=1000,position=None)


@pytest.mark.parametrize("side,want", [("buy","call"),("sell","put")])
def test_tape_confirms_buyer_vs_seller_flow_only_when_price_follows(side,want):
    reader=TapeReader()
    for item in prints(direction=side):
        reader.ingest(item)
    snapshot=reader.snapshot(T)
    assert snapshot.direction==want
    assert snapshot.reason=="confirmed"
    assert snapshot.total_shares==2250
    assert abs(snapshot.signed_imbalance)==1
    assert abs(snapshot.price_move_bps)>1


def test_tape_absorption_is_no_trade_not_reverse_it():
    reader=TapeReader()
    for item in prints(absorb=True):
        reader.ingest(item)
    snap=reader.snapshot(T)
    assert snap.reason=="possible_absorption" and snap.direction is None


def test_tape_no_trade_when_stale_ambiguous_or_latency_is_high():
    reader=TapeReader()
    for item in prints(last_age=10):
        reader.ingest(item)
    assert reader.snapshot(T).reason=="stale"
    r2=TapeReader()
    for item in prints(unknown=True):
        r2.ingest(item)
    assert r2.snapshot(T).reason=="mixed_flow"
    late=TapeReader()
    last=prints()
    for item in last:
        late.ingest(TapePrint(
            event_time=item.event_time-timedelta(seconds=4),
            observed_at=item.observed_at,
            price=item.price,shares=item.shares,aggressor=item.aggressor,
            bid=item.bid,ask=item.ask,
        ))
    assert late.snapshot(T).reason=="late_feed"


def test_tape_unseen_future_prints_do_not_count():
    r=TapeReader()
    for item in prints():
        r.ingest(item)
    assert r.snapshot(T-timedelta(seconds=22)).reason=="no_tape"


def test_tape_rejects_out_of_order_and_future_observation():
    events=prints()
    r=TapeReader()
    r.ingest(events[-1])
    with pytest.raises(ValueError,match="chronological"):
        r.ingest(events[0])
    with pytest.raises(ValueError,match="before"):
        TapePrint(T,T-timedelta(seconds=1),700,100,"buy")


def test_stock_tape_sidecar_rejects_aggregated_bars_and_opra(tmp_path:Path):
    file=tmp_path/"spy_tape_2026-10-08.csv"
    file.write_text(
        "event_time,observed_at,price,shares,aggressor,bid,ask,dataset,schema,symbol\n"
        f"{T.isoformat()},{T.isoformat()},700,100,B,699.99,700.01,EQUS.MINI,ohlcv-1m,SPY\n"
    )
    with pytest.raises(ValueError,match="individual trades"):
        list(iter_spy_stock_tape(tmp_path))
    file.write_text(
        "event_time,observed_at,price,shares,aggressor,bid,ask,dataset,schema,symbol\n"
        f"{T.isoformat()},{T.isoformat()},700,100,N,699.99,700.01,OPRA.PILLAR,tcbbo,SPY\n"
    )
    with pytest.raises(ValueError,match="equities venue"):
        list(iter_spy_stock_tape(tmp_path))


def test_stock_tape_sidecar_classifies_only_with_pretrade_nbbo(tmp_path:Path):
    file=tmp_path/"spy_tape_2026-10-08.csv"
    file.write_text(
        "event_time,observed_at,price,shares,aggressor,bid,ask,dataset,schema,symbol\n"
        f"{T.isoformat()},{T.isoformat()},700.01,100,N,699.99,700.01,EQUS.MINI,tbbo,SPY\n"
    )
    tape=list(iter_spy_stock_tape(tmp_path))
    assert len(tape)==1 and tape[0].aggressor=="buy"


def test_tape_aware_strategy_opens_neither_side_without_genuine_tape():
    strategy=TapeAwareQuarterStrategy(starting_cash=1000,tape_events=())
    assert strategy.decide(frame(),account()).action=="hold"
    assert strategy.last_tape_snapshot.reason=="no_tape"
    assert strategy._select_entry_option(frame(),250) is None


@pytest.mark.parametrize("side",["buy","sell"])
def test_tape_aware_strategy_routes_direction_to_real_call_or_put_and_keeps_quarter_cap(side):
    s=TapeAwareQuarterStrategy(starting_cash=1000,tape_events=prints(direction=side))
    s.decide(frame(),account())
    wanted="call" if side=="buy" else "put"
    assert s.last_tape_snapshot.direction==wanted
    chosen=s._select_entry_option(frame(),250)
    assert chosen is not None and chosen.right==wanted
    signal=s._entry_signal(chosen,250)
    assert signal.action=="open"
    assert signal.max_total_cost==250
    assert signal.quantity==2
    assert wanted.upper() in signal.reason


def test_missing_tape_data_explicitly_fails_backtest_no_fabricated_performance(tmp_path):
    with pytest.raises(RuntimeError,match="NO_TAPE_DATA"):
        run_tape_research_directory(tmp_path,starting_cash=1000)
