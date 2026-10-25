"""Isolation tests for the research-only restored V5 drawdown sweep."""
from engine.v5_eod_drawdown_study import POLICIES, allowed_size


def test_sizing_never_increases_existing_signal_quantity():
    for policy in POLICIES.values():
        result = allowed_size(policy=policy, base_qty=4, opening=1000,
                              debit=135, premium_spent_today=0)
        assert 0 <= result <= 4


def test_per_trade_budget_respects_integer_contract_granularity():
    assert allowed_size(policy={"trade_limit": .20}, base_qty=50,
                        opening=1000, debit=100) == 2
    assert allowed_size(policy={"trade_limit": .15}, base_qty=50,
                        opening=1000, debit=160) == 0


def test_daily_premium_cap_counts_all_entry_debits():
    policy={"daily_premium_limit": .35}
    assert allowed_size(policy=policy, base_qty=50, opening=1000,
                        debit=100, premium_spent_today=200) == 1
    assert allowed_size(policy=policy, base_qty=50, opening=1000,
                        debit=100, premium_spent_today=300) == 0


def test_daily_max_contracts_respected():
    assert allowed_size(policy={"max_contracts": 1}, base_qty=30,
                        opening=1000, debit=100) == 1
