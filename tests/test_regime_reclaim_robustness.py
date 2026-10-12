"""Robustness checks should not turn selected backtest wins into guarantees."""
import csv,json
from engine.regime_reclaim_robustness import wilson,run


def test_wilson_is_not_guaranteed_high_win_rate():
    assert wilson(0,0) is None
    lo,hi=wilson(76,146)
    assert lo<52.05<hi and lo<50
    assert hi<70


def test_friction_stress_and_calendar_block_bootstrap(tmp_path):
    ledger=tmp_path/"trades.csv"
    with ledger.open("w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=[
            "day","arm","side","setup","pnl_usd","entry_at"])
        w.writeheader()
        w.writerow({"day":"2026-10-05",
                    "arm":"blend_trend_or_stretch_cost_aware",
                    "side":"call","setup":"trend_reclaim",
                    "pnl_usd":6.,"entry_at":"2026-10-05T14:00:00+00:00"})
        w.writerow({"day":"2026-10-06",
                    "arm":"blend_trend_or_stretch_cost_aware",
                    "side":"put","setup":"stretch_reclaim",
                    "pnl_usd":-2.,"entry_at":"2026-10-06T14:01:00+00:00"})
    archive=tmp_path/"archive";archive.mkdir()
    for d in ["2026-10-05","2026-10-06","2026-10-07"]:
        (archive/f"spy_0dte_{d}.csv.gz").write_bytes(b"")
    from unittest.mock import patch
    with patch("engine.regime_reclaim_robustness.BOOT_REPS",60):
        report=run(ledger,archive,tmp_path/"report.json")
    assert report["archive_sessions"]==3
    assert report["trades"]==2
    assert report["win_rate_pct"]==50.
    assert report["daily_block_bootstrap"]["extra_0_per_trade_usd"]["aggregate_cost_stressed_historical_usd"]==4.
    assert report["daily_block_bootstrap"]["extra_2_per_trade_usd"]["aggregate_cost_stressed_historical_usd"]==0.
    assert report["daily_block_bootstrap"]["extra_5_per_trade_usd"]["aggregate_cost_stressed_historical_usd"]==-6.
    assert report["sum_without_best_day_usd"]==-2.
    assert report["by_option_side"]["call"]["trades"]==1
    assert json.loads((tmp_path/"report.json").read_text())["trades"]==2
