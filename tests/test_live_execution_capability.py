"""Owner-initiated Webull PREVIEW probe must never place a real order."""
from datetime import date, datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from engine import api as base, webull_live_api as mod
from engine.webull_live import WebullAccountProfile
from engine import shared_live_quotes


class FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        instant = cls(2026, 10, 19, 15, 0, tzinfo=timezone.utc)
        return instant.astimezone(tz) if tz else instant


class FakeBroker:
    def __init__(self, cash=100):
        self.cash = cash
        self.previews=[]
        self.placements=[]

    def account_profile(self):
        return WebullAccountProfile("paper-safe-account","masked","CASH")

    def balances(self, _account_id):
        return {"total_net_liquidation_value":100.34,
                "total_day_profit_loss":0,
                "account_currency_assets":[
                    {"currency":"USD","settled_cash":self.cash,"buying_power":self.cash}
                ]}

    def preview_option_order(self, order):
        self.previews.append(order)
        return {"order_preview": "accepted"}

    def place_option_order(self, order):
        self.placements.append(order)
        raise AssertionError("the preview route must NEVER send a broker order")

    def place_confirmed_option_order(self, order):
        self.placements.append(order)
        raise AssertionError("the preview route must NEVER send a broker order")


def quote():
    now=FixedDatetime.now(timezone.utc)
    return SimpleNamespace(
        option_symbol="SPY261019C00770000",
        expiration=date(2026,10,19),strike=770.,
        right="call",bid=.45,ask=.46,bid_size=5,ask_size=4,
        greeks=SimpleNamespace(delta=.51,implied_volatility=.20),
    )


def setup(monkeypatch,tmp_path, broker, *, quotes=None, ready=True):
    monkeypatch.setenv("START_COLLECTOR_IN_API","false")
    monkeypatch.setenv("START_PAPER_AUTOTRADER","false")
    monkeypatch.setenv("CONTROL_DB_PATH",str(tmp_path/"control.sqlite"))
    monkeypatch.setenv("APP_PREVIEW_MODE","false")
    base._control_store().set_mode(base.TradingMode.LIVE)
    base.app.dependency_overrides[base.require_user] = (
        lambda:base.UserIdentity("live-preview-owner","owner@example.com"))
    monkeypatch.setattr(mod,"datetime",FixedDatetime)
    monkeypatch.setattr(mod,"execution_blocks",lambda: [] if ready else ["live_orders_disabled"])
    monkeypatch.setattr(mod,"live_status",lambda:{"enabled":True,"worker_current":True})
    monkeypatch.setattr(mod.WebullLiveClient, "from_env", classmethod(lambda cls:broker))
    monkeypatch.setattr(shared_live_quotes, "collector_live_quotes",
        lambda path, now, **kw:(quote(),) if quotes is None else quotes)


def test_webull_option_preview_can_succeed_without_any_order(monkeypatch,tmp_path):
    broker=FakeBroker()
    setup(monkeypatch,tmp_path,broker)
    try:
        with TestClient(mod.app) as client:
            response=client.post("/v1/live/execution-check",
                                  json={"confirmation":"VERIFY LIVE PREVIEW ONLY"})
        assert response.status_code==200
        result=response.json()
        assert result["status"]=="BROKER_PREVIEW_ACCEPTED_NO_REAL_ORDER"
        assert result["broker_preview_accepted"] is True
        assert result["order_submitted"] is False
        assert result["broker_order_acknowledged"] is False
        assert result["verified_settled_cash"]==100
        assert result["minimum_one_contract_debit_usd"]==47
        assert len(broker.previews)==1
        assert broker.previews[0].position_intent=="BUY_TO_OPEN"
        assert broker.previews[0].quantity==1
        assert broker.placements==[]
    finally:
        base.app.dependency_overrides.clear()


def test_live_preview_exposes_cash_block_without_broker_order(monkeypatch,tmp_path):
    broker=FakeBroker(cash=25)
    setup(monkeypatch,tmp_path,broker)
    try:
        with TestClient(mod.app) as client:
            response=client.post("/v1/live/execution-check",
                                  json={"confirmation":"VERIFY LIVE PREVIEW ONLY"})
        assert response.status_code==200
        assert response.json()["status"]=="INSUFFICIENT_SETTLED_BUYING_POWER"
        assert broker.previews==[]
        assert broker.placements==[]
    finally:
        base.app.dependency_overrides.clear()


def test_live_preview_rejects_missing_fresh_quotes_and_live_lock(monkeypatch,tmp_path):
    broker=FakeBroker()
    setup(monkeypatch,tmp_path,broker,quotes=())
    try:
        with TestClient(mod.app) as client:
            response=client.post("/v1/live/execution-check",
                                  json={"confirmation":"VERIFY LIVE PREVIEW ONLY"})
        assert response.status_code==200
        assert response.json()["status"]=="NO_FRESH_ELIGIBLE_OPTION_QUOTE"
        assert broker.previews==[]
        assert broker.placements==[]
        # Live disabled means the probe must never reach the broker.
        with __import__("pytest").MonkeyPatch.context() as patch:
            patch.setattr(mod,"execution_blocks",lambda:["live_orders_disabled"])
            r=client.post("/v1/live/execution-check",
                          json={"confirmation":"VERIFY LIVE PREVIEW ONLY"})
        assert r.json()["status"]=="BLOCKED_BY_LIVE_PREREQUISITES"
        assert broker.placements==[]
    finally:
        base.app.dependency_overrides.clear()
