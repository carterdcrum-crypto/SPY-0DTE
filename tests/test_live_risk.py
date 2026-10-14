from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from engine.live_risk import (
    ARM_CONFIRMATION,
    LiveRiskEnvelopeError,
    LiveRiskEnvelopeStore,
)


EASTERN = ZoneInfo("America/New_York")


def test_live_risk_envelope_persists_across_trading_days(tmp_path):
    store = LiveRiskEnvelopeStore(tmp_path / "risk.sqlite")
    now = datetime(2026, 9, 25, 10, 0, tzinfo=EASTERN)

    envelope = store.arm(
        daily_loss_limit=25.0,
        daily_gain_limit=40.0,
        max_account_exposure_pct=0.20,
        max_contracts=3,
        explicit_confirmation=ARM_CONFIRMATION,
        now=now,
    )

    assert envelope.armed_today(now)
    assert envelope.daily_loss_limit == 25.0
    assert envelope.daily_gain_limit == 40.0
    assert envelope.max_account_exposure_pct == 0.20
    assert envelope.max_contracts == 3
    assert envelope.as_dict(now)["max_open_positions"] == 1

    next_day = datetime(2026, 9, 26, 10, 0, tzinfo=EASTERN)
    assert envelope.armed_today(next_day) is True


def test_standing_envelope_requires_owner_confirmation(tmp_path):
    store = LiveRiskEnvelopeStore(tmp_path / "risk.sqlite")

    with pytest.raises(LiveRiskEnvelopeError, match="confirmation"):
        store.arm(
            daily_loss_limit=25.0,
            daily_gain_limit=40.0,
            max_account_exposure_pct=0.20,
            max_contracts=3,
            explicit_confirmation="yes",
        )


def test_explicit_disarm_clears_standing_limits(tmp_path):
    store = LiveRiskEnvelopeStore(tmp_path / "risk.sqlite")
    now = datetime(2026, 9, 25, 10, 0, tzinfo=EASTERN)
    store.arm(
        daily_loss_limit=25.0,
        daily_gain_limit=40.0,
        max_account_exposure_pct=0.20,
        max_contracts=3,
        explicit_confirmation=ARM_CONFIRMATION,
        now=now,
    )

    disarmed = store.disarm()
    assert disarmed.trading_date is None
    assert disarmed.daily_loss_limit is None
    assert disarmed.daily_gain_limit is None
    assert disarmed.max_contracts is None
    assert not disarmed.armed_today()
