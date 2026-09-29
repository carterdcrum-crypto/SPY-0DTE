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
