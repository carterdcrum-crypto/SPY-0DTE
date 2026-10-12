"""EdgeForge bidirectional SPY options research: causal guardrail tests."""
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
import pytest

from engine.edgeforge_bidirectional import (
    ARMS, TRAIL_PERCENT, SIMULATED_MAX_ONE_CONTRACT_BUDGET_USD,
    EXTRA_FRICTION_USD, MIN_PRIOR_SIDE_SAMPLES, FEATURE_WEIGHTS,
    observed_signal_index, candidate_features, analog_forecast, choose,
    summarize, daily_block_bootstrap,
)


def fixture_signal(direction="call",at="2026-08-05T11:01:00+00:00"):
    return SimpleNamespace(
        factor_score=6,macd_histo_aligned_or_improving=True,
        opportunity=SimpleNamespace(
            original_direction=direction,
            signal_at=at,
            volume_ratio_at_signal=1.8,
            setup="close_breakout_baseline",
            signed_spy_ten_minute_close_change_bps=99999.0,
        )
    )


def test_asof_close_no_future_price_inputs():
    start=datetime(2026,8,5,10,30,tzinfo=timezone.utc)
    frames=[SimpleNamespace(timestamp=start+timedelta(minutes=k),
            market=SimpleNamespace(spot=600+k*.01,
                                   implied_volatility=.21,
                                   realized_volatility=.17))
            for k in range(46)]
    idx=30
    sig_time=(frames[idx].timestamp+timedelta(minutes=1)).isoformat()
    signal=fixture_signal(at=sig_time)
    option=SimpleNamespace(spread_fraction=.03,delta=.5,ask=1.1)
    assert observed_signal_index(frames,sig_time)==idx
    call=candidate_features(signal,option,frames,idx,"call")
    put=candidate_features(signal,option,frames,idx,"put")
    assert len(call)==len(FEATURE_WEIGHTS)==14
    assert call[0]==-1.0 and put[0]==1.0
    assert call[1]>0 and put[1]<0
    frames[idx+1].market.spot=1_000_000
    signal.opportunity.signed_spy_ten_minute_close_change_bps=-999999
    assert candidate_features(signal,option,frames,idx,"call")==call


def synthetic(side,idx,day,profit=30):
    features=(side,)+tuple(float(idx%6)/5 for _ in range(13))
    return {"day":day,"signal_at":f"{day}T12:00:00+00:00",
            "features":features,"pnl_usd":profit if idx%3==0 else -10.}


def test_missing_data_abstains_no_fabricated_75pct_probability():
    cohort=[synthetic(-1.,i,f"2026-04-{i%28+1:02d}") for i in range(29)]
    example=(1.0,)+tuple(0.4 for _ in range(13))
    assert analog_forecast(cohort,example) is None
    cohort.extend(synthetic(1.,i,f"2026-05-{i%28+1:02d}")
                  for i in range(30))
    report=analog_forecast(cohort,example)
    assert report
    assert report["prior_same_side_contracts"]==30
    assert 0<=report["predicted_win_pct"]<=100
    assert report["analog_contracts"]==30


def test_first_45_days_cannot_infiltrate_train_test_labels():
    # This unit probes the predictor's read boundary: hypothetical future
    # records supplied OUTSIDE prior never enter the predictor.
    priors=[synthetic(-1.,i,f"2026-04-{i%28+1:02d}") for i in range(40)]
    features=priors[-1]["features"]
    before=analog_forecast(priors,features)
    hypothetical_future=[synthetic(-1.,i,"2026-10-15",profit=1e6)
                         for i in range(40)]
    # Caller only supplies strictly prior cases; no outside labels used.
    after=analog_forecast(priors,features)
    assert before==after
    assert hypothetical_future


def test_trade_routing_is_only_model_score_and_never_realized_pnl():
    def case(side,cost=80):
        return {"side":side,"pnl_usd":-1e6,
                "entry_debit_one_contract":cost}
    event={"original_right":"call",
           "sides":{"call":case("call"),"put":case("put")}}
    good={"predicted_stressed_net_usd":12.,
          "predicted_win_pct":80.,
          "local_neighbor_unstressed_pnl_usd":16.,
          "cohort_unstressed_pnl_usd":13.,
          "estimated_lower_bound_stressed_net_usd":3.}
    bad={**good,"predicted_stressed_net_usd":-1.}
    assert choose(event,{"call":good,"put":bad},
                  "edgeforge_precise_75")["side"]=="call"
    assert choose(event,{"call":bad,"put":bad},
                  "edgeforge_precise_75") is None
    assert choose(event,{"call":None,"put":None},
                  "edgeforge_precise_75") is None
    event["sides"]["call"]["pnl_usd"]=1e8
    assert choose(event,{"call":bad,"put":bad},
                  "edgeforge_precise_75") is None
    with pytest.raises(ValueError):
        choose(event,{"call":good,"put":good},"100pct_guaranteed")


def test_affordability_cap_filters_out_pricey_contract():
    def case(cost):
        return {"entry_debit_one_contract":cost}
    event={"original_right":"call",
           "sides":{"call":case(SIMULATED_MAX_ONE_CONTRACT_BUDGET_USD+1),
                    "put":case(75.)}}
    forecast={"predicted_stressed_net_usd":8.,
              "predicted_win_pct":.7,
              "local_neighbor_unstressed_pnl_usd":13.,
              "cohort_unstressed_pnl_usd":15.,
              "estimated_lower_bound_stressed_net_usd":1.}
    assert choose(event,{"call":forecast,"put":forecast},
            "edgeforge_expert_consensus_115_cap")["entry_debit_one_contract"]==75


def test_zero_trade_is_no_win_rate_and_zero_profit_is_not_proof():
    days=["2026-04-01","2026-04-02"]
    arm="edgeforge_precise_75"
    m=summarize([],days,arm,"all_inspected")
    assert m["win_rate_pct"] is None
    assert m["trades"]==0
    assert m["flat_or_no_trade_days"]==2
    interval=daily_block_bootstrap([],days,arm,reps=1000)
    assert interval["additional_2usd_friction_same_interval"]==[0.,0.]
    assert TRAIL_PERCENT==30 and EXTRA_FRICTION_USD==2.0
