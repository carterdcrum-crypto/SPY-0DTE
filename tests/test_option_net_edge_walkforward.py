"""Regression: option execution friction, strictly prior labels and causal choices."""
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from collections import defaultdict
import pytest

from engine.option_net_edge_walkforward import (
    EXTRA_FRICTION_USD, EXPERTS, POLICIES, PRIOR_SESSIONS, TRAIL_PCT,
    STOP_PCT, MAX_HOLD, MIN_PRIOR_EXPERT, normalize_candidate_features,
    predict_net, decide, cost_check, day_evaluate, outcome_summary,
)


def frame_series():
    start=datetime(2026,10,6,13,30,tzinfo=timezone.utc)
    return [SimpleNamespace(
        timestamp=start+timedelta(minutes=i),
        market=SimpleNamespace(spot=600+.08*i,volume_ratio=1.7,
            realized_volatility=.18,implied_volatility=.20))
        for i in range(55)]


def quote(ask=1.2,bid=1.17):
    return SimpleNamespace(
        ask=ask,bid=bid,delta=.52,
        implied_volatility=.19,spread_fraction=(ask-bid)/((ask+bid)/2),
    )


def hist(i,expert="direct_5m",return_pnl=10.0):
    # Same dimension as quoted features, never future-day market inputs.
    return {"day":f"2026-09-{1+(i%25):02d}",
        "signal_at":f"2026-09-{1+(i%25):02d}T14:00:00+00:00",
        "features":tuple(0.1*(i%6) for _ in range(12)),
        "expert":expert,"pnl_usd":return_pnl}


def test_frozen_no_take_profit_policy_and_experts():
    assert TRAIL_PCT==30 and STOP_PCT==35 and MAX_HOLD==10
    assert PRIOR_SESSIONS==45
    assert EXTRA_FRICTION_USD==2.
    assert len(EXPERTS)==5 and len(POLICIES)==5


def test_quote_features_causal_and_delta_break_even_more_expensive_at_wider_spread():
    f=frame_series()
    narrow=normalize_candidate_features(f,20,quote(),"call")
    broad=normalize_candidate_features(f,20,quote(1.2,1.0),"call")
    assert len(narrow)==12
    assert broad[6]>narrow[6]  # more SPY price move needed to cover option friction
    # Future SPY price observations never affect completed signal features.
    f[21].market.spot=1e7
    assert narrow==normalize_candidate_features(f,20,quote(),"call")
    assert normalize_candidate_features(f,20,quote(),"put")[0]<0


def test_forecast_requires_prior_expert_labels_and_refuses_unverified_data():
    features=tuple(.1 for _ in range(12))
    assert predict_net([],EXPERTS[0],features) is None
    prior=[hist(i,return_pnl=40) for i in range(MIN_PRIOR_EXPERT-1)]
    assert predict_net(prior,EXPERTS[0],features) is None
    prior.append(hist(30,return_pnl=-10))
    p=predict_net(prior,EXPERTS[0],features)
    assert p is not None
    assert p["prior_expert_trades"]==MIN_PRIOR_EXPERT
    assert p["not_calibrated_probability"] is True
    assert predict_net(prior,EXPERTS[1],features) is None


def test_net_ev_gate_and_uncertainty_are_more_conservative_than_accuracy():
    f=tuple([.1]*7+[1.8]+[.1]*4)
    q=quote()
    good={"predicted_stressed_net_usd":5.,
          "uncertainty_penalized_stressed_net_usd":-2.}
    assert decide(EXPERTS[0],f,q,good,"prior_analog_positive_net")
    assert not decide(EXPERTS[0],f,q,good,"prior_analog_positive_lower_bound")
    good["uncertainty_penalized_stressed_net_usd"]=1.
    assert decide(EXPERTS[0],f,q,good,"prior_analog_positive_lower_bound")
    assert decide(EXPERTS[0],f,q,good,"prior_analog_net_plus_friction")
    assert decide(EXPERTS[0],f,q,None,"all_setups_one_at_a_time")
    assert not decide(EXPERTS[0],f,q,None,"prior_analog_positive_net")
    with pytest.raises(ValueError):
        decide(EXPERTS[0],f,q,good,"75pct_guaranteed")


def test_research_decision_cannot_use_future_option_profit(monkeypatch):
    import engine.option_net_edge_walkforward as model
    f=frame_series()
    q=quote()
    c={"expert":"direct_5m","event":{"signal_at":"2026-10-06T13:52:00+00:00",
           "right":"call","volume_ratio":1.7},
       "features":normalize_candidate_features(f,20,q,"call"),
       "quote":q,"entry_at":datetime(2026,10,6,13,54,tzinfo=timezone.utc),
       "option_quote_index":21,"symbol":"FAKE",
       "entry_debit_usd":121.65}
    monkeypatch.setattr(model,"_sampled_path",
        lambda frames,idx,symbol: [(
            datetime(2026,10,6,13,56,tzinfo=timezone.utc),1.1)])
    monkeypatch.setattr(model,"simulate",
        lambda ask,path,trail:{
            "pnl_usd":999999,"return_on_premium_pct":1.,
            "exit_time":path[0][0].isoformat(),"exit_reason":"trailing_stop"
        })
    # With no prior, not one net-forecast-gated arm can take a trade
    # despite arbitrarily positive FUTURE option performance.
    prior=[]
    examples,selected,diagnostics=day_evaluate(f,"2026-10-06",[c],prior)
    assert examples[0]["pnl_usd"]==999999
    assert {s["arm"] for s in selected}=={"all_setups_one_at_a_time","option_friction_only"}


def test_no_trade_sessions_keep_winrate_null():
    s=outcome_summary([],["2026-10-06"],"prior_analog_positive_net",
                      "all_inspected")
    assert s["observed_win_pct"] is None
    assert s["trades_per_session"]==0.
    assert s["net_option_pnl_usd_1_contract_NOT_ACCOUNT"]==0.
