"""Research-only frozen 30% trailing entry checks."""
from types import SimpleNamespace
import pytest
from engine.entry_filter_30pct_study import eligible, VARIANTS, TRAIL_PCT, _summarize


def row(setup="close_breakout_baseline", family_mode="momentum",
        price=True, macd=True, volume=1.60, weighted=7, passing=True):
    op=SimpleNamespace(setup=setup,volume_ratio_at_signal=volume,
                       weighted_total=weighted)
    return SimpleNamespace(
        opportunity=op,family_mode=family_mode,
        price_action_confirmed=price,
        macd_histo_aligned_or_improving=macd,
        reasons=("base_four_factors_PASS",) if passing else ("base_four_factors_REJECT",)
    )


def test_30pct_frozen_and_no_dependent_future_fields():
    assert TRAIL_PCT==30
    r=row()
    r.opportunity.signed_spy_ten_minute_close_change_bps= -1000000
    a=[eligible(r,v) for v in VARIANTS]
    r.opportunity.signed_spy_ten_minute_close_change_bps=1000000
    assert a==[eligible(r,v) for v in VARIANTS]


def test_confluence_filter_momentum_only():
    r=row()
    assert eligible(r,"momentum_only")
    assert eligible(r,"momentum_macd_price")
    assert eligible(r,"higher_volume_ge_1p50")
    assert eligible(r,"weighted_score_ge_6")
    r=row(setup="trend_pullback_reclaim",macd=False,volume=1.3)
    assert eligible(r,"pullback_only")
    assert not eligible(r,"macd_and_price_action")
    assert not eligible(r,"higher_volume_ge_1p50")


def test_reversal_is_disjoint_from_momentum():
    r=row(setup="rolling_mean_reversal",family_mode="reversal")
    assert eligible(r,"reversal_only")
    assert not eligible(r,"momentum_only")
    assert not eligible(r,"momentum_macd_price")
    assert not eligible(r,"breakout_only")


def test_old_signal_failure_cannot_reenter_any_variant():
    r=row(passing=False)
    assert not any(eligible(r,v) for v in VARIANTS)
    with pytest.raises(ValueError):
        eligible(r,"dynamic_curve_fit")


def test_empty_regime_has_zero_days_not_fake_wins():
    s=_summarize([], ["2026-04-01"],"momentum_only","all_inspected")
    assert s["matched_trades"]==0
    assert s["sessions"]==1
    assert s["sum_pnl_per_calendar_session_usd_NOT_ACCOUNT"]==0
    assert s["winning_trades_pct"] is None
