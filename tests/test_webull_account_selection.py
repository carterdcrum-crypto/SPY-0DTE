"""Selection is owner-authenticated, cash-only, and survives service restarts."""
from pathlib import Path

from fastapi.testclient import TestClient

from engine import api as base
from engine import webull_live_api as live_api
from engine.webull_live import selected_webull_account_id


class FakeBroker:
    def accounts(self):
        return (
            {"account_id": "cash-a", "account_number": "AB1234", "account_type": "CASH"},
            {"account_id": "cash-b", "account_number": "CD5678", "account_type": "CASH"},
            {"account_id": "margin-c", "account_number": "EF9999", "account_type": "MARGIN"},
        )

    def open_orders(self, account_id):
        return ()


def test_single_owner_can_select_one_webull_cash_account(monkeypatch, tmp_path):
    monkeypatch.setenv("START_COLLECTOR_IN_API", "false")
    monkeypatch.setenv("LIVE_AUTONOMY_DB_PATH", str(tmp_path / "autonomy.sqlite"))
    monkeypatch.setenv("WEBULL_SELECTED_ACCOUNT_PATH", str(tmp_path / "selected"))
    monkeypatch.delenv("WEBULL_ACCOUNT_ID", raising=False)
    monkeypatch.setattr(live_api, "webull_env_status", lambda: {"configured": True})
    monkeypatch.setattr(live_api.WebullLiveClient, "from_env", lambda: FakeBroker())
    base.app.dependency_overrides[base.require_user] = lambda: base.UserIdentity(subject="owner:1", email="owner")
    try:
        with TestClient(live_api.app) as client:
            response = client.get("/v1/broker/webull/accounts")
            assert response.status_code == 200
            choices = response.json()["accounts"]
            assert len(choices) == 3
            assert choices[0]["label"] == "CASH account ending 1234"
            assert choices[1]["label"] == "CASH account ending 5678"
            assert not any(x["selected"] for x in choices)
            denied = client.post("/v1/broker/webull/select-account", json={
                "account_id": "margin-c", "confirmation": "SELECT WEBULL CASH ACCOUNT"
            })
            assert denied.status_code == 422
            assert selected_webull_account_id() == ""
            saved = client.post("/v1/broker/webull/select-account", json={
                "account_id": "cash-b", "confirmation": "SELECT WEBULL CASH ACCOUNT"
            })
            assert saved.status_code == 200
            assert saved.json()["selected"] is True
            assert selected_webull_account_id() == "cash-b"
            assert (tmp_path / "selected").read_text().strip() == "cash-b"
            assert [x["selected"] for x in client.get("/v1/broker/webull/accounts").json()["accounts"]] == [False, True, False]
    finally:
        base.app.dependency_overrides.pop(base.require_user, None)


def test_account_selection_rejects_env_override(monkeypatch, tmp_path):
    from engine.webull_live import save_selected_webull_account_id
    monkeypatch.setenv("WEBULL_SELECTED_ACCOUNT_PATH", str(tmp_path / "selected"))
    monkeypatch.setenv("WEBULL_ACCOUNT_ID", "admin-picked")
    try:
        save_selected_webull_account_id("attacker-picked")
    except ValueError as exc:
        assert "Railway" in str(exc)
    else:
        raise AssertionError("Expected environment override to prevent unsafe account change")
    assert not (tmp_path / "selected").exists()
