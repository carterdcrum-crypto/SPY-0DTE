"""Frozen trailing 30% entry-ensemble research safety and causality checks."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import pytest

from engine.adaptive_entry_walkforward import (
    TRAIL_PCT, EXPERTS, ARMS, PRIOR_SESSIONS, MIN_TRAINING_MATCHED,
    expert_for, causal_features, train_prior_only, predicted_score, decision,
    prior_profit_gate, stats,
)


def row(setup="close_breakout_baseline",volume=1.7,hour=10,macd=True):
    op=SimpleNamespace(
        setup=setup,original_direction="call",signal_at=f"2026-07-01T{hour:02d}:30:00-04:00",
        volume_ratio_at_signal=volume,
        signed_spy_ten_minute_close_change_bps=1_000_000_000,
    )
    return SimpleNamespace(opportunity=op, family_mode="momentum",
                           reasons=("base_four_factors_PASS",), factor_score=6,
                           price_action_confirmed=True,
                           macd_histo_aligned_or_improving=macd)


def test_expected_expert_routing_and_holdout_not_read():
    assert TRAIL_PCT==30
    r=row()
    assert expert_for(r)==EXPERTS[0]
    r.opportunity.signed_spy_ten_minute_close_change_bps=-1e9
    assert expert_for(r)==EXPERTS[0]
    assert expert_for(row(hour=14)) is None
    assert expert_for(row(volume=1.1)) is None
    assert expert_for(row(setup="trend_pullback_reclaim"))==EXPERTS[1]
    assert expert_for(row(setup="trend_pullback_reclaim",macd=False)) is None
    assert len(ARMS)==8


def test_features_are_fixed_and_use_only_signal_time_closes():
    now=datetime(2026,7,1,13,0,tzinfo=timezone.utc)
    prices=[600+.02*i for i in range(32)]
    frames=[SimpleNamespace(timestamp=now+timedelta(minutes=i),
       market=SimpleNamespace(spot=p,realized_volatility=.15,implied_volatility=.18))
       for i,p in enumerate(prices)]
    quote=SimpleNamespace(spread_fraction=.04,delta=.51,ask=1.5)
    r=row()
    first=causal_features(r,quote,frames,25)
    frames[26].market.spot=10000
    frames[31].market.spot=1
    assert first==causal_features(r,quote,frames,25)
    r.opportunity.signed_spy_ten_minute_close_change_bps=-99999
    assert first==causal_features(r,quote,frames,25)
    assert len(first)==14


def pastcase(i,expert=None,win=None,pnl=None,day="2026-07-01"):
    win = i%3==0 if win is None else win
    return {"features":[(i%8)/8,.4,-.2]+[0.]*11,
            "won":win,
            "net_pnl_usd":(25. if win else -24.) if pnl is None else pnl,
            "expert":expert or EXPERTS[i%2],"day":day}


def test_fit_only_supplied_prior_trades():
    cases=[pastcase(i) for i in range(120)]
    assert train_prior_only(cases[:MIN_TRAINING_MATCHED-1]) is None
    trained=train_prior_only(cases)
    assert trained is not None
    assert len(trained[0])==15
    prediction=predicted_score(trained,cases[3])
    assert 0 < prediction["predicted_win"] < 1
    assert isinstance(prediction["predicted_stressed_net_usd_per_contract"],float)
    # Refit after adding a completely new prior day: trained weights change.
    future=[pastcase(i,win=True,pnl=80.) for i in range(70)]
    assert train_prior_only(cases+future)[0]!=trained[0]


def test_strict_75_rule_does_not_falsely_pass_low_predicted_probability():
    case=pastcase(1,expert=EXPERTS[0])
    low={"predicted_win":.7499,"predicted_stressed_net_usd_per_contract":8.,
         "prior_expert_matched":32}
    high={**low,"predicted_win":.8}
    assert not decision(case,low,"adaptive_predicted_win_75_and_ev",[])
    assert decision(case,high,"adaptive_predicted_win_75_and_ev",[])
    assert not decision(case,{**high,"predicted_stressed_net_usd_per_contract":-1.},
                        "adaptive_predicted_win_75_and_ev",[])
    assert not decision(case,None,"adaptive_predicted_win_75_and_ev",[])
    assert decision(case,None,"both_experts_unfiltered",[])
    with pytest.raises(ValueError):
        decision(case,low,"guaranteed_75_win",[])


def test_profit_evidence_gate_requires_prior_positivity_across_days():
    pos=[pastcase(i,expert=EXPERTS[0],win=True,pnl=50.,
                  day=f"2026-07-{1+i//3:02d}") for i in range(90)]
    neg=[{**case,"net_pnl_usd":-15.} for case in pos]
    assert prior_profit_gate(pos,EXPERTS[0])
    assert not prior_profit_gate(neg,EXPERTS[0])
    assert not prior_profit_gate(pos[:30],EXPERTS[0])


def test_zero_prediction_outcome_is_null_win_rate():
    a=stats([],["2026-07-01"],"adaptive_predicted_win_75_and_ev","all_inspected")
    assert a["trades"]==0
    assert a["win_rate_pct"] is None
    assert a["hypothetical_pnl_usd_not_account"]==0.0
