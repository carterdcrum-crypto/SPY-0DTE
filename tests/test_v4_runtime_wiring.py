"""V4 3x runtime must be selectable without unlocking real orders."""
from datetime import datetime, timezone
from pathlib import Path

from engine.paper_runner import create_trader
from engine.v4_paper_trader import V4QualityPaperTrader
from engine.v4_live_strategy import V4QualityLiveStrategy


def test_v4_paper_profile_is_selected_without_credentials_and_never_places_broker_orders(
        monkeypatch,tmp_path):
    monkeypatch.setenv("SPY_STRATEGY_PROFILE","v4_original_3x")
    monkeypatch.setenv("PAPER_DB_PATH",str(tmp_path/"paper.sqlite"))
    monkeypatch.setenv("COLLECTOR_DB_PATH",str(tmp_path/"market.sqlite"))
    monkeypatch.setenv("PAPER_STARTING_CASH","1000")
    monkeypatch.setenv("WEBULL_ENVIRONMENT","sandbox")
    monkeypatch.setenv("MARKET_DATA_PROVIDER","tradier")
    monkeypatch.delenv("TRADIER_ACCESS_TOKEN",raising=False)
    monkeypatch.delenv("TRADIER_MARKET_DATA_TOKEN",raising=False)
    monkeypatch.delenv("WEBULL_LIVE_APP_KEY",raising=False)
    monkeypatch.delenv("WEBULL_LIVE_APP_SECRET",raising=False)
    trader=create_trader(lambda:"PAPER",delayed=False)
    assert isinstance(trader,V4QualityPaperTrader)
    result=trader.tick(datetime(2026,10,12,15,0,tzinfo=timezone.utc))
    assert result["state"] in {"WAITING_V4_BARS","WAITING_V4_QUOTES","NO_SIGNAL"}
    assert trader.account_store.snapshot().open_positions==0


def test_v4_live_adapter_is_quant_only_with_no_ai_inheritance():
    from engine.paper_ai_autotrader import AIAugmentedDynamicExitTrader
    assert not issubclass(V4QualityLiveStrategy,AIAugmentedDynamicExitTrader)
    assert 'ai_engine' not in vars(V4QualityLiveStrategy(None,'test'))


def test_v5_paper_uses_single_collector_option_feed_when_live_is_active(
        monkeypatch, tmp_path):
    """PAPER must never create a second Webull options poller in LIVE mode."""
    from engine import paper_runner, live_market, webull_live
    from engine.v5_close_only_paper import V5CloseOnlyPaperTrader

    monkeypatch.setenv("PAPER_STRATEGY_PROFILE","v5_close_only_unrestricted_research")
    monkeypatch.setenv("SPY_STRATEGY_PROFILE","v5_close_only_unrestricted_research")
    monkeypatch.setenv("PAPER_DB_PATH",str(tmp_path/"paper.sqlite"))
    monkeypatch.setenv("COLLECTOR_DB_PATH",str(tmp_path/"market.sqlite"))
    monkeypatch.setenv("PAPER_STARTING_CASH","1000")
    monkeypatch.setattr(webull_live, "production_credentials",
                        lambda: ("example-api-key","example-api-secret"))

    class FakeStockBarProvider:
        def collect_once(self, *_args, **_kwargs):
            raise AssertionError("paper options cannot poll Webull a second time")
    monkeypatch.setattr(live_market, "LiveDataProvider",
                        lambda *args,**kwargs: FakeStockBarProvider())
    trader=paper_runner.create_trader(lambda: "PAPER",delayed=False)
    assert isinstance(trader,V5CloseOnlyPaperTrader)
    assert trader.production_provider is None
    assert isinstance(trader.bar_provider, FakeStockBarProvider)
    assert Path(trader.market_reader.path) == tmp_path/"market.sqlite"
    # PAPER options always read production collector SQLite, not the bar SDK.
    assert trader._quotes(datetime(2026,10,19,15,tzinfo=timezone.utc)) == ()
