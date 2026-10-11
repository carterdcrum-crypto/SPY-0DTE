"""Regression tests: trailing grid must never target-sell or scale out."""
from datetime import datetime, timedelta, timezone
import pytest
from engine.trailing_only_sweep import TRAIL_PCTS, policy_name, simulate, HARD_STOP

NOW=datetime(2026,10,6,14,0,tzinfo=timezone.utc)

def path(*bids):
    return [(NOW+timedelta(minutes=i+1),bid) for i,bid in enumerate(bids)]

def test_grid_has_looser_stops_and_no_stop_control():
    assert TRAIL_PCTS[0]==20
    assert TRAIL_PCTS[-1]==90
    assert len(TRAIL_PCTS)==15
    assert policy_name(None)=="no_trailing"

def test_no_profit_target_for_even_huge_gain():
    result=simulate(1.,path(2.,3.,4.,5.),40)
    assert result["exit_reason"]=="hard_max_hold_10min"
    assert result["pnl_usd"]>350.

def test_trailing_40_holds_but_20_exits_early():
    # Enter ~$102, credit hits $199, retraces to $149 then rebounds.
    ticks=path(2.,1.50,3.0)
    stop20=simulate(1.,ticks,20)
    stop40=simulate(1.,ticks,40)
    assert stop20["exit_reason"]=="trailing_stop"
    assert stop40["exit_reason"]=="hard_max_hold_10min"
    assert stop40["pnl_usd"]>stop20["pnl_usd"]

def test_independent_hard_stop_survives_even_with_no_trail():
    result=simulate(1.,path(.60,3.),None)
    assert result["exit_reason"]=="independent_hard_stop_35pct"
    assert result["pnl_usd"]<0.

def test_all_positions_exit_once_and_invalid_input_rejected():
    with pytest.raises(ValueError):
        simulate(1.,[],40)
    with pytest.raises(ValueError):
        simulate(1.,path(1.),0)
