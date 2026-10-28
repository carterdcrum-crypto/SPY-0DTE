"""Regression tests: production V5 cannot fall back to V4."""
from __future__ import annotations

import pytest

from engine.v5_profile_lock import REQUIRED_PROFILE, profile_is_locked, selected_profile
from engine.v5_close_only_operational import PROFILE
from engine.v5_close_only_live_strategy import V5CloseOnlyLiveStrategy
from engine.v5_close_only_paper import V5CloseOnlyPaperTrader
from engine.v5_drawdown_sizing import drawdown_fraction


@pytest.mark.parametrize("variable", ["LIVE_STRATEGY_PROFILE", "PAPER_STRATEGY_PROFILE"])
def test_missing_defaults_exclusively_to_v5(monkeypatch, variable):
    monkeypatch.delenv(variable, raising=False)
    assert selected_profile(variable) == REQUIRED_PROFILE
    assert profile_is_locked(variable)


@pytest.mark.parametrize("variable", ["LIVE_STRATEGY_PROFILE", "PAPER_STRATEGY_PROFILE"])
@pytest.mark.parametrize("wrong", ["v4_original_3x", "v5_research_alt", "unknown", ""])
def test_misconfiguration_fails_closed(monkeypatch, variable, wrong):
    monkeypatch.setenv(variable, wrong)
    assert not profile_is_locked(variable)


def test_exact_archived_full_cash_policy_remains_selected():
    assert REQUIRED_PROFILE == PROFILE == "v5_close_only_unrestricted_research"
    assert V5CloseOnlyLiveStrategy.unrestricted_v5_live is True
    assert V5CloseOnlyPaperTrader is not None
    assert [drawdown_fraction(x, 1000.0) for x in (1000, 970, 940, 850)] == [1.0, 0.70, 0.40, 0.15]


def test_invalid_setting_rejected():
    with pytest.raises(ValueError):
        selected_profile("SPY_STRATEGY_PROFILE")
