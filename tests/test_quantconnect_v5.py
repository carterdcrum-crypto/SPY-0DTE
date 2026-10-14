"""QC V4/V5 research regression checks. Synthetic series ONLY for unit tests.

No prices, fills, probabilities, or historical performance are inferred.
"""
import ast
import math
import random
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "quantconnect"))
from v5_core import (
    latest_signals, contract_quantity, realized_volatility, quality_score,
    score_equity, Signal,
)
from engine.regime_reclaim_frozen_signal import identify_setups


def _market(seed=23, n=100):
    r = random.Random(seed)
    prices = [500.0]
    for i in range(n-1):
        prices.append(max(10,prices[-1]+r.uniform(-.60,.60)+
                          (.08 if i%27<10 else -.05)))
    highs = [x+.15 for x in prices]
    lows = [x-.15 for x in prices]
    vols = [10000.+1000.*r.randrange(12) for _ in prices]
    return prices,highs,lows,vols


def test_v4_closed_bar_signal_parity_with_frozen_reference():
    for seed in range(10):
        closes,highs,lows,vols = _market(seed)
        frames = []
        start = datetime(2026,10,13,9,30,tzinfo=ZoneInfo("America/New_York"))
        for i, (close,vol) in enumerate(zip(closes,vols)):
            rvol = vol/(sum(vols[i-20:i])/20.) if i>=20 else 0.
            frames.append(SimpleNamespace(timestamp=start+timedelta(minutes=i),
                                          market=SimpleNamespace(
                                              spot=close,volume_ratio=rvol)))
        original = identify_setups(frames)
        for i in range(35,len(closes)):
            received = latest_signals(closes[:i+1],highs[:i+1],
                                      lows[:i+1],vols[:i+1],"v4_original")
            expected = [(row["side"],row["setup"]) for row in original
                        if row["at"]==frames[i].timestamp+timedelta(minutes=1)]
            assert [(x.side,x.setup) for x in received] == expected


def test_v5_requires_completed_history_and_no_future_bar():
    p,h,l,v = _market()
    assert latest_signals(p[:35],h[:35],l[:35],v[:35]) == []
    assert latest_signals(p,h,l,v) == latest_signals(
        p,h,l,v)  # deterministic; no random inference
    assert realized_volatility(p)>0


def test_costs_caps_and_settlement_cash():
    common=dict(score=5,equity=1000.,settled_cash=1000.,
                peak_equity=1000.,ask=.50,bid_size=99,ask_size=99)
    assert contract_quantity("v4_original",**common)==3
    assert contract_quantity("v4_original",**{**common,"score":4})==2
    assert contract_quantity("v4_original",**{**common,"score":3})==1
    assert contract_quantity("v5_unrestricted_research",**common)>3
    assert contract_quantity("v5_current",**common)<=int(.30*1000/51.65)
    assert contract_quantity("v5_optimized",**common)<=int(.35*1000/51.65)
    assert contract_quantity("v5_current",**{**common,"settled_cash":10})==0
    assert contract_quantity("v5_unrestricted_research",**{**common,"ask_size":2})==2
    assert contract_quantity("v5_current",**{**common,"equity":910})==0
    assert contract_quantity("v5_current",**{**common,"score":2})==0


def test_quality_score_and_metrics_from_observed_curve_only():
    sig = Signal("call","momentum_ignition",1.8,5.,.40)
    assert quality_score(sig,.01,.5,.18,.20)==6
    assert score_equity([]) is None
    assert score_equity([1000,1250,5100,4800]) == {
        "ending_equity":4800,"max_eod_dd_pct":round(100*300/5100,2),
        "sessions_to_5k":3}


def test_qc_python_syntax_compiles_without_lean_sdk():
    path=Path(__file__).resolve().parents[1]/"quantconnect"/"v5_main.py"
    source=path.read_text()
    tree=ast.parse(source)
    compile(tree,str(path),"exec")
    assert "self.set_brokerage_model" in source
    assert "AccountType.CASH" in source
    assert "quote_bars.get" in source
    assert "self.market_order" in source
    assert "v5_unrestricted_research" in source
    assert "BrokerageName.QUANTCONNECT_BROKERAGE" in source
