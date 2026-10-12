"""Entry edge: never claim a profitable model without past-only evidence."""
from datetime import datetime,timezone
from types import SimpleNamespace
import pytest

from engine.cost_aware_entry_edge import (
    VARIANTS, WINDOW, TRAIL_PERCENT, EXTRA_SLIPPAGE_USD_ROUNDTRIP,
    quote_eligible, signal_eligible, conservative_prior_gate, robustness,
)

def row(volume=1.55,setup="close_breakout_baseline",at="2026-07-07T10:45:00-04:00",
        passing=True,macd=True,price=True,weighted=7):
    op=SimpleNamespace(volume_ratio_at_signal=volume,setup=setup,
                       signal_at=at,weighted_total=weighted,
                       signed_spy_ten_minute_close_change_bps=1e6)
    return SimpleNamespace(opportunity=op,
        reasons=("base_four_factors_PASS",) if passing else ("base_four_factors_REJECT",),
        family_mode="momentum",macd_histo_aligned_or_improving=macd,
        price_action_confirmed=price)

def prior_day(pnl=10.,variant="volume_150",count=2):
    return {"cases":{v:([{"net_pnl_usd":pnl}]*count if v==variant else [])
                     for v in VARIANTS}}

def test_fixed_exit_and_tiny_history_cannot_trade():
    assert TRAIL_PERCENT==30
    assert len(VARIANTS)==9
    x=conservative_prior_gate([prior_day() for _ in range(WINDOW-1)],"volume_150")
    assert x["allowed"] is False
    assert x["reason"]=="insufficient_prior_sessions"

def test_good_only_when_past_day_stressed_bound_clears_zero():
    positive=[prior_day(11.) for _ in range(WINDOW)]
    assert conservative_prior_gate(positive,"volume_150")["allowed"] is True
    negative=[prior_day(1.) for _ in range(WINDOW)]
    result=conservative_prior_gate(negative,"volume_150")
    assert not result["allowed"]
    assert result["prior_stressed_lower_bound_usd_per_day"]<0

def test_never_compute_from_current_or_future_day():
    historical=[prior_day(11.) for _ in range(WINDOW)]
    baseline=conservative_prior_gate(historical,"volume_150")
    poisoned=conservative_prior_gate([*historical,prior_day(-999999.)],"volume_150")
    assert baseline["allowed"] is True
    assert poisoned["allowed"] is False
    # Callers MUST pass sessions strictly BEFORE evaluated day, as run() does.

def test_minimum_samples_not_optimistic_one_trade_anecdote():
    sparse=[prior_day(pnl=200.,count=0) for _ in range(WINDOW-1)]
    sparse.append(prior_day(200.,count=1))
    r=conservative_prior_gate(sparse,"volume_150")
    assert not r["allowed"]
    assert r["reason"]=="insufficient_prior_trade_count"

def test_asof_indicator_hypotheses_never_see_future_label():
    z=row()
    outcomes1={v:signal_eligible(z,v) for v in VARIANTS}
    z.opportunity.signed_spy_ten_minute_close_change_bps=-1e6
    assert outcomes1=={v:signal_eligible(z,v) for v in VARIANTS}
    assert outcomes1["volume_150"]
    assert not outcomes1["volume_200"]
    assert outcomes1["volume_150_early"]
    assert not signal_eligible(row(volume=1.0),"volume_150")
    assert not any(signal_eligible(row(passing=False),v) for v in VARIANTS)

def test_quote_spread_gate_and_bad_variant():
    narrow=SimpleNamespace(spread_fraction=.075)
    wide=SimpleNamespace(spread_fraction=.09)
    assert quote_eligible(narrow,"tight_spread_080")
    assert not quote_eligible(wide,"volume_150_spread_080")
    assert quote_eligible(wide,"volume_150")
    with pytest.raises(ValueError):
        conservative_prior_gate([],"infinite_money")
    with pytest.raises(ValueError):
        signal_eligible(row(),"invented_policy")


def test_robustness_loses_outlier_and_reports_breakeven_payoff():
    days=[]
    for i in range(10):
        pnl=100. if i==0 else (-10. if i<6 else 8.)
        days.append({"day":f"2026-04-{i+1:02d}",
                     "cases":{v:([{"net_pnl_usd":pnl}]
                         if v=="volume_150_early" else []) for v in VARIANTS}})
    result=robustness(days,"volume_150_early",reps=100)
    assert result["raw_pnl_total_usd"]==82
    assert result["minus_best_1_day_sum_usd"]==-18
    assert result["stress_pnl_total_usd"]==62
    assert result["breakeven_win_rate_at_observed_payoff_ratio_pct"] is not None
    assert len(result["bootstrap_5session_day_blocks_95pct_usd_per_calendar_day"])==2


def test_morning_research_entry_was_shifted_to_0930_1100_eastern():
    for clock,expected in (("09:30",True),("09:59",True),
                           ("10:59",True),("11:00",False),
                           ("11:30",False),("12:00",False)):
        candidate=row(at=f"2026-07-07T{clock}:00-04:00")
        assert signal_eligible(candidate,"volume_150_early")==expected
    assert signal_eligible(row(at="2026-07-07T09:45:00-04:00",volume=1.0),
                           "volume_150_early") is False
