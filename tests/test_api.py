from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from engine.api import UserIdentity, app, require_user


def _authorized_user():
    return UserIdentity(subject="test-user", email="owner@example.com")


def test_health_is_public(monkeypatch, tmp_path):
    monkeypatch.setenv("START_COLLECTOR_IN_API", "false")
    monkeypatch.setenv("CONTROL_DB_PATH", str(tmp_path / "control.sqlite"))
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_paper_mode_can_be_selected_but_live_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setenv("START_COLLECTOR_IN_API", "false")
    monkeypatch.setenv("CONTROL_DB_PATH", str(tmp_path / "control.sqlite"))
    monkeypatch.setenv("COLLECTOR_DB_PATH", str(tmp_path / "market.sqlite"))
    monkeypatch.setenv("WEBULL_ENVIRONMENT", "sandbox")
    monkeypatch.setenv("ALLOW_LIVE_ORDERS", "false")
    monkeypatch.setenv("LIVE_ORDER_EXECUTOR_READY", "false")
    app.dependency_overrides[require_user] = _authorized_user
    try:
        with TestClient(app) as client:
            paper = client.post("/v1/mode", json={"mode": "PAPER"})
            live = client.post(
                "/v1/mode",
                json={"mode": "LIVE", "confirmation": "ENABLE LIVE TRADING"},
            )
        assert paper.status_code == 200
        assert paper.json()["mode"] == "PAPER"
        assert live.status_code == 409
        assert "live_orders_disabled" in live.json()["detail"]["reasons"]
    finally:
        app.dependency_overrides.clear()


def test_webull_credentials_are_encrypted_and_never_echoed(monkeypatch, tmp_path):
    monkeypatch.setenv("START_COLLECTOR_IN_API", "false")
    monkeypatch.setenv("CONTROL_DB_PATH", str(tmp_path / "control.sqlite"))
    monkeypatch.setenv("APP_ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))
    app.dependency_overrides[require_user] = _authorized_user
    try:
        with TestClient(app) as client:
            response = client.post(
                "/v1/broker/webull",
                json={
                    "environment": "sandbox",
                    "app_key": "paper-app-key-123",
                    "app_secret": "paper-app-secret-456",
                },
            )
            profiles = client.get("/v1/broker/webull")
        assert response.status_code == 200
        assert response.json()["configured"] is True
        serialized = response.text + profiles.text
        assert "paper-app-key-123" not in serialized
        assert "paper-app-secret-456" not in serialized
        assert profiles.json()["sandbox"]["configured"] is True
    finally:
        app.dependency_overrides.clear()


def test_status_exposes_second_resolution_cadence(monkeypatch, tmp_path):
    monkeypatch.setenv("START_COLLECTOR_IN_API", "false")
    monkeypatch.setenv("CONTROL_DB_PATH", str(tmp_path / "control.sqlite"))
    monkeypatch.setenv("COLLECTOR_DB_PATH", str(tmp_path / "market.sqlite"))
    app.dependency_overrides[require_user] = _authorized_user
    try:
        with TestClient(app) as client:
            response = client.get("/v1/status")
        assert response.status_code == 200
        cadence = response.json()["cadence"]
        assert cadence["engine_tick_seconds"] == 1.0
        assert cadence["sandbox_active_option_refresh_seconds"] == 2.0
    finally:
        app.dependency_overrides.clear()
