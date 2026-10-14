"""Only one owner may enroll; every broker/websocket call requires a live session."""
from fastapi.testclient import TestClient
from engine.api import app


def test_only_one_proof_of_ownership_signup_and_sessions(monkeypatch, tmp_path):
    monkeypatch.setenv("START_COLLECTOR_IN_API", "false")
    monkeypatch.setenv("OWNER_AUTH_DB_PATH", str(tmp_path / "owner.sqlite"))
    monkeypatch.setenv("OWNER_SETUP_CODE", "example-setup-code-not-real-1234567890")
    with TestClient(app) as client:
        s = client.get("/v1/auth/setup-status")
        assert s.json() == {"signup_available": True, "owner_created": False}
        assert client.get("/v1/auth/me").status_code == 401
        assert client.get("/v1/status").status_code == 401
        wrong = client.post("/v1/auth/signup", json={
            "username": "owner", "password": "a-long-private-passphrase",
            "setup_code": "not-correct-enrollment-code-abcdefghijk"
        })
        assert wrong.status_code == 403
        created = client.post("/v1/auth/signup", json={
            "username": "owner", "password": "a-long-private-passphrase",
            "setup_code": "example-setup-code-not-real-1234567890",
        })
        assert created.status_code == 200
        token = created.json()["access_token"]
        assert len(token) >= 48
        assert "password" not in created.text
        assert client.get("/v1/auth/setup-status").json()["owner_created"] is True
        assert client.get("/v1/auth/setup-status").json()["signup_available"] is False
        second = client.post("/v1/auth/signup", json={
            "username": "imposter", "password": "another-long-passphrase",
            "setup_code": "example-setup-code-not-real-1234567890",
        })
        assert second.status_code == 409
        assert client.get("/v1/auth/me", headers={"Authorization": "Bearer " + token}).json() == {
            "authenticated": True, "owner": True, "username": "owner",
        }
        assert client.post("/v1/auth/login", json={
            "username": "owner", "password": "bad-password",
        }).status_code == 401
        signed_in = client.post("/v1/auth/login", json={
            "username": "owner", "password": "a-long-private-passphrase",
        })
        assert signed_in.status_code == 200
        new_token = signed_in.json()["access_token"]
        assert new_token != token
        headers = {"Authorization": "Bearer " + new_token}
        assert client.get("/v1/auth/me", headers=headers).status_code == 200
        assert client.post("/v1/auth/logout", headers=headers).json() == {"logged_out": True}
        assert client.get("/v1/auth/me", headers=headers).status_code == 401
    # Registration stays permanently closed across app/process restarts.
    with TestClient(app) as client:
        assert client.get("/v1/auth/setup-status").json()["owner_created"] is True


def test_signup_denied_without_server_only_enrollment_secret(monkeypatch, tmp_path):
    monkeypatch.setenv("START_COLLECTOR_IN_API", "false")
    monkeypatch.setenv("OWNER_AUTH_DB_PATH", str(tmp_path / "owner.sqlite"))
    monkeypatch.delenv("OWNER_SETUP_CODE", raising=False)
    with TestClient(app) as client:
        assert client.get("/v1/auth/setup-status").json()["signup_available"] is False
        assert client.post("/v1/auth/signup", json={
            "username": "owner", "password": "a-long-private-passphrase",
            "setup_code": "wrong-setup-code-with-at-least-32-characters",
        }).status_code == 503


def test_signup_length_error_does_not_echo_password_or_setup_code(monkeypatch, tmp_path):
    monkeypatch.setenv("START_COLLECTOR_IN_API", "false")
    monkeypatch.setenv("OWNER_AUTH_DB_PATH", str(tmp_path / "owner.sqlite"))
    with TestClient(app) as client:
        response = client.post("/v1/auth/signup", json={
            "username": "owner",
            "password": "too-short",
            "setup_code": "short-code",
        })
        assert response.status_code == 422
        fields = {item["field"]: item["message"] for item in response.json()["detail"]}
        assert "14" in fields["password"]
        assert "32" in fields["setup_code"]
        assert "too-short" not in response.text
        assert "short-code" not in response.text
        assert response.headers["cache-control"] == "no-store"
