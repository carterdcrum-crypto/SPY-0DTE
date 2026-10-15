"""Regression tests for exact-cash archival V5 research reruns."""
from collections import Counter
import pytest

from engine.v4_dynamic_size_backtest import summary
from engine.v5_archive_proxy import (
    ARMS, first_hit, proxy_size, rolling_42, run,
)


def test_unrestricted_contract_sizing_uses_actual_151_22_cash():
    arm = "v5_close_proxy_unrestricted_RESEARCH_ONLY"
    assert proxy_size(arm, equity=151.22, settled=151.22,
                      peak=151.22, debit=75.61, score=0,
                      starting_cash=151.22) == 2
    assert proxy_size(arm, equity=151.22, settled=151.22,
                      peak=151.22, debit=75.62, score=0,
                      starting_cash=151.22) == 1
    assert proxy_size(arm, equity=151.22, settled=151.22,
                      peak=151.22, debit=151.23, score=0,
                      starting_cash=151.22) == 0


def test_research_summary_tracks_exact_micro_account_drawdown_and_return():
    state = {
        "settled": 125.22, "pending": 0,
        "daily": [{"day":"d1","equity_usd":181.22},
                  {"day":"d2","equity_usd":125.22}],
        "trades": [], "skips":Counter(), "exits":Counter(),
    }
    result = summary(state, starting_cash=151.22)
    assert result["start_equity_usd"] == 151.22
    assert result["net_profit_usd"] == -26.00
    assert result["max_end_of_day_drawdown_usd"] == 56
    assert result["drawdown_peak_day"] == "d1"
    assert result["drawdown_trough_day"] == "d2"
    assert first_hit(state, 151.22*5) is None


def test_small_balance_backtest_requires_real_archive(tmp_path):
    with pytest.raises(ValueError, match="archive absent"):
        run(tmp_path, tmp_path/"no_output", starting_cash=151.22,
            arms=("v5_close_proxy_unrestricted_RESEARCH_ONLY",))


def test_starting_cash_validation_rejects_non_cents_and_unknown_arm(tmp_path):
    with pytest.raises(ValueError, match="dollar-and-cent"):
        run(tmp_path,tmp_path/"out",starting_cash=151.225)
    with pytest.raises(ValueError, match="research arm"):
        run(tmp_path,tmp_path/"out",starting_cash=151.22,arms=("fake",))


def test_rolling_42_uses_new_account_starting_balance():
    state = {"daily":[{"equity_usd":151.22} for _ in range(43)]}
    result = rolling_42(state, starting_cash=151.22)
    assert result["windows"] == 2
    assert result["5x_windows"] == 0
    assert result["halving_windows"] == 0
