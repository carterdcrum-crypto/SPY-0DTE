from datetime import datetime, timedelta, timezone

from engine.exit_policy_comparison import simulate


NOW = datetime(2026, 10, 7, 15, 0, tzinfo=timezone.utc)


def path(*bids):
    return [(NOW + timedelta(minutes=i + 1), bid) for i,bid in enumerate(bids)]


def test_new_trailing_20_exits_earlier_than_old_static_loss_stop():
    trace=path(.9,.80,.75,.74)
    old=simulate(1.0,trace,mode="old_50",contracts=1)
    new=simulate(1.0,trace,mode="new_100_trail20",contracts=1)
    assert new["reasons"]["trailing_stop_20"]==1
    assert new["pnl"] > old["pnl"]


def test_new_100_target_scales_four_and_trails_remaining_one():
    trace=path(1.4,2.10,2.40,1.91)
    old=simulate(1.0,trace,mode="old_50",contracts=4)
    new=simulate(1.0,trace,mode="new_100_trail20",contracts=4)
    assert new["reasons"]["take_profit_100"]==3
    assert new["reasons"]["trailing_stop_20"]==1
    assert new["target_sold"] == 3
    assert old["reasons"]["take_profit_50"]==4


def test_one_contract_target_sells_all_and_no_profit_runner():
    trace=path(2.20,1.0)
    new=simulate(1.0,trace,mode="new_100_trail20",contracts=1)
    assert new["reasons"]=={"take_profit_100":1}


def test_factorial_arms_distinguish_target_from_trailing():
    trace=path(1.70,1.35,1.18,1.12)
    old=simulate(1.0,trace,mode="old_50",contracts=4)
    old_trail=simulate(1.0,trace,mode="old_50_trail20",contracts=4)
    new_no_trail=simulate(1.0,trace,mode="new_100_no_trail",contracts=4)
    new_trail=simulate(1.0,trace,mode="new_100_trail20",contracts=4)
    assert old["reasons"] == {"take_profit_50":4}
    assert old_trail["reasons"] == {"take_profit_50":4}
    assert "trailing_stop_20" not in new_no_trail["reasons"]
    assert "trailing_stop_20" in new_trail["reasons"]


def test_new_trailing_and_100_can_underperform_old_50_on_pullback():
    trace=path(1.65,1.10,1.0,.89)
    old=simulate(1.0,trace,mode="old_50",contracts=1)
    new=simulate(1.0,trace,mode="new_100_trail20",contracts=1)
    assert old["pnl"] > new["pnl"]
