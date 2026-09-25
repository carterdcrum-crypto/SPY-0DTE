from fastapi.testclient import TestClient

from engine import api as base_api
from engine.api_v2 import app


def _authorized_user():
    return base_api.UserIdentity(subject="autonomy-test", email="owner@example.com")


def _configure(monkeypatch, tmp_path):
    monkeypatch.setenv("START_COLLECTOR_IN_API", "false")
    monkeypatch.setenv("START_PAPER_AUTOTRADER", "false")
    monkeypatch.setenv("CONTROL_DB_PATH", str(tmp_path / "control.sqlite"))
    monkeypatch.setenv("COLLECTOR_DB_PATH", str(tmp_path / "market.sqlite"))
    monkeypatch.setenv("PAPER_DB_PATH", str(tmp_path / "paper.sqlite"))
    monkeypatch.setenv("PAPER_STARTING_CASH", "115")
    monkeypatch.delenv("TRADIER_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("TRADIER_ACCOUNT_ID", raising=False)
    base_api.app.dependency_overrides[base_api.require_user] = _authorized_user


def test_autonomous_paper_can_arm_without_tradier_credentials(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    try:
        with TestClient(app) as client:
            response = client.post("/v1/paper/autonomy", json={"armed": True})
            status = client.get("/v1/status")

        assert response.status_code == 200
        autonomy = response.json()
        assert autonomy["armed"] is True
        assert autonomy["mode"] == "PAPER"
        assert autonomy["execution"] == "local_paper_ledger"
        assert autonomy["broker_credentials_required"] is False
        assert autonomy["live_order_submission"] is False
        assert autonomy["production_isolated"] is True

        assert status.status_code == 200
        assert status.json()["paper_autonomy"]["armed"] is True
        assert status.json()["live_broker"]["real_order_submission"] is False
    finally:
        base_api.app.dependency_overrides.clear()


def test_disarm_switches_to_shadow_and_blocks_new_entries(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    try:
        with TestClient(app) as client:
            client.post("/v1/paper/autonomy", json={"armed": True})
            response = client.post("/v1/paper/autonomy", json={"armed": False})

        assert response.status_code == 200
        autonomy = response.json()
        assert autonomy["armed"] is False
        assert autonomy["mode"] == "SHADOW"
        assert autonomy["new_entries_enabled"] is False
        assert autonomy["disarmed_behavior"] == "block_new_entries_manage_existing_positions"
    finally:
        base_api.app.dependency_overrides.clear()
