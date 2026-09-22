from datetime import date, datetime, time, timezone

import pytest

from engine.providers.thetadata import ThetaDataConfig, ThetaDataProvider


class FakeClient:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def option_history_greeks_all(self, **kwargs):
        self.calls.append(kwargs)
        return self.rows


def sample_row(**overrides):
    row = {
        "expiration": date(2026, 9, 22),
        "timestamp": datetime(2026, 9, 22, 10, 15),
        "strike": 670.0,
        "right": "call",
        "bid": 1.20,
        "ask": 1.24,
        "delta": 0.51,
        "gamma": 0.032,
        "theta": -0.41,
        "vega": 0.06,
        "implied_vol": 0.182,
        "underlying_price": 669.85,
    }
    row.update(overrides)
    return row


def test_fetch_zero_dte_requests_same_day_spy_chain():
    client = FakeClient([sample_row()])
    provider = ThetaDataProvider(client=client)

    observations = provider.fetch_zero_dte(date(2026, 9, 22))

    assert len(observations) == 1
    call = client.calls[0]
    assert call["symbol"] == "SPY"
    assert call["expiration"] == date(2026, 9, 22)
    assert call["date"] == date(2026, 9, 22)
    assert call["interval"] == "1m"
    assert call["right"] == "both"
    assert call["version"] == "latest"


def test_normalizes_eastern_timestamp_and_occ_symbol():
    provider = ThetaDataProvider(client=FakeClient([]))
    observation = provider.normalize([sample_row()], trade_date=date(2026, 9, 22))[0]

    assert observation.timestamp == datetime(2026, 9, 22, 14, 15, tzinfo=timezone.utc)
    assert observation.option_symbol == "O:SPY260922C00670000"
    assert observation.gamma == pytest.approx(0.032)
    assert observation.spread == pytest.approx(0.04)


def test_normalize_sorts_contracts_deterministically():
    provider = ThetaDataProvider(client=FakeClient([]))
    rows = [
        sample_row(strike=671.0, right="put"),
        sample_row(strike=669.0, right="call"),
        sample_row(strike=669.0, right="put"),
    ]

    observations = provider.normalize(rows, trade_date=date(2026, 9, 22))

    assert [(item.strike, item.right) for item in observations] == [
        (669.0, "call"),
        (669.0, "put"),
        (671.0, "put"),
    ]


def test_rejects_non_zero_dte_payload():
    provider = ThetaDataProvider(client=FakeClient([]))
    with pytest.raises(ValueError, match="non-0DTE"):
        provider.normalize(
            [sample_row(expiration=date(2026, 9, 23))],
            trade_date=date(2026, 9, 22),
        )


def test_rejects_crossed_quote():
    provider = ThetaDataProvider(client=FakeClient([]))
    with pytest.raises(ValueError, match="crossed"):
        provider.normalize(
            [sample_row(bid=1.30, ask=1.20)],
            trade_date=date(2026, 9, 22),
        )


def test_provider_is_spy_only():
    with pytest.raises(ValueError, match="restricted to SPY"):
        ThetaDataProvider(
            client=FakeClient([]),
            config=ThetaDataConfig(symbol="QQQ", start_time=time(9, 30)),
        )
