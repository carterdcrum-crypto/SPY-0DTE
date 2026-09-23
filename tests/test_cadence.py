import pytest

from engine.cadence import MarketCadencePolicy, cadence_decision, quote_is_fresh


def test_engine_evaluates_every_second_independent_of_option_refresh():
    policy = MarketCadencePolicy()
    plan = cadence_decision(
        101.0,
        last_engine_tick=100.0,
        last_active_option_refresh=100.0,
        last_full_chain_refresh=100.0,
        sandbox=True,
        policy=policy,
    )
    assert plan.evaluate_engine is True
    assert plan.refresh_active_options is False
    assert plan.refresh_full_chain is False


def test_sandbox_active_options_refresh_every_two_seconds():
    policy = MarketCadencePolicy()
    plan = cadence_decision(
        102.0,
        last_engine_tick=101.5,
        last_active_option_refresh=100.0,
        last_full_chain_refresh=80.0,
        sandbox=True,
        policy=policy,
    )
    assert plan.evaluate_engine is False
    assert plan.refresh_active_options is True
    assert plan.refresh_full_chain is False


def test_production_allows_one_second_active_refresh():
    policy = MarketCadencePolicy()
    plan = cadence_decision(
        101.0,
        last_engine_tick=100.2,
        last_active_option_refresh=100.0,
        last_full_chain_refresh=100.0,
        sandbox=False,
        policy=policy,
    )
    assert plan.refresh_active_options is True


def test_full_chain_scan_stays_background_cadence():
    policy = MarketCadencePolicy(full_chain_refresh_seconds=60.0)
    assert cadence_decision(
        160.0,
        last_engine_tick=159.5,
        last_active_option_refresh=159.0,
        last_full_chain_refresh=100.0,
        sandbox=True,
        policy=policy,
    ).refresh_full_chain is True


def test_rate_limit_guard_rejects_impossible_cadence():
    with pytest.raises(ValueError):
        MarketCadencePolicy(sandbox_active_option_refresh_seconds=1.0)
    with pytest.raises(ValueError):
        MarketCadencePolicy(production_active_option_refresh_seconds=0.5)


def test_stale_quotes_fail_closed():
    policy = MarketCadencePolicy(maximum_live_quote_age_seconds=3.0)
    assert quote_is_fresh(1.0, policy)
    assert quote_is_fresh(3.0, policy)
    assert not quote_is_fresh(3.001, policy)
    assert not quote_is_fresh(-1.0, policy)
