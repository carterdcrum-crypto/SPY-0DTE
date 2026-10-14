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
        greeks=SimpleNamespace(delta=.50,implied_volatility=.001))
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
    assert proposal["quality_score"]==6
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




def test_v4_original_does_not_require_v2_regime_or_vwap(monkeypatch):
    """Regression: frozen $5k archive selected raw trend/stretch signals."""
    b=bars()
    monkeypatch.setattr(v,"identify_setups",lambda frames:[signal(b)])
    proposal,info=v.candidate(NOW,b,[q()],equity=1000.,settled=1000.,peak=1000.)
    assert proposal is not None
    assert proposal["quality_score"]>=5
    assert "regime" not in proposal and "signal_vwap" not in proposal
    assert "Original V4" in info["reason"]


def test_v4_original_rank_targets_delta_point_five(monkeypatch):
    b=bars()
    monkeypatch.setattr(v,"identify_setups",lambda frames:[signal(b)])
    quoted=q()
    other=q(option_symbol="SPY261012C00601000",
            greeks=SimpleNamespace(delta=.55,implied_volatility=.001))
    proposal,_=v.candidate(NOW,b,[other,quoted],equity=1000.,settled=1000.,peak=1000.)
    assert proposal["symbol"]==quoted.option_symbol


def test_official_webull_nested_symbol_bars_parse():
    rows=[]
    start=NOW.replace(second=0,microsecond=0)-timedelta(minutes=3)
    for i in range(4):
        at=start+timedelta(minutes=i)
        rows.append({"time":at.isoformat(),"open":"600","high":"601",
                     "low":"599","close":"600","volume":"220"})
    data={"result":[{"symbol":"SPY","result":rows}]}
    parsed=v.parse_webull_bars(data,NOW)
    assert len(parsed)==3
    assert all(x.volume==220 for x in parsed)
    with pytest.raises(ValueError,match="SPY"):
        v.parse_webull_bars({"result":[{"symbol":"AAPL","result":rows}]},NOW)




def test_chart_is_thirty_real_completed_minute_bars_and_stale_later():
    b=bars()
    observed=b[-1].available_at+timedelta(seconds=2)
    v._store_spy_chart(b,observed)
    fresh=v.spy_chart_30m(observed)
    assert fresh["state"]=="WAITING_FOR_NEXT_BAR"
    assert fresh["window_minutes"]==30 and fresh["interval"]=="1m"
    assert len(fresh["candles"])==30
    assert all(not c["forming"] and c["volume"]==200 for c in fresh["candles"])
    assert [c["close"] for c in fresh["candles"]]==[i.close for i in b[-30:]]
    stale=v.spy_chart_30m(observed+timedelta(minutes=30))
    assert stale["state"]=="STALE"
    assert stale["candles"]==fresh["candles"]
    assert len(v.spy_chart_5m(observed)["candles"])==5  # old APK compatibility


def test_chart_in_progress_candle_updates_before_close_but_quant_excludes_it():
    b=bars()
    now=b[-1].available_at+timedelta(seconds=20)
    t=now.replace(second=0,microsecond=0)
    row={"time":t.isoformat(),"open":"603","high":"603.2","low":"602.9",
         "close":"603.1","volume":"50"}
    historical=[{"time":x.start.isoformat(),"open":x.open,"high":x.high,
                 "low":x.low,"close":x.close,"volume":x.volume} for x in b]
    response={"symbol":"SPY","result":historical+[row]}
    closed=v.parse_webull_bars(response,now)
    display=v.parse_webull_bars(response,now,include_forming=True)
    assert len(display)==len(closed)+1
    assert closed[-1]==b[-1]  # trading input never contains forming bar
    v._store_spy_chart(display,now)
    first=v.spy_chart_30m(now+timedelta(seconds=1))
    assert first["state"]=="LIVE_FORMING"
    assert first["has_forming_candle"]
    assert len(first["candles"])==30
    assert first["candles"][-1]["close"]==603.1
    assert first["candles"][-1]["forming"]
    row2={**row,"high":"603.4","close":"603.3","volume":"135"}
    later=now+timedelta(seconds=5)
    display2=v.parse_webull_bars(
        {"symbol":"SPY","result":historical+[row2]},later,include_forming=True)
    v._store_spy_chart(display2,later)
    second=v.spy_chart_30m(later)
    assert second["candles"][-1]["close"]==603.3
    assert second["candles"][-1]["volume"]==135
    assert second["candles"][-2]==first["candles"][-2]
    # Observed producer ticks do not entitle inventing subsequent movements:
    assert v.spy_chart_30m(later+timedelta(seconds=100))["state"]=="STALE"


def test_chart_fails_closed_with_missing_provider_rows_and_real_gaps():
    v._SPY_CHART.clear()
    v._SPY_CHART.update({"candles":[],"source":"Webull OpenAPI · SPY M1",
                         "reason":"no bars","observed_at":None})
    b=bars()
    observed=b[-1].available_at+timedelta(seconds=1)
    v._store_spy_chart(b[-3:],observed)
    assert len(v.spy_chart_30m(observed)["candles"])==3  # session warmup
    v._store_spy_chart(b,observed)
    prev=v.spy_chart_30m(observed)["candles"]
    v._store_spy_chart([],observed+timedelta(seconds=5),
                      reason="provider unavailable")
    assert v.spy_chart_30m(observed+timedelta(seconds=5))["candles"]==prev
    assert v.spy_chart_30m(observed+timedelta(minutes=4))["state"]=="STALE"
    gap=b[-30:-20]+b[-16:]
    v._store_spy_chart(gap,observed)
    got=v.spy_chart_30m(observed)["candles"]
    assert len(got)==16  # no invented four-minute OHLCV gap
    assert got[0]["time"]==gap[10].start.isoformat()



def test_webull_history_requests_realtime_forming_bar_separately_from_quant():
    from engine.v4_operational import WebullMinuteBarCache
    data=bars()
    now=data[-1].available_at+timedelta(seconds=20)
    sample=[{"time":b.start.isoformat(),"open":b.open,"high":b.high,
             "low":b.low,"close":b.close,"volume":b.volume} for b in data]
    next_at=data[-1].available_at
    forming={"time":next_at.isoformat(),"open":"603","high":"603.2",
             "low":"602.9","close":"603.1","volume":"80"}
    seen=[]
    def history(*args,**kwargs):
        seen.append(kwargs.get("real_time_required"))
        return {"symbol":"SPY","result":sample+([forming] if kwargs["real_time_required"] else [])}
    provider=SimpleNamespace(client=SimpleNamespace(
        market_data=SimpleNamespace(get_history_bar=history)))
    cache=WebullMinuteBarCache()
    finished=cache.refresh(provider,now)
    assert seen==[False,True]
    assert len(finished)==len(data) and not any(b.start==next_at for b in finished)
    chart=v.spy_chart_30m(now)
    assert len(chart["candles"])==30
    assert chart["state"]=="LIVE_FORMING"
    assert chart["candles"][-1]["forming"] and chart["candles"][-1]["close"]==603.1
    # Sub-minute updates overwrite only current forming candle, not quant bars.
    forming["close"]="603.3"
    forming["high"]="603.4"  # maintain authentic OHLC consistency on refresh
    newer=now+timedelta(seconds=5)
    cache.refresh(provider,newer)
    assert len(cache.bars)==len(data)
    assert v.spy_chart_30m(newer)["candles"][-1]["close"]==603.3


def test_webull_realtime_denial_cannot_block_valid_completed_quant_bars():
    from engine.v4_operational import WebullMinuteBarCache
    data=bars()
    now=data[-1].available_at+timedelta(seconds=20)
    sample=[{"time":b.start.isoformat(),"open":b.open,"high":b.high,
             "low":b.low,"close":b.close,"volume":b.volume} for b in data]
    def history(*args,**kwargs):
        if kwargs["real_time_required"]:
            return SimpleNamespace(status_code=403)
        return {"symbol":"SPY","result":sample}
    provider=SimpleNamespace(client=SimpleNamespace(
        market_data=SimpleNamespace(get_history_bar=history)))
    cache=WebullMinuteBarCache()
    assert len(cache.refresh(provider,now))==80
    chart=v.spy_chart_30m(now)
    assert chart["state"]=="WAITING_FOR_NEXT_BAR"
    assert "subscription" in chart["reason"]
    assert chart["has_forming_candle"] is False
