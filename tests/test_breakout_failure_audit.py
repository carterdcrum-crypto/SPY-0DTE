"""Causal breakout diagnostics; synthetic fixtures never imply option P&L."""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from engine.data import HistoricalFrame
from engine.market import MarketSnapshot
from engine.breakout_failure_audit import (
    FilterPolicy,FILTERS,annotate_baseline_session,select_filter,
    _metrics,_strata,run_audit,
)

ET=ZoneInfo("America/New_York")


def frames(day=date(2026,8,12),*,bad_future=False,missing_prior_bar=False):
    start=datetime.combine(day,time(9,30),ET)
    out=[]
    for i in range(64):
        if i<30:
            spot=700+i*.005
        elif i==32:
            spot=700.50
        elif i>32:
            spot=700.10 if bad_future and i>33 else 700.5+(i-32)*.006
        else:
            spot=700.145
        now=start+timedelta(minutes=i+(1 if missing_prior_bar and i>=11 else 0))
        out.append(HistoricalFrame(
            timestamp=now,options=(),
            market=MarketSnapshot(
                spot=spot,bid=spot-.01,ask=spot+.01,
                realized_volatility=.2,implied_volatility=.2,
                volume_ratio=2. if i==32 else 1.,
                minutes_to_close=390-i,
            )))
    return out


def first_breakout(series):
    data=annotate_baseline_session(series)
    assert data
    return next(x for x in data if x.signal_spot==700.50)


def test_breakout_failure_and_reversal_are_measured_from_future_closes_only():
    up=first_breakout(frames())
    down=first_breakout(frames(bad_future=True))
    assert up.signal_at==down.signal_at
    assert up.direction==down.direction=="call"
    assert up.signed_next_ten_minute_spy_bps>0
    assert down.signed_next_ten_minute_spy_bps<0
    assert down.snap_back_inside_range_first_three_minutes
    assert down.first_three_minute_adverse_bps<0


def test_filter_decisions_not_influenced_by_future_outcomes():
    up=first_breakout(frames())
    reversed_after=first_breakout(frames(bad_future=True))
    # Features must be bit-for-bit identical for identical past.
    for field in (
        "signal_spot","prior_fifteen_close_boundary","extension_bps",
        "signal_volume_ratio","signed_five_minute_trend_bps",
        "signed_thirty_minute_trend_bps","efficiency_last_fifteen",
        "pre_signal_history_complete","time_bucket",
    ):
        assert getattr(up,field)==getattr(reversed_after,field)
    for name in FILTERS:
        assert select_filter(up,name)==select_filter(reversed_after,name)
        # Even maliciously replacing future outcome fields may not
        # alter the filtering decision.
        altered=replace(
            up,signed_next_ten_minute_spy_bps=-100000,
            snap_back_inside_range_first_three_minutes=True,
            net_negative_after_2bp_proxy_friction=True,
            worst_close_only_adverse_bps=-10000,
        )
        assert select_filter(up,name)==select_filter(altered,name)


def test_filters_are_fixed_and_fail_closed_if_history_is_incomplete():
    event=first_breakout(frames())
    assert select_filter(event,"baseline_all")
    with pytest.raises(ValueError,match="unknown"):
        select_filter(event,"anything_not_declared")
    fragmented=first_breakout(frames(missing_prior_bar=True))
    assert not fragmented.pre_signal_history_complete
    assert select_filter(fragmented,"baseline_all")
    for name in FILTERS[1:]:
        assert not select_filter(fragmented,name)


def test_predeclared_filter_logic_is_reproducible():
    e=first_breakout(frames())
    hot=replace(e,pre_signal_history_complete=True,signal_volume_ratio=1.6,
                signed_five_minute_trend_bps=5,
                signed_thirty_minute_trend_bps=9,
                efficiency_last_fifteen=.6,
                extension_bps=2)
    assert all(select_filter(hot,n) for n in FILTERS)
    cold=replace(hot,extension_bps=20)
    assert select_filter(cold,"volume_plus_five_minute_trend")
    assert select_filter(cold,"thirty_minute_trend_plus_efficiency")
    assert not select_filter(cold,"low_extension_plus_volume")
    assert not select_filter(cold,"combined_conservative")
    choppy=replace(hot,efficiency_last_fifteen=.10)
    assert not select_filter(choppy,"thirty_minute_trend_plus_efficiency")
    assert not select_filter(choppy,"combined_conservative")


def test_paired_bootstrap_includes_all_days_and_zero_baseline_delta():
    day1=date(2026,8,12)
    day2=date(2026,8,13)
    rows=annotate_baseline_session(frames(day1))
    days=(day1,day2)
    cfg=FilterPolicy(bootstrap_reps=100,bootstrap_seed=12)
    baseline=_metrics(rows,rows,days,cfg)
    assert baseline["sessions_in_period"]==2
    assert baseline["baseline_opportunities"]==len(rows)
    assert baseline["accepted"]==len(rows)
    assert baseline["account_return"] is None
    assert baseline["account_max_drawdown"] is None
    assert baseline["options_trades"] is None
    assert baseline["day_block_bootstrap"]["paired_change_vs_baseline_bps_per_day_95pct_ci"]==[0.,0.]
    assert _metrics(rows,rows,days,cfg)==baseline
    ignored=_metrics([],rows,days,cfg)
    assert ignored["accepted"]==0
    assert ignored["signal_sessions"]==0
    assert ignored["mean_spy_signed_bps_per_event"] is None
    assert ignored["mean_spy_signed_bps_per_session_after_2bp_proxy"]==0.


def test_all_periods_all_filters_report_failures_and_no_option_results():
    entries=[*frames(date(2026,4,8)),*frames(date(2026,8,12)),
             *frames(date(2026,9,10),bad_future=True)]
    report=run_audit(entries,policy=FilterPolicy(bootstrap_reps=25))
    assert report["source_sessions"]==3
    assert report["source_frames"]==64*3
    assert report["no_holdout_claim"] is True
    assert len(report["partitions"])==3
    for period,policies in report["partitions"].items():
        assert set(policies)==set(FILTERS)
        for name,row in policies.items():
            assert row["sessions_in_period"]==1
            assert row["account_return"] is None
            assert row["options_trades"] is None
            assert row["skipped"]==row["baseline_opportunities"]-row["accepted"]
    assert report["posthoc_mechanism_diagnostics"]
    assert all("wrong_direction_fraction" in r for r in
               report["posthoc_mechanism_diagnostics"])


def test_no_incorrect_zero_account_drawdown_or_profit_from_price_proxy():
    row=first_breakout(frames(bad_future=True))
    stats=_metrics([row],[row],[date(2026,8,12)],FilterPolicy(bootstrap_reps=20))
    assert stats["max_cumulative_signed_bps_decline_NOT_account_drawdown"]>0
    assert stats["account_max_drawdown"] is None
    assert stats["mean_spy_signed_bps_per_event_after_2bp_proxy"]<0
    assert stats["failed_direction_fraction"]==1.


def test_posthoc_strata_are_labeled_and_are_not_used_in_filter():
    event=first_breakout(frames())
    labels=_strata([event],"validation",FilterPolicy())
    assert any(r["factor"]=="time_of_day" for r in labels)
    assert all(r["small_group_fewer_than_20"] for r in labels)
