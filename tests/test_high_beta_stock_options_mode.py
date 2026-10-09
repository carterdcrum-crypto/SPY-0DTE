"""Strict causal stock optionability/RVOL/ATR/beta and quote-based P&L tests.

All fixtures are SYNTHETIC TESTS ONLY; not historical winning strategies.
"""
from __future__ import annotations

import csv
import json
from dataclasses import replace
from datetime import date,datetime,time,timedelta
from zoneinfo import ZoneInfo

import pytest

from engine.high_beta_stock_screen import (
    StockMinute, StockSignal, ScreenConfig,
    calc_prior_atr14,calc_prior_beta252,screen,
    read_stock_minutes,signals_by_symbol_day,screening_audit,
)
from engine.high_beta_options_compare import (
    NBBO,read_event_options,select_entry_quote,select_exit_quote,
    simulate,compare_modes,main as compare_main,
)
from engine.weighted_daily_coverage import Opportunity

ET=ZoneInfo("America/New_York")


def minute(ts:datetime,*,symbol="HIGH",close=50.,cum=2_000_000,
           prior_mean=1_000_000,atr=2.5,beta=2.,optionable=True,
           features_day=date(2026,10,8),minute_volume=30000):
    return StockMinute(
        timestamp=ts,symbol=symbol,close=close,
        minute_volume=minute_volume,cumulative_volume=cum,
        prior20_mean_cumvol_same_clock=prior_mean,
        prior20_mean_minutevol_same_clock=20000.,
        atr14_prev_session_dollars=atr,beta252_prev_session=beta,
        optionable_asof=optionable,metrics_last_session=features_day,
        history_source="synthetic unit test only",
    )


def op(day=date(2026,10,9),direction="call",score=6):
    ts=datetime.combine(day,time(10,40),ET)
    return Opportunity(
        day=day.isoformat(),setup="close_breakout_baseline",
        original_direction=direction,
        signal_at=ts.isoformat(),
        entry_proxy_at=(ts+timedelta(minutes=1)).isoformat(),
        exit_proxy_at=(ts+timedelta(minutes=11)).isoformat(),
        rsi14=65.,sma20=49.5,sma50=49.,
        macd=.3,macd_signal=.2,
        volume_ratio_at_signal=1.5,
        recent_macd_crossover_points=2,
        rsi_points=2,sma_points=2,
        price_action_points=0,volume_proxy_points=0,
        weighted_total=score,
        signed_spy_ten_minute_close_change_bps=-1000.,  # corrupt future label!
    )


def stocksignal(symbol="HIGH",spot=50.,day=date(2026,10,9),side="call",score=6):
    return StockSignal(
        symbol=symbol,underlying_spot_at_signal=spot,
        observed_rvol_asof=2.0,
        observed_cumulative_volume=2_000_000,
        observed_atr14_prev_session_dollars=2.5,
        observed_beta252_prev_session=2.0,
        opportunity=op(day,side,score),
    )


def nbbo(symbol="HIGH",expiry=date(2026,10,16),strike=50.,
         side="call",bid=1.75,ask=1.85,observed=None,
         source="nbbo-event",size=5):
    if observed is None:
        observed=datetime(2026,10,9,10,40,2,tzinfo=ET)
    occ=f"{symbol}{expiry.strftime('%y%m%d')}{'C' if side=='call' else 'P'}{round(strike*1000):08d}"
    return NBBO(observed_at=observed,underlying=symbol,option_symbol=occ,
                expiration=expiry,right=side,strike=strike,bid=bid,ask=ask,
                bid_size=size,ask_size=size,source=source)


def test_strict_stock_filters_asof_only_and_benchmark_beta_signed():
    t=datetime(2026,10,9,10,40,tzinfo=ET)
    x=minute(t)
    assert screen(x)
    assert x.rvol_asof==2.
    assert x.minute_rvol_asof==1.5
    assert not screen(replace(x,prior20_mean_cumvol_same_clock=2_000_000))
    assert not screen(replace(x,atr14_prev_session_dollars=1.0))
    assert not screen(replace(x,cumulative_volume=1_000_000))
    assert not screen(replace(x,close=30.))
    assert not screen(replace(x,beta252_prev_session=1.5))
    assert not screen(replace(x,optionable_asof=False))
    assert not screen(replace(x,beta252_prev_session=-2.))
    with pytest.raises(ValueError,match="current or future"):
        minute(t,features_day=date(2026,10,9))
    with pytest.raises(ValueError,match="denominators"):
        minute(t,prior_mean=0.)


def test_atr_uses_prior_close_gaps_and_beta_uses_aligned_daily_returns():
    days=[(51.,49.,50.)]*15
    assert calc_prior_atr14(days)==pytest.approx(2.)
    gap=[(51.,49.,50.)]+[(54.,51.,52.)]*14
    # First TR of the last 14 previous bars captures overnight gap:
    assert calc_prior_atr14(gap)>2.
    spy=[(-.01 if i%2 else .02) for i in range(252)]
    stock=[2.0*i for i in spy]
    assert calc_prior_beta252(stock,spy)==pytest.approx(2.)
    with pytest.raises(ValueError,match="completed daily"):
        calc_prior_beta252(stock[:200],spy)


def test_declared_volume_is_cumulative_by_signal_minute_not_end_of_day():
    day=date(2026,10,9)
    first=datetime.combine(day,time(9,30),ET)
    rows=[]
    for i in range(145):
        px=50.+(.001 if i%2 else 0.)
        if i==90:px=50.9
        if 91<=i<=110:px=50.9+(i-90)*.01
        if i>=111:px=51.1
        # Signal at i=90 (11:00 ET); cumulative volume crosses
        # 1M only afterward: there must be NO early screened signal.
        cum=500_000 if i<=90 else 2_500_000
        rows.append(minute(first+timedelta(minutes=i),close=px,cum=cum))
    raw=signals_by_symbol_day(rows,apply_high_beta_screen=False)
    filtered=signals_by_symbol_day(rows)
    assert raw
    earlier={x.opportunity.signal_at for x in raw if
             datetime.fromisoformat(x.opportunity.signal_at)<=
             first+timedelta(minutes=91)}
    assert earlier
    assert all(x.opportunity.signal_at not in earlier for x in filtered)
    audit=screening_audit(rows)
    assert audit["observed_trading_sessions"]==1
    assert audit["coverage_by_day"][day.isoformat()]["symbols_ever_passed_AS_OF_that_minute"]==1


def test_stock_csv_rejects_data_leakage_provenance_and_declining_shares(tmp_path):
    path=tmp_path/"stock_minutes_2026-10-09.csv"
    a=minute(datetime(2026,10,9,10,40,tzinfo=ET))
    columns={
        "ts_start":a.timestamp.isoformat(),"symbol":a.symbol,
        "close":a.close,"minute_volume":a.minute_volume,
        "cumulative_volume":a.cumulative_volume,
        "prior20_mean_cumvol_same_clock":a.prior20_mean_cumvol_same_clock,
        "prior20_mean_minutevol_same_clock":a.prior20_mean_minutevol_same_clock,
        "atr14_prev_session_dollars":a.atr14_prev_session_dollars,
        "beta252_prev_session":a.beta252_prev_session,
        "optionable_asof":"true","metrics_last_session":a.metrics_last_session.isoformat(),
        "history_source":"synthetic test not vendor data",
    }
    with path.open("w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=list(columns))
        w.writeheader()
        w.writerow(columns)
    assert len(read_stock_minutes([path]))==1
    with path.open("a",newline="") as f:
        csv.DictWriter(f,fieldnames=list(columns)).writerow({
            **columns,"ts_start":"2026-10-09T10:41:00-04:00",
            "cumulative_volume":1000000,
        })
    with pytest.raises(ValueError,match="declining"):
        read_stock_minutes([path])


def test_options_require_true_observed_nbbo_and_occ_contract_identity():
    good=nbbo()
    assert good.spread_fraction>0
    with pytest.raises(ValueError,match="synthetic"):
        nbbo(source="minute-snapshot")
    with pytest.raises(ValueError,match="mismatch"):
        replace(good,strike=51.)
    with pytest.raises(ValueError,match="real OCC"):
        replace(good,underlying="SPY")
    with pytest.raises(ValueError,match="quote must have timezone"):
        replace(good,observed_at=good.observed_at.replace(tzinfo=None))
    with pytest.raises(ValueError,match="invalid NBBO"):
        replace(good,ask=0)


def test_true_entry_after_signal_no_peek_expiry_and_option_quote_exit():
    item=stocksignal()
    early=nbbo(observed=datetime(2026,10,9,10,39,59,tzinfo=ET),bid=8,ask=8.2)
    current=nbbo()
    late=nbbo(observed=datetime(2026,10,9,10,42,tzinfo=ET))
    assert select_entry_quote([early,current,late],item,
                              mode="HIGH_BETA_STOCK_OPTIONS")==current
    assert select_entry_quote([current],item,mode="SPY_0DTE") is None
    after_exit=nbbo(observed=datetime(2026,10,9,10,50,2,tzinfo=ET),bid=2.85,ask=3.0)
    assert select_exit_quote([current,after_exit],current)==after_exit
    assert select_exit_quote([current],current) is None
    wrong_side=nbbo(side="put")
    assert select_entry_quote([wrong_side],item,mode="HIGH_BETA_STOCK_OPTIONS") is None


def test_real_quote_bid_ask_profit_math_and_unresolved_quote_block_ranking():
    sample=stocksignal()
    entry=nbbo(ask=1.0,bid=.95)
    exit_=nbbo(observed=datetime(2026,10,9,10,50,2,tzinfo=ET),ask=1.55,bid=1.50)
    r=simulate(mode="HIGH_BETA_STOCK_OPTIONS",
        policy="weighted_score_ge4",raw_signals=[sample],
        option_quotes=[entry,exit_],common_days=["2026-10-09"],starting_cash=10000)
    assert r["observed_completed_option_roundtrips"]==1
    assert r["trades"][0].realized_pnl_usd==pytest.approx(
        (1.50-.01)*100-.68-((1.0+.01)*100+.68))
    assert r["days_profit_usd_positive"]==1
    assert r["stock_options_1_to_7_calendar_DTE_count"]==1
    assert r["net_return_on_INITIAL_SCENARIO_cash_percent"]>0
    assert not r["eligible_for_complete_head_to_head_ranking"]
    assert r["missing_exit_quote_signals"]==0
    bad=simulate(mode="HIGH_BETA_STOCK_OPTIONS",
        policy="weighted_score_ge4",raw_signals=[sample],
        option_quotes=[entry],common_days=["2026-10-09"],starting_cash=10000)
    assert bad["missing_exit_quote_signals"]==1
    assert not bad["quote_and_cash_coverage_complete"]
    assert not bad["trades"]
    assert bad["incomplete_unresolved_positions"]
    assert bad["days_profit_usd_zero"]==1


def test_low_capital_respects_long_option_debit_and_never_borrows():
    sample=stocksignal()
    data=[nbbo(ask=1,bid=.95),
          nbbo(observed=datetime(2026,10,9,10,50,2,tzinfo=ET),ask=2,bid=1.95)]
    r=simulate(mode="HIGH_BETA_STOCK_OPTIONS",policy="all_setups_nonoverlapping",
        raw_signals=[sample],option_quotes=data,common_days=["2026-10-09"],
        starting_cash=115)
    assert r["observed_completed_option_roundtrips"]==0
    assert r["unaffordable_one_contract_signals"]==1
    assert r["net_realized_quote_assumption_pnl_usd"]==0


def test_spy_0dte_and_stock_weekly_are_not_the_same_expiration():
    day=date(2026,10,9)
    sig=stocksignal(symbol="SPY",spot=700.,day=day)
    spyq=nbbo(symbol="SPY",expiry=day,strike=700.,bid=2.,ask=2.10)
    assert select_entry_quote([spyq],sig,mode="SPY_0DTE")==spyq
    wrong=nbbo(symbol="SPY",expiry=date(2026,10,16),strike=700.)
    assert select_entry_quote([wrong],sig,mode="SPY_0DTE") is None
    # Stock weekly next expiry accepted as separately reported 1-7d tenor:
    assert select_entry_quote([nbbo()],stocksignal(),mode="HIGH_BETA_STOCK_OPTIONS")


def test_readiness_fails_closed_without_market_or_option_data(tmp_path,capsys):
    out=tmp_path/"results"
    assert compare_main(["--data-dir",str(tmp_path),"--output-dir",str(out)])==0
    payload=json.loads((out/"readiness.json").read_text())
    assert payload["status"]=="BLOCKED_MISSING_HISTORICAL_REAL_MARKET_INPUTS"
    assert payload["historical_option_PnL"] is None
    assert payload["profitable_strategy"] is None
    assert len(payload["missing"])==2
    assert payload["live_order_changes"]==0


def test_tiny_manual_sample_cannot_claim_real_predictive_winner():
    # Point-in-time source minutes with no actual executable option data
    # MUST return no winner, not SPY price-direction proxy substituted as P&L.
    day=date(2026,10,9)
    start=datetime.combine(day,time(9,30),ET)
    all_bars=[]
    for i in range(90):
        t=start+timedelta(minutes=i)
        for sym,price in (("SPY",700.),("HIGH",50.)):
            all_bars.append(minute(t,symbol=sym,close=price))
    r=compare_modes(all_bars,())
    assert r["matched_calendar_dates"]==1
    for policy,result in r["policies"].items():
        assert result["true_predictive_winner"] is None
        assert result["higher_OBSERVED_historical_scenario_net_pnl_if_complete"] is None
        assert result["historical_comparison_status"]=="BLOCKED_INCOMPLETE_OR_UNOBSERVED_QUOTES"
