"""Selected V4 3x paper/live causal stock M1 + real option snapshots."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from engine import v4_operational as v
from engine.weekly_options_data import Bar

NOW=datetime(2026,10,12,14,59,40,tzinfo=timezone.utc)
START=datetime(2026,10,12,13,39,tzinfo=timezone.utc)


def bars():
    return [Bar(START+timedelta(minutes=i),
        600+i*.015,600.04+i*.015,599.96+i*.015,
        600.01+i*.015+(i%4)*.007,200)
            for i in range(80)]


def q(**kw):
    d=dict(source="webull_production",timestamp_quality="option_quote_timestamp",
        timestamp=NOW-timedelta(seconds=.2),received_at=NOW-timedelta(seconds=.1),
        underlying_timestamp=NOW-timedelta(seconds=.2),
        option_symbol="SPY261012C00600000",expiration=NOW.date(),
        right="call",strike=600.,bid=.99,ask=1.,
        bid_size=10,ask_size=10,underlying_price=601.2,
        greeks=SimpleNamespace(delta=.50,implied_volatility=.01))
    d.update(kw)
    return SimpleNamespace(**d)


def signal(bars):
    return {"setup":"trend_reclaim","side":"call",
            "volume_ratio":1.3,"trend_bps":5.,"efficiency":.4,
            "at":bars[-2].available_at}


def test_fresh_asof_real_m1_can_select_three_contracts(monkeypatch):
    b=bars()
    monkeypatch.setattr(v,"identify_setups",lambda frames:[signal(b)])
    proposal,diagnostics=v.candidate(NOW,b,[q()],equity=1000.,
                                      settled=1000.,peak=1000.)
    assert diagnostics["state"]=="SIGNAL"
    assert proposal["quality_score"]==5
    assert proposal["quantity"]==3
    assert proposal["signal_at"]==signal(b)["at"].isoformat()


def test_one_to_three_contracts_have_cash_depth_and_owner_ceiling(monkeypatch):
    b=bars()
    monkeypatch.setattr(v,"identify_setups",lambda frames:[signal(b)])
    sizes=[
      v.candidate(NOW,b,[q(ask_size=1)],equity=1000.,settled=1000.,peak=1000.)[0]["quantity"],
      v.candidate(NOW,b,[q()],equity=1000.,settled=1000.,peak=1000.,max_contracts=1)[0]["quantity"],
      v.candidate(NOW,b,[q()],equity=1000.,settled=1000.,peak=1000.,exposure=.12)[0]["quantity"],
    ]
    assert sizes==[1,1,1]


def test_no_fake_3x_on_delayed_snapshot_or_missing_volume(monkeypatch):
    b=bars()
    monkeypatch.setattr(v,"identify_setups",lambda frames:[signal(b)])
    for fake in (q(source="webull_sandbox_delayed"),
                 q(timestamp=NOW-timedelta(minutes=2)),
                 q(underlying_timestamp=None),
                 q(greeks=None)):
        proposal,reason=v.candidate(NOW,b,[fake],equity=1000.,
                                    settled=1000.,peak=1000.)
        assert proposal is None
    b[-1]=Bar(b[-1].start,600,601,599,600,0)
    proposal,reason=v.candidate(NOW,b,[q()],equity=1000.,
                                settled=1000.,peak=1000.)
    assert proposal is None


def test_bar_parser_excludes_in_progress_no_fallback():
    rows=[]
    start=NOW.replace(second=0,microsecond=0)-timedelta(minutes=3)
    for i in range(4):
        at=start+timedelta(minutes=i)
        rows.append({"time":at.isoformat(),"open":"600","high":"601",
                     "low":"599","close":"600","volume":"220",
                     "trading_sessions":"RTH"})
    parsed=v.parse_webull_bars({"symbol":"SPY","result":rows},NOW)
    assert len(parsed)==3
    assert parsed[-1].available_at<=NOW
    with pytest.raises(ValueError,match="denied"):
        v.parse_webull_bars(SimpleNamespace(status_code=403),NOW)


def test_v4_exit_uses_full_position_stops_and_ten_min_gate():
    pos={"opened_at":(NOW-timedelta(minutes=9)).isoformat(),
         "average_cost":101.65,"peak_exit_value":132.}
    q1=q(bid=.96,ask=.97)
    assert v.exit_reason(pos,q1,bars(),NOW) is None
    q2=q(bid=.91,ask=.92)
    # credit ~=89.35; 132*0.7 = 92.4, so trailing exit
    assert v.exit_reason(pos,q2,bars(),NOW)=="v4_trailing_30pct"
    pos["opened_at"]=(NOW-timedelta(minutes=11)).isoformat()
    pos["peak_exit_value"]=101.65
    assert v.exit_reason(pos,q1,bars(),NOW) is not None


def test_historical_rounding_does_not_override_live_hard_risk(monkeypatch):
    b=bars()
    monkeypatch.setattr(v,"identify_setups",lambda frames:[signal(b)])
    # The archived research study permits a 1-contract minimum despite
    # fractional budget rounding; new actual broker risk does not.
    proposal,reasons=v.candidate(NOW,b,[q(ask=3.,bid=2.99)],
        equity=1000.,settled=1000.,peak=1000.,max_contracts=3,exposure=.10)
    assert proposal is None
