"""Position sizing is causal and never bypasses 0DTE hard-risk gates."""
import pytest

from engine.v4_compound_sizing import adaptive_quantity, unrestricted_research_quantity


def choose(**changes):
    kwargs=dict(score=5,equity=1000.,settled=1000.,peak=1000.,
                debit=101.65,starting_cash=1000.,exposure=.35,
                max_contracts=100,bid_size=100,ask_size=100)
    kwargs.update(changes)
    return adaptive_quantity(**kwargs)


def test_original_size_at_start_and_growth_above_old_three_ceiling():
    assert choose()==3
    assert choose(equity=2000.,settled=2000.,peak=2000.)==6
    assert choose(equity=4000.,settled=4000.,peak=4000.)>6


def test_quality_drawdown_and_protected_profit():
    assert choose(score=3,equity=3000.,settled=3000.,peak=3000.)==1
    assert choose(equity=2000.,settled=2000.,peak=2100.)==1
    assert choose(equity=2000.,settled=2000.,peak=2050.)==3
    assert choose(equity=2000.,settled=2000.,peak=2000.,exposure=.15)==2


def test_hard_budgets_and_liquidity_cannot_be_overridden():
    assert choose(equity=2000.,settled=2000.,peak=2000.,bid_size=2)==2
    assert choose(equity=2000.,settled=2000.,peak=2000.,max_contracts=1)==1
    assert choose(settled=50.)==0
    assert choose(debit=900.)==0
    assert choose(exposure=0.)==0
    assert choose(peak=float("nan"))==0


def test_unrestricted_is_separate_research_only_stress():
    assert unrestricted_research_quantity(settled=1000.,debit=101.65)==9
    assert unrestricted_research_quantity(settled=1000.,debit=0.)==0


def test_adaptive_candidate_remains_real_quote_eligible(monkeypatch):
    from engine import v4_operational as v
    from tests.test_v4_operational import bars, q, signal, NOW
    b=bars()
    monkeypatch.setattr(v,"identify_setups",lambda frames:[signal(b)])
    proposal,why=v.candidate(NOW,b,[q(bid_size=20,ask_size=20)],
        equity=2000.,settled=2000.,peak=2000.,
        sizing_mode="adaptive",starting_cash=1000.,max_contracts=100)
    assert why["state"]=="SIGNAL" and proposal["quantity"]==6
    assert proposal["sizing_mode"]=="adaptive_compound"
    delayed=q(timestamp=NOW-v.timedelta(seconds=120))
    blocked,_=v.candidate(NOW,b,[delayed],equity=2000.,settled=2000.,
                          peak=2000.,sizing_mode="adaptive",max_contracts=100)
    assert blocked is None
