"""Public-style Voss-inspired price structure checks; no proprietary claims."""
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
from unittest.mock import patch
from collections import Counter

import pytest

from engine.voss_public_structure_research import (
    ARMS,SETUPS,START_CASH,TRAIL,HARD_STOP,MAX_HOLD,
    near,strict_completed,asof_structure_signals,match,quote_cost_good,
)


def bars(prices):
    t=datetime(2026,10,6,13,30,tzinfo=timezone.utc)
    return [SimpleNamespace(
        timestamp=t+timedelta(minutes=i),
        market=SimpleNamespace(spot=p,volume_ratio=1.8,
            realized_volatility=.16)
    ) for i,p in enumerate(prices)]


def test_research_independence_and_frozen_no_take_profit():
    assert len(ARMS)==5 and len(SETUPS)==3
    assert START_CASH==1000.
    assert TRAIL==30 and HARD_STOP==35 and MAX_HOLD==10
    assert "voss_structure_combo_option_cost_gate" in ARMS


def test_completed_range_level_not_future_highs():
    data=bars([600+.03*i for i in range(80)])
    data[45].market.spot-=.5
    prior=asof_structure_signals(data[:50])
    data[56].market.spot=12345.
    after=[r for r in asof_structure_signals(data)
        if r["at"]<=data[49].timestamp+timedelta(minutes=1)]
    assert prior==after
    assert strict_completed(data,25,24)
    assert near(600,600.1,3)
    assert not near(600,601,3)


def test_level_rejection_is_confirmed_not_a_reaction_at_same_candle():
    # Create repeated 20-close resistance near 100.5; on last completed
    # close after prior touch reject down by >2bp.
    prices=[100.1]*70
    prices[21]=100.50
    prices[25]=100.50
    prices[29]=100.50
    prices[34]=100.49
    prices[35]=100.45
    rows=asof_structure_signals(bars(prices))
    assert all(row["setup"] in SETUPS for row in rows)
    assert all(row["at"].minute>=52 or row["at"].hour!=13 for row in rows)


def test_asof_bid_ask_cost_gate_and_research_arm_filters():
    q=SimpleNamespace(spread_fraction=.02,implied_volatility=.18)
    m=SimpleNamespace(realized_volatility=.16)
    assert quote_cost_good(q,m)
    q.spread_fraction=.08
    assert not quote_cost_good(q,m)
    assert match("voss_level_break_retest","level_break_retest",True)
    assert not match("voss_level_break_retest","level_rejection",True)
    assert match("voss_structure_combo","level_rejection",False)
    assert not match("voss_structure_combo_option_cost_gate","level_rejection",False)
    with pytest.raises(ValueError):
        match("pretend_Voss_guaranteed_profits","level_rejection",True)
