"""No hindsight and no options-selling proceeds reuse in V4 quality scaling."""
from types import SimpleNamespace
import pytest
from engine.v4_quality_sizing import score_at_entry,size,run

def sample(setup="trend_reclaim",rvol=1.4,spread=.012,
           delta=.5,iv=.20,rv=.20,trend=5.,efficiency=.4):
    quote=SimpleNamespace(spread_fraction=spread,delta=delta,
        implied_volatility=iv)
    item={"signal":{"setup":setup,"volume_ratio":rvol,
                    "trend_bps":trend,"efficiency":efficiency},
          "entry_idx":1,"quote":quote}
    frames=[SimpleNamespace(market=SimpleNamespace(realized_volatility=rv))
            for _ in range(3)]
    return item,frames

def test_quality_score_uses_only_asof_frozen_signal_and_entry_quote():
    item,frames=sample()
    assert score_at_entry(item,frames)==6
    item["signal"]["volume_ratio"]=.8
    item["quote"].spread_fraction=.03
    assert score_at_entry(item,frames)==4
    frames[2].market.realized_volatility=.000001 # future frame must not count
    assert score_at_entry(item,frames)==4

def test_quality_scaling_requires_premium_budget_and_cash():
    assert size("v4_quality_2x",score=6,equity=3000.,
                settled=3000.,peak=3000.,debit=100.)==2
    assert size("v4_quality_3x",score=6,equity=3000.,
                settled=3000.,peak=3000.,debit=100.)==3
    assert size("v4_quality_3x",score=3,equity=3000.,
                settled=3000.,peak=3000.,debit=100.)==1
    assert size("v4_quality_3x",score=6,equity=1000.,
                settled=150.,peak=1000.,debit=100.)==1

def test_drawdown_blocks_only_extras():
    assert size("v4_quality_2x",score=6,equity=2700.,
                settled=2700.,peak=3000.,debit=100.)==1
    assert size("v4_quality_2x_no_4pct_dd_gate",score=6,equity=2700.,
                settled=2700.,peak=3000.,debit=100.)==2

def test_no_real_archive_means_no_backtest(tmp_path):
    with pytest.raises(ValueError,match="inspected SPY archive"):
        run(tmp_path,tmp_path/"out")
