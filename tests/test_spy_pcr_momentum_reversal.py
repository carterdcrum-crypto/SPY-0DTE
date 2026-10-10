"""As-of put/call VOLUME ratio + momentum/reversal MACD strategy tests.

PCR from incomplete quote sampling is never fabricated or considered valid.
"""
from __future__ import annotations

import csv
from dataclasses import replace
from datetime import date,datetime,time,timedelta
from zoneinfo import ZoneInfo

import pytest

from engine.data import HistoricalFrame
from engine.market import MarketSnapshot
from engine.weighted_daily_coverage import Opportunity
from engine.spy_pcr_momentum_reversal import (
    PCRPoint,Classified,load_complete_spy_pcr,pcr_at,_confirmed_pcr,
    session_classifications,select,_stats,run_study,POLICIES,
)

ET=ZoneInfo("America/New_York")


def op(day="2026-08-12",side="call",family="close_breakout_baseline",
       at=time(11,0),signed=5.,score=6):
    ts=datetime.combine(date.fromisoformat(day),at,ET)
    return Opportunity(
        day=day,setup=family,original_direction=side,
        signal_at=ts.isoformat(),
        entry_proxy_at=(ts+timedelta(minutes=1)).isoformat(),
        exit_proxy_at=(ts+timedelta(minutes=11)).isoformat(),
        rsi14=50.,sma20=700.,sma50=699.,macd=.3,macd_signal=.2,
        volume_ratio_at_signal=1.5,
        recent_macd_crossover_points=2,rsi_points=2,sma_points=2,
        price_action_points=0,volume_proxy_points=0,weighted_total=score,
        signed_spy_ten_minute_close_change_bps=signed,
    )


def labeled(o,passed=True,pcr=None,pcr_ok=None):
    return Classified(
        opportunity=o,family_mode="reversal" if o.setup=="rolling_mean_reversal" else "momentum",
        factor_score=6,price_action_confirmed=True,
        macd_histo_aligned_or_improving=True,volume_confirmed=True,
        reversal_confirmed=o.setup=="rolling_mean_reversal",
        pcr_value=pcr,pcr_observed_at=None,
        pcr_confirms=pcr_ok,
        reasons=("base_four_factors_PASS" if passed else "base_four_factors_REJECT",),
    )


def pcr(day="2026-08-12",h=10,m=59,puts=1000,calls=800):
    return PCRPoint(
        datetime.combine(date.fromisoformat(day),time(h,m),ET),
        puts,calls,"actual historical OPRA full SPY option chain",
        "all_spy_listed_options",
    )


def history(day=date(2026,8,12),reverse_future=False):
    first=datetime.combine(day,time(9,30),ET)
    rows=[]
    for i in range(165):
        if i<90:price=700.+(.001 if i%2 else 0.)
        elif i==90:price=700.65
        elif i<95:price=700.66
        elif reverse_future and i>=96:price=698.50
        else:price=700.66+(i-94)*.01
        t=first+timedelta(minutes=i)
        rows.append(HistoricalFrame(
            timestamp=t,options=(),market=MarketSnapshot(
                spot=price,bid=price-.01,ask=price+.01,
                realized_volatility=.2,implied_volatility=.25,
                volume_ratio=1.8 if i>=85 else 1.,
                minutes_to_close=390-i)))
    return rows


def test_pcr_parser_never_reads_marketwide_or_incomplete_sampling(tmp_path):
    file=tmp_path/"spy_pcr_2026-08-12.csv"
    row={"observed_at":"2026-08-12T10:59:00-04:00",
         "put_volume_so_far":1000,"call_volume_so_far":800,
         "coverage":"all_spy_listed_options","source":"verified synthetic fixture"}
    with file.open("w",newline="") as handle:
        writer=csv.DictWriter(handle,fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
        writer.writerow({**row,"observed_at":"2026-08-12T11:00:00-04:00",
                         "put_volume_so_far":1100,"call_volume_so_far":900})
    data=load_complete_spy_pcr([file])
    assert len(data["2026-08-12"])==2
    t=datetime(2026,8,12,10,59,30,tzinfo=ET)
    assert pcr_at(data["2026-08-12"],t).put_volume_so_far==1000
    assert pcr_at(data["2026-08-12"],datetime(2026,8,12,11,0,tzinfo=ET)).put_volume_so_far==1100
    assert pcr_at(data["2026-08-12"],datetime(2026,8,12,10,58,tzinfo=ET)) is None
    assert pcr_at(data["2026-08-12"],datetime(2026,8,12,11,6,tzinfo=ET)) is None
    with pytest.raises(ValueError,match="complete|incomplete"):
        replace(pcr(),"coverage","x") if False else replace(pcr(),coverage="sampled_chain")
    with pytest.raises(ValueError,match="divide by zero"):
        replace(pcr(),call_volume_so_far=0)
    with pytest.raises(ValueError,match="decreasing"):
        with file.open("a",newline="") as handle:
            csv.DictWriter(handle,fieldnames=list(row)).writerow({
                **row,"observed_at":"2026-08-12T11:01:00-04:00",
                "put_volume_so_far":900,"call_volume_so_far":1200})
        load_complete_spy_pcr([file])


def test_pcr_hypotheses_momentum_vs_reversal_are_frozen_and_opposite():
    c=op(side="call")
    put=op(side="put")
    rev_call=op(side="call",family="rolling_mean_reversal")
    rev_put=op(side="put",family="rolling_mean_reversal")
    assert _confirmed_pcr(c,.9)
    assert not _confirmed_pcr(c,1.3)
    assert _confirmed_pcr(put,1.2)
    assert not _confirmed_pcr(put,.8)
    assert _confirmed_pcr(rev_call,1.3)
    assert not _confirmed_pcr(rev_call,.8)
    assert _confirmed_pcr(rev_put,.8)
    assert not _confirmed_pcr(rev_put,1.3)


def test_acceptance_is_unchanged_if_future_spy_price_return_is_poisoned():
    row=labeled(op(),pcr=.8,pcr_ok=True)
    alter=replace(row,opportunity=replace(
        row.opportunity,signed_spy_ten_minute_close_change_bps=-90000))
    assert [len(select([row],policy)) for policy in POLICIES]==[
        len(select([alter],policy)) for policy in POLICIES]
    no_pcr=replace(row,pcr_value=None,pcr_confirms=None)
    assert select([no_pcr],POLICIES[2])==[]
    assert len(select([no_pcr],POLICIES[1]))==1


def test_shared_one_position_clock_across_momentum_and_reversal():
    a=labeled(op(at=time(11,0)))
    b=labeled(op(at=time(11,1),family="rolling_mean_reversal"))
    c=labeled(op(at=time(11,12)))
    assert select([a,b,c],POLICIES[1])==[a,c]
    rejected=replace(a,reasons=("base_four_factors_REJECT",))
    assert select([rejected,b,c],POLICIES[1])==[b,c]


def test_no_missing_pcr_misreported_as_unprofitable_backtest():
    d="2026-08-12"
    o=labeled(op(signed=9),pcr=None,pcr_ok=None)
    m=_stats([d,"2026-08-13"],[o],POLICIES[-1],False)
    assert m["status"].startswith("NOT_TESTED")
    assert m["accepted"] is None
    assert m["option_pnl"] is None
    ab=_stats([d,"2026-08-13"],[o],POLICIES[1],False)
    assert ab["accepted"]==1
    assert ab["positive_spy_proxy_days"]==1
    assert ab["zero_spy_proxy_days"]==1
    assert ab["signed_spy_bps_per_day_minus_2bp_proxy"]==3.5


def test_causal_indicators_same_signal_different_future_return():
    a=session_classifications(history())
    b=session_classifications(history(reverse_future=True))
    sig=datetime(2026,8,12,11,1,tzinfo=ET).isoformat()
    u=next(x for x in a if x.opportunity.signal_at==sig)
    v=next(x for x in b if x.opportunity.signal_at==sig)
    assert u.factor_score==v.factor_score
    assert u.price_action_confirmed==v.price_action_confirmed
    assert u.macd_histo_aligned_or_improving==v.macd_histo_aligned_or_improving
    assert u.volume_confirmed==v.volume_confirmed
    assert u.reasons==v.reasons
    assert u.opportunity.signed_spy_ten_minute_close_change_bps>0
    assert v.opportunity.signed_spy_ten_minute_close_change_bps<0


def test_stream_reports_unavailable_pcr_and_untouched_no_orders():
    frames=[*history(date(2026,4,8)),*history(date(2026,8,12)),
            *history(date(2026,9,10))]
    res=run_study(frames)
    assert res["sessions"]==3
    assert res["frames"]==3*165
    assert res["real_option_pnl"] is None
    assert res["data_purchases_usd"]==0
    assert res["live_trades_placed"]==0
    assert res["adaptive_trailing_return_gate"] is False
    for per in res["periods"].values():
        assert per["trading_days"]==1
        assert per["models"][POLICIES[-1]]["status"].startswith("NOT_TESTED")
        for k in POLICIES[:2]:
            assert per["models"][k]["option_pnl"] is None


def test_out_of_order_sessions_rejected():
    with pytest.raises(ValueError,match="chronologically"):
        run_study([*history(date(2026,9,10)),*history(date(2026,8,12))])
