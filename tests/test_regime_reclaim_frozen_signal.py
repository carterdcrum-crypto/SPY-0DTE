"""No accidental threshold changes or reconstructed intrabar future signals."""
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
from engine.regime_reclaim_frozen_signal import identify_setups,simulate

def fake_bars(closes,start):
    return [SimpleNamespace(
        timestamp=start+timedelta(minutes=i),
        market=SimpleNamespace(spot=p,volume_ratio=1.0,realized_volatility=.2))
        for i,p in enumerate(closes)]

def test_flat_price_has_no_signal():
    frame=fake_bars([100.] * 100, datetime(2026,10,12,13,30,tzinfo=timezone.utc))
    assert identify_setups(frame)==[]

def test_frozen_30pct_trail_and_independent_hard_loss():
    start=datetime(2026,10,12,15,0,tzinfo=timezone.utc)
    prices=[(start+timedelta(seconds=60*i),p) for i,p in enumerate((1.20,1.50,.99))]
    a=simulate(1.0,prices,30)
    assert a["exit_reason"]=="trailing_stop"
    assert a["pnl_usd"]<0.0
    b=simulate(1.0,[(start,.60)],30)
    assert b["exit_reason"]=="independent_hard_stop_35pct"
