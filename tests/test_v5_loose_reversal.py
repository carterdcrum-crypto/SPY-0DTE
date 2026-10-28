"""Production-only wider liquidity reversal, with immutable historical defaults."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from engine.v5_archive_proxy import close_proxy_signals
from engine.v5_close_only_operational import (
    V5_REVERSAL_PRIOR_CLOSES, V5_REVERSAL_EFFICIENCY_CEILING,
    MAX_PREMIUM_SPREAD, V5_MIN_ABS_DELTA, V5_MAX_ABS_DELTA, V5_MAX_IV_RV,
    paper_candidate,
)
from engine.weekly_options_data import Bar
from engine.v4_operational import _rv


def reversal_frames():
    start = datetime(2026, 10, 13, 13, 30, tzinfo=timezone.utc)
    closes = [100.0] * 42
    closes[18] = 110.0  # In 20-close range, but outside 15-close range
    closes[39] = 105.0  # False breakout of 15-close high
    closes[40] = 99.0   # Immediate recross
    closes[41] = 98.8   # Next completed candle, no lookahead
    bars = [
        Bar(start+timedelta(minutes=i), close-.03, close+.04,
            close-.06, close, 60000 if i==40 else 15000)
        for i,close in enumerate(closes)
    ]
    frames = [
        SimpleNamespace(timestamp=b.start,
            market=SimpleNamespace(spot=b.close,
                volume_ratio=(4.0 if i==40 else 1.0)))
        for i,b in enumerate(bars)
    ]
    return bars,frames


def test_research_archival_default_preserved_and_looser_reversal_is_activated():
    bars,frames = reversal_frames()
    assert (V5_REVERSAL_PRIOR_CLOSES, V5_REVERSAL_EFFICIENCY_CEILING)==(15,.75)
    original = close_proxy_signals(frames,40,challenger=True)
    explicit_frozen = close_proxy_signals(
        frames,40,challenger=True,
        reversal_lookback=20,reversal_max_efficiency=.60)
    assert original==explicit_frozen
    assert not any(s.setup=="liquidity_reversal_CLOSE_PROXY" for s in original)
    changed = close_proxy_signals(
        frames,40,challenger=True,
        reversal_lookback=V5_REVERSAL_PRIOR_CLOSES,
        reversal_max_efficiency=V5_REVERSAL_EFFICIENCY_CEILING)
    assert [(s.side,s.setup) for s in changed if
            s.setup=="liquidity_reversal_CLOSE_PROXY"]==[
                ("put","liquidity_reversal_CLOSE_PROXY")]


def test_live_and_paper_shared_proposal_use_new_reversal_only():
    bars,frames=reversal_frames()
    now=bars[-1].available_at+timedelta(seconds=1)
    rv=_rv(bars,len(bars)-3)
    assert rv>0
    quote=SimpleNamespace(
        source="webull_production",
        timestamp_quality="option_quote_timestamp",
        timestamp=now, received_at=now, underlying_timestamp=now,
        expiration=now.astimezone(__import__("zoneinfo").ZoneInfo(
            "America/New_York")).date(),option_symbol="SPY261013P00100000",
        right="put", strike=100.0, bid=.45,ask=.46,bid_size=12,ask_size=12,
        underlying_price=98.8,
        greeks=SimpleNamespace(delta=-.50,implied_volatility=rv*.8))
    proposed,diagnostics=paper_candidate(
        now,bars,[quote],equity=1000,settled=1000,peak=1000)
    assert proposed is not None,diagnostics
    assert proposed["setup"]=="liquidity_reversal_CLOSE_PROXY"
    assert proposed["side"]=="put"
    assert proposed["quantity"]>0
    d=diagnostics["entry_diagnostics"]
    assert (d["reversal_prior_closes"],d["reversal_max_efficiency"]) == (15,.75)
    assert (MAX_PREMIUM_SPREAD,V5_MIN_ABS_DELTA,V5_MAX_ABS_DELTA,V5_MAX_IV_RV)==(
        .05,.35,.65,1.5)
    quote.bid_size=0
    declined,diag=paper_candidate(now,bars,[quote],
        equity=1000,settled=1000,peak=1000)
    assert declined is None
    assert diag["entry_diagnostics"]["rejection_code"]=="missing_depth"
