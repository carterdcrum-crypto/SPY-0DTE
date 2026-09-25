from __future__ import annotations

from fastapi.testclient import TestClient

from engine import api as base
from engine.api_v2 import app, status_payload


def _authorized_user():
    return base.UserIdentity(subject="test-user", email="owner@example.com")


def test_tradier_live_gate_stays_locked_in_login_free_preview(monkeypatch, tmp_path):
    monkeypatch.setenv("START_COLLECTOR_IN_API", "false")
    monkeypatch.setenv("START_PAPER_AUTOTRADER", "false")
    monkeypatch.setenv("CONTROL_DB_PATH", str(tmp_path / "control.sqlite"))
    monkeypatch.setenv("COLLECTOR_DB_PATH", str(tmp_path / "market.sqlite"))
    monkeypatch.setenv("PAPER_DB_PATH", str(tmp_path / "paper.sqlite"))
    monkeypatch.setenv("LIVE_BROKER", "tradier")
    monkeypatch.setenv("TRADIER_ACCESS_TOKEN", "secret-token")
    monkeypatch.setenv("TRADIER_ACCOUNT_ID", "VA000001")
    monkeypatch.setenv("ALLOW_LIVE_ORDERS", "true")
    monkeypatch.setenv("LIVE_ORDER_EXECUTOR_READY", "true")
    monkeypatch.setenv("APP_PREVIEW_MODE", "true")

    payload = status_payload()
    assert payload["live_gate"]["ready"] is False
    assert "owner_auth_required_for_live" in payload["live_gate"]["reasons"]
    assert payload["live_broker"]["configured"] is True
    assert payload["live_broker"]["real_order_submission"] is False
    assert "secret-token" not in repr(payload)


def test_tradier_readiness_endpoint_reports_unconfigured_without_secret(monkeypatch, tmp_path):
    monkeypatch.setenv("START_COLLECTOR_IN_API", "false")
    monkeypatch.setenv("START_PAPER_AUTOTRADER", "false")
    monkeypatch.setenv("CONTROL_DB_PATH", str(tmp_path / "control.sqlite"))
    monkeypatch.setenv("COLLECTOR_DB_PATH", str(tmp_path / "market.sqlite"))
    monkeypatch.setenv("PAPER_DB_PATH", str(tmp_path / "paper.sqlite"))
    monkeypatch.delenv("TRADIER_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("TRADIER_ACCOUNT_ID", raising=False)
    app.dependency_overrides[base.require_user] = _authorized_user
    try:
        with TestClient(app) as client:
            response = client.get("/v1/broker/tradier/readiness")
        assert response.status_code == 200
        body = response.json()
        assert body["configured"] is False
        assert body["connected"] is False
    finally:
        app.dependency_overrides.clear()
