"""Predictive V5 research is causal, never live trading."""
from collections import deque
from types import SimpleNamespace

from engine.v5_predictive_sizing_study import (
    POLICIES, budget_fraction, ex_ante_factors, reliability_multiplier, risk_size,
)


def test_reliability_uses_only_past_and_shrinks():
    assert reliability_multiplier(deque([]))==1.0
    assert reliability_multiplier(deque([True]*7))==1.0
    assert reliability_multiplier(deque([True]*30))>1.0
    assert reliability_multiplier(deque([False]*30))<1.0


def test_spread_and_iv_ratio_penalize_exposure():
    def sample(spread, iv):
        return {"quote": SimpleNamespace(spread_fraction=spread,
                         implied_volatility=iv),
                "realized_vol_at_entry":.20,
                "score":4,
                "signal":{"volume_ratio":1.8}}
    evidence={"aligned":True,"confluence":False}
    strong=ex_ante_factors(sample(.008,.21),evidence,1)
    weak=ex_ante_factors(sample(.028,.29),evidence,1)
    f=POLICIES["predictive_combined"]
    assert budget_fraction(f,1000,1000,strong)>budget_fraction(f,1000,1000,weak)


def test_predictive_never_exceeds_current_throttle_band():
    factors={"quality_regime":1.5,"liquidity":1.2,
             "volatility":1.2,"reliability":1.3,"spread":.01}
    for policy in POLICIES.values():
        if policy[0]=="adaptive":
            continue
        assert budget_fraction(policy,950,1000,factors)<=.40
        assert budget_fraction(policy,890,1000,factors)<=.15


def test_strict_spread_blocks_wide_quote():
    factors={"quality_regime":1.0,"liquidity":.63,
             "volatility":1.0,"reliability":1.0,"spread":.03}
    assert budget_fraction(POLICIES["predictive_combined_skip_wide"],
                           1000,1000,factors)==0.


def test_cash_bounding():
    item={"debit":75.,"score":5}
    factors={"quality_regime":1.1,"liquidity":1.0,
             "volatility":1.0,"reliability":1.0,"spread":.01}
    for policy in POLICIES.values():
        qty,_=risk_size(policy,item=item,settled=150.,opening=1000,
                        peak=1000,factors=factors)
        assert 0<=qty<=2
