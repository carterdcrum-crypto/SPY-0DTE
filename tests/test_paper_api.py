from fastapi.testclient import TestClient

from engine import api as base_api
from engine.api_v2 import app


def _authorized_user():
    return base_api.UserIdentity(subject="paper-test", email="owner@example.com")


def _configure(monkeypatch, tmp_path):
    monkeypatch.setenv("START_COLLECTOR_IN_API", "false")
    monkeypatch.setenv("CONTROL_DB_PATH", str(tmp_path / "control.sqlite"))
    monkeypatch.setenv("COLLECTOR_DB_PATH", str(tmp_path / "market.sqlite"))
    monkeypatch.setenv("PAPER_DB_PATH", str(tmp_path / "paper.sqlite"))
    monkeypatch.setenv("PAPER_STARTING_CASH", "250")
    base_api.app.dependency_overrides[base_api.require_user] = _authorized_user


def test_status_contains_persistent_paper_account(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    try:
        with TestClient(app) as client:
            response = client.get("/v1/status")
        assert response.status_code == 200
        paper = response.json()["paper"]
        assert paper["starting_cash"] == 250.0
        assert paper["settled_cash"] == 250.0
        assert paper["unsettled_cash"] == 0.0
        assert paper["realized_pnl"] == 0.0
        assert paper["open_positions"] == 0
        assert paper["trade_count"] == 0
    finally:
        base_api.app.dependency_overrides.clear()


def test_paper_reset_requires_explicit_confirmation(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    try:
        with TestClient(app) as client:
            rejected = client.post(
                "/v1/paper/reset",
                json={"starting_cash": 500, "confirmation": "reset"},
            )
            accepted = client.post(
                "/v1/paper/reset",
                json={"starting_cash": 500, "confirmation": "RESET PAPER ACCOUNT"},
            )
            account = client.get("/v1/paper/account")
        assert rejected.status_code == 400
        assert accepted.status_code == 200
        assert accepted.json()["starting_cash"] == 500.0
        assert accepted.json()["settled_cash"] == 500.0
        assert account.json()["starting_cash"] == 500.0
    finally:
        base_api.app.dependency_overrides.clear()
