from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from cryptography.fernet import Fernet

from engine import api, api_v2, webull_live_api
from engine.live_autonomy_store import ARM_AUTONOMY, LiveAutonomyStore, autonomy_db_path
from engine.webull_live import production_credentials, webull_env_status, WebullLiveError


@pytest.fixture
def controls(tmp_path, monkeypatch):
    for key,name in [("CONTROL_DB_PATH","control"),("PAPER_DB_PATH","paper"),
                     ("LIVE_RISK_DB_PATH","risk"),("LIVE_AUTONOMY_DB_PATH","live"),("COLLECTOR_DB_PATH","market")]:
        monkeypatch.setenv(key, str(tmp_path/(name+".sqlite")))
    monkeypatch.setenv("APP_PREVIEW_MODE", "false")
    monkeypatch.setenv("ALLOW_LIVE_ORDERS", "false")
    monkeypatch.setenv("START_PAPER_AUTOTRADER", "false")
    monkeypatch.setenv("START_COLLECTOR_IN_API", "false")
    monkeypatch.setenv("APP_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("WEBULL_ENVIRONMENT", "sandbox")
    monkeypatch.delenv("WEBULL_LIVE_APP_KEY", raising=False)
    monkeypatch.delenv("WEBULL_LIVE_APP_SECRET", raising=False)
    api.app.dependency_overrides[api.require_user] = lambda: api.UserIdentity("owner", "owner@example.com")
    try:
        with TestClient(api.app) as client:
            yield client
    finally:
        api.app.dependency_overrides.clear()


def save_limits(client):
    response = client.post("/v1/live/risk-envelope", json={"daily_loss_limit":25,"daily_gain_limit":40,
        "max_account_exposure_pct":.2,"max_contracts":1,"confirmation":"ARM LIVE TODAY"})
    assert response.status_code == 200


def test_auto_trade_switch_persists_then_stops(controls):
    save_limits(controls)
    response = controls.post("/v1/live/autonomy", json={"enabled":True,"confirmation":ARM_AUTONOMY})
    assert response.status_code == 200
    assert response.json()["enabled"] is True
    assert api._control_store().get_mode() is api.TradingMode.LIVE
    assert LiveAutonomyStore(autonomy_db_path()).policy()["enabled"]
    assert "live_orders_disabled" in response.json()["prerequisites"]
    response = controls.post("/v1/live/autonomy", json={"enabled":False,"confirmation":"STOP AUTONOMOUS LIVE"})
    assert response.status_code == 200 and not response.json()["enabled"]


def test_switch_requires_saved_limits_and_never_changes_risk_silently(controls):
    assert controls.post("/v1/live/autonomy", json={"enabled":True,"confirmation":ARM_AUTONOMY}).status_code == 400
    save_limits(controls)
    controls.post("/v1/live/autonomy", json={"enabled":True,"confirmation":ARM_AUTONOMY})
    response = controls.post("/v1/live/risk-envelope", json={"daily_loss_limit":500,"daily_gain_limit":40,
        "max_account_exposure_pct":.2,"max_contracts":1,"confirmation":"ARM LIVE TODAY"})
    assert response.status_code == 409
    assert LiveAutonomyStore(autonomy_db_path()).policy()["limits"]["daily_loss_limit"] == 25


def test_paper_selection_turns_auto_trade_off(controls):
    save_limits(controls)
    controls.post("/v1/live/autonomy", json={"enabled":True,"confirmation":ARM_AUTONOMY})
    assert controls.post("/v1/mode", json={"mode":"PAPER"}).status_code == 200
    assert not LiveAutonomyStore(autonomy_db_path()).policy()["enabled"]


def test_configured_autonomy_prevents_manual_order_path(controls):
    save_limits(controls)
    controls.post("/v1/live/autonomy", json={"enabled":True,"confirmation":ARM_AUTONOMY})
    response = controls.post("/v1/live/order/prepare", json={"confirmation":"PREPARE LIVE ORDER"})
    assert response.status_code == 409
    assert "autonomous executor" in response.json()["detail"]


def test_sandbox_keys_do_not_count_as_production(controls, monkeypatch):
    monkeypatch.setenv("WEBULL_APP_KEY", "sandbox-key")
    monkeypatch.setenv("WEBULL_APP_SECRET", "sandbox-secret")
    assert not webull_env_status()["configured"]
    with pytest.raises(WebullLiveError): production_credentials()


def test_saved_production_credentials_are_used_and_never_echoed(controls):
    response = controls.post("/v1/broker/webull", json={"environment":"production",
        "app_key":"production-key-test", "app_secret":"production-secret-test"})
    assert response.status_code == 200
    assert production_credentials() == ("production-key-test", "production-secret-test")
    assert webull_env_status()["configured"]
    assert "production-secret-test" not in response.text
    assert "production-key-test" not in response.text
    save_limits(controls)
    controls.post("/v1/live/autonomy", json={"enabled":True,"confirmation":ARM_AUTONOMY})
    assert controls.post("/v1/broker/webull", json={"environment":"production",
        "app_key":"replacement-key", "app_secret":"replacement-secret"}).status_code == 409


def test_status_uses_live_worker_and_does_not_poll_broker_per_viewer(controls, monkeypatch):
    save_limits(controls)
    controls.post("/v1/live/autonomy", json={"enabled":True,"confirmation":ARM_AUTONOMY})
    def forbidden(*args, **kwargs):
        pytest.fail("viewers must not poll the broker while the live worker owns reconciliation")
    monkeypatch.setattr(webull_live_api, "cached_webull_guard", forbidden)
    monkeypatch.setattr(api_v2, "cached_live_broker_guard", forbidden)
    payload = controls.get("/v1/status").json()
    assert payload["live_alert"] is None
    assert payload["decision"]["state"] == payload["live_autonomy"]["state"]
    assert payload["live_risk"]["standing_authorization"]


def test_paper_autonomy_can_trade_simultaneously_with_live_without_disarming_broker(controls):
    """Live and paper authorization are independent durable switches."""
    save_limits(controls)
    enabled = controls.post("/v1/live/autonomy", json={
        "enabled": True, "confirmation": ARM_AUTONOMY,
    })
    assert enabled.status_code == 200
    assert enabled.json()["enabled"] is True
    assert api._control_store().get_mode() is api.TradingMode.LIVE

    armed = controls.post("/v1/paper/autonomy", json={"armed": True})
    assert armed.status_code == 200
    assert armed.json()["armed"] is True
    assert armed.json()["mode"] == "PAPER"
    assert armed.json()["primary_mode"] == "LIVE"
    assert armed.json()["simultaneous_live_and_paper"] is True
    assert api._control_store().get_mode() is api.TradingMode.LIVE
    assert api_v2._strategy_mode() == "PAPER"
    assert LiveAutonomyStore(autonomy_db_path()).policy()["enabled"] is True
    status = controls.get("/v1/status").json()
    assert status["mode"] == "LIVE"
    assert status["live_autonomy"]["enabled"] is True
    assert status["paper_autonomy"]["armed"] is True
    assert status["paper_autonomy"]["live_order_submission"] is False
    assert status["paper_autonomy"]["production_isolated"] is True

    # Reopening the control database (as after a Railway restart) retains
    # both switches. No trading capital or paper ledger is reset.
    assert api.ControlStore(api._control_store().path).paper_armed() is True
    stopped = controls.post("/v1/paper/autonomy", json={"armed": False})
    assert stopped.status_code == 200
    assert stopped.json()["primary_mode"] == "LIVE"
    assert stopped.json()["mode"] == "SHADOW"
    assert api_v2._strategy_mode() == "SHADOW"
    assert LiveAutonomyStore(autonomy_db_path()).policy()["enabled"] is True
    assert api._control_store().get_mode() is api.TradingMode.LIVE


def test_enabling_live_preserves_existing_simulator_arming(controls):
    assert controls.post("/v1/paper/autonomy", json={"armed": True}).status_code == 200
    save_limits(controls)
    assert controls.post("/v1/live/autonomy", json={
        "enabled": True, "confirmation": ARM_AUTONOMY,
    }).status_code == 200
    assert api._control_store().get_mode() is api.TradingMode.LIVE
    assert api._control_store().paper_armed() is True
    assert api_v2._strategy_mode() == "PAPER"
    # Standard explicit mode selection still retains its earlier safety:
    # switching from LIVE to standalone PAPER disables real-money autonomy.
    result = controls.post("/v1/mode", json={"mode":"PAPER"})
    assert result.status_code == 200
    assert api._control_store().paper_armed() is True
    assert not LiveAutonomyStore(autonomy_db_path()).policy()["enabled"]
