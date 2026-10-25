"""Research-only V5 conditional sizing invariants."""
from types import SimpleNamespace

from engine.v5_conditional_capital_study import (
    POLICIES, choose_qty, dd_multiplier, market_evidence,
    peak_to_trough_eod, MOMENTUM, REVERSAL, EXPANSION,
)


def item(setup=MOMENTUM):
    return {
        "debit":100.,
        "score":4,
        "signal":{"setup":setup,"trend_bps":5.,"efficiency":.50,
                  "volume_ratio":1.8},
        "quote":SimpleNamespace(spread_fraction=.01,implied_volatility=.20),
        "realized_vol_at_entry":.20,
    }


def test_no_sizing_arm_can_exceed_settled_cash():
    evidence=market_evidence(item(),{MOMENTUM,EXPANSION})
    for policy in POLICIES.values():
        n,ratio=choose_qty(policy,item=item(),opening=2000,settled=250,
                           peak=2200,evidence=evidence)
        assert 0<=n<=2
        assert ratio>=0


def test_conditional_fraction_uses_quality_and_confluence():
    high=market_evidence(item(),{MOMENTUM,EXPANSION})
    low_item=item(REVERSAL)
    low_item["score"]=1
    low_item["signal"]["trend_bps"]=20.
    low_item["signal"]["efficiency"]=.80
    low_item["signal"]["volume_ratio"]=1.3
    low_item["quote"].spread_fraction=.025
    low_item["quote"].implied_volatility=.29
    lower=market_evidence(low_item,{REVERSAL})
    assert high["tier"]>lower["tier"]
    policy=POLICIES["conditional_balanced_no_dd_throttle"]
    n1,_=choose_qty(policy,item=item(),opening=1000,settled=1000,
                    peak=1000,evidence=high)
    n2,_=choose_qty(policy,item=item(),opening=1000,settled=1000,
                    peak=1000,evidence=lower)
    assert n1>n2


def test_drawdown_risk_budget_uses_previous_session_only():
    assert dd_multiplier(1000,1000)==1.
    assert dd_multiplier(970,1000)==.70
    assert dd_multiplier(920,1000)==.40
    assert dd_multiplier(800,1000)==.15


def test_true_max_dd_fraction_not_largest_dollar_decline():
    daily=[{"day":"one","equity_usd":500},
           {"day":"two","equity_usd":2000},
           {"day":"three","equity_usd":1500}]
    x=peak_to_trough_eod(daily,1000)
    assert x["true_max_eod_drawdown_pct"]==50.0
    assert x["worst_dd_trough_day"]=="one"
