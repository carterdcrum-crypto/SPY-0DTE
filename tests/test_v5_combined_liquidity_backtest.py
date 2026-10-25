"""Guardrails for the research-only combined V5 liquidity sensitivity."""
from types import SimpleNamespace
from unittest.mock import patch

from quantconnect.v5_core import Signal
from engine.v5_combined_liquidity_backtest import combined_signals


def _frames(prior_break):
    # The prior 20 closes are 100.00; current prior close is a controlled
    # upward excursion; current close is back inside the range.
    prices = [100.0] * 40
    prices[-2] = 100.0 * (1.0 + prior_break)
    prices[-1] = 99.99
    return [SimpleNamespace(market=SimpleNamespace(spot=x)) for x in prices]


def _signals():
    return (
        Signal("call", "momentum_ignition_CLOSE_PROXY", 1.5, 4.0, .45),
        Signal("put", "liquidity_reversal_CLOSE_PROXY", 1.5, 4.0, .25),
        Signal("call", "volatility_expansion_CLOSE_PROXY", 1.5, 4.0, .45),
    )


def test_current_v5_base_signals_are_identical():
    frames = _frames(.0001)
    with patch("engine.v5_combined_liquidity_backtest.close_proxy_signals",
               return_value=_signals()):
        assert combined_signals(frames, 39, sweep_bps=0) == _signals()


def test_sweep_2bps_filters_only_liquidity_if_break_is_too_small():
    frames = _frames(.0001)
    with patch("engine.v5_combined_liquidity_backtest.close_proxy_signals",
               return_value=_signals()):
        out = combined_signals(frames, 39, sweep_bps=2)
    assert [s.setup for s in out] == [
        "momentum_ignition_CLOSE_PROXY", "volatility_expansion_CLOSE_PROXY",
    ]


def test_sweep_2bps_keeps_liquidity_if_break_is_large_enough():
    frames = _frames(.0003)
    with patch("engine.v5_combined_liquidity_backtest.close_proxy_signals",
               return_value=_signals()):
        assert combined_signals(frames, 39, sweep_bps=2) == _signals()


def test_unchanged_existing_v5_momentum_and_expansion_have_no_new_gate():
    frames = _frames(0)
    others = (_signals()[0], _signals()[2])
    with patch("engine.v5_combined_liquidity_backtest.close_proxy_signals",
               return_value=others):
        assert combined_signals(frames, 39, sweep_bps=2) == others
