"""Unit checks for T+1-only, fail-closed risk budgets and no hindsight Kelly."""
from pathlib import Path
import pytest
from engine.v4_dynamic_size_backtest import (
    choose_size, run, ARMS, START_CASH, MAX_CONTRACTS,
)

DEFAULT=dict(
    equity=1000., settled=1000., peak=1000., debit=80.,
    spread=.01,relative_volume=1.5,previous_returns=(),
    expiry_minutes=100.,
)

def decision(arm,**overrides):
    fields={**DEFAULT,**overrides}
    return choose_size(arm=arm,**fields)

def test_frozen_v4_fits_one_contract_and_rejects_unaffordable():
    assert decision(ARMS[0]).contracts==1
    assert decision(ARMS[0],settled=50).contracts==0

def test_compound_sizes_integer_and_preserves_settlement():
    result=decision("v4_dynamic_compound")
    assert 1 < result.contracts <= MAX_CONTRACTS
    assert result.contracts*80 <= 1000
    result2=decision("v4_dynamic_compound",settled=150)
    assert result2.contracts<=1

def test_profit_vault_reduces_spendable_cash_without_fake_withdrawal():
    a=decision("v4_dynamic_compound",equity=2000.,settled=2000.,peak=2000.,debit=250.)
    b=decision("v4_dynamic_profit_vault",equity=2000.,settled=2000.,peak=2000.,debit=250.)
    assert b.reserve==500.
    assert b.contracts<=a.contracts

def test_drawdown_halt_applies_to_every_dynamic_arm_but_not_frozen():
    for arm in ARMS[1:]:
        assert decision(arm,equity=800.,settled=800.,peak=1000.).contracts==0
    assert decision(ARMS[0],equity=800.,settled=800.,peak=1000.).contracts==1

def test_kelly_requires_25_prior_closed_trades_and_allows_zero():
    assert decision("v4_dynamic_fractional_kelly",previous_returns=(.30,)*24).reason=="INSUFFICIENT_PREVIOUS_DAYS_FOR_KELLY"
    d=decision("v4_dynamic_fractional_kelly",previous_returns=(-.25,)*50)
    assert d.contracts==0
    assert d.reason in {"insufficient_edge_probability","non_positive_net_ev","non_positive_expected_utility"}

def test_no_archive_does_not_invent_a_model_backtest(tmp_path):
    with pytest.raises(ValueError,match="archive required"):
        run(tmp_path,tmp_path/"out")

def test_unrecognized_arm_fails_closed():
    with pytest.raises(ValueError,match="unknown sizing arm"):
        decision("other")
