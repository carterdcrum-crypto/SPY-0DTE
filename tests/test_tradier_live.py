from __future__ import annotations

from engine.broker import OptionOrderRequest
from engine.tradier_live import TradierLiveClient, tradier_env_status


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)


def _order() -> OptionOrderRequest:
    return OptionOrderRequest(
        account_id="VA000001",
        client_order_id="spy0dte-001",
        underlying="SPY",
        strike_price=770.0,
        expiration_date="2026-09-25",
        option_type="CALL",
        side="BUY",
        position_intent="BUY_TO_OPEN",
        quantity=1,
        limit_price=0.42,
    )


def test_occ_symbol_matches_spy_0dte_format():
    assert TradierLiveClient.occ_symbol(_order()) == "SPY260925C00770000"


def test_profile_resolves_active_cash_account_without_leaking_token():
    session = FakeSession(
        [
            FakeResponse(
                {
                    "profile": {
                        "account": [
                            {
                                "account_number": "VA000001",
                                "type": "cash",
                                "option_level": 2,
                                "status": "active",
                            }
                        ]
                    }
                }
            )
        ]
    )
    client = TradierLiveClient(access_token="super-secret", session=session)
    profile = client.account_profile()
    assert profile.account_number == "VA000001"
    assert profile.is_cash is True
    assert profile.is_active is True
    assert profile.option_level == 2
    serialized = repr(profile.as_dict()) + repr(session.calls[0][2])
    assert "super-secret" in serialized  # token is sent only in the Authorization header
    assert "super-secret" not in repr(profile.as_dict())


def test_preview_option_order_can_never_submit_real_order():
    session = FakeSession([FakeResponse({"order": {"status": "ok", "result": True}})])
    client = TradierLiveClient(
        access_token="token",
        account_id="VA000001",
        session=session,
    )
    result = client.preview_option_order(_order())
    assert result["order"]["status"] == "ok"
    method, url, kwargs = session.calls[0]
    assert method == "POST"
    assert url.endswith("/accounts/VA000001/orders")
    assert kwargs["data"]["preview"] == "true"
    assert kwargs["data"]["class"] == "option"
    assert kwargs["data"]["side"] == "buy_to_open"
    assert kwargs["data"]["option_symbol"] == "SPY260925C00770000"
    assert not hasattr(client, "submit_order")


def test_env_status_exposes_presence_only(monkeypatch):
    monkeypatch.setenv("TRADIER_ACCESS_TOKEN", "secret-value")
    monkeypatch.setenv("TRADIER_ACCOUNT_ID", "VA000001")
    status = tradier_env_status()
    assert status["configured"] is True
    assert status["execution"] == "preview_only"
    assert "secret-value" not in repr(status)
