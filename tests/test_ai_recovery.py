import threading
import time
from dataclasses import replace

import pytest

from engine.ai_consensus import AIAdvisoryEngine, AIContext, AIProviderSignal, _parse_signal


def context():
    return AIContext(
        market_time="2026-09-25T15:00:00+00:00", data_mode="DELAYED_SIMULATION", spot=670,
        horizon_minutes=5, minutes_to_close=300, quant_probability_up=0.6,
        quant_expected_log_return=0.001, quant_volatility=0.002, quant_agreement=0.8,
        quant_calibration=0.7, quant_regime_match=0.8, recent_spot_returns=(0.001,),
        option_call_median_move=0.01, option_put_median_move=-0.01,
    )


class Provider:
    name, model = "fake", "test"
    def __init__(self):
        self.calls = 0
        self.fail = False

    def evaluate(self, context, timeout_seconds):
        self.calls += 1
        if self.fail:
            raise RuntimeError("secret=do-not-publish")
        return AIProviderSignal(self.name, self.model, 0.7, 0.8, 0.8, "trend", "low", "test", 1)


def primed():
    provider = Provider()
    engine = AIAdvisoryEngine((provider,))
    engine._refresh(context())
    engine._last_started = time.monotonic()
    assert engine.latest_or_request(context()) is not None
    return engine, provider


def test_cached_advice_expires_and_returns_to_quant():
    engine, _ = primed()
    engine._latest_started -= engine.max_age_seconds + 1
    assert engine.latest_or_request(context()) is None
    assert engine.status()["fallback"] == "quant_only"
    assert engine.status()["latest"] is None


@pytest.mark.parametrize("change", [
    {"market_time": "2026-09-28T15:00:00+00:00"},
    {"market_time": "2026-09-25T14:59:59+00:00"},
    {"data_mode": "REALTIME_COMPATIBLE"},
    {"spot": 680},
    {"horizon_minutes": 1},
])
def test_context_change_invalidates_advice(change):
    engine, _ = primed()
    assert engine.latest_or_request(replace(context(), **change)) is None


def test_provider_failure_backs_off_and_recovers_without_secret_leak():
    engine, provider = primed()
    provider.fail = True
    engine._refresh(context())
    assert engine.status()["errors"] == {"fake": "RuntimeError"}
    assert "do-not-publish" not in str(engine.status())
    calls = provider.calls
    engine._refresh(context())
    assert provider.calls == calls
    provider.fail = False
    engine._provider_retry_at["fake"] = 0
    engine._refresh(context())
    assert engine.status()["errors"] == {}
    assert engine.status()["provider_health"]["fake"]["consecutive_failures"] == 0


def test_hung_provider_cannot_block_refresh_or_spawn_more_copies():
    release, entered = threading.Event(), threading.Event()
    provider = Provider()
    original = provider.evaluate

    def hung(context, timeout):
        entered.set()
        release.wait(2)
        return original(context, timeout)

    provider.evaluate = hung
    engine = AIAdvisoryEngine((provider,))
    engine.timeout_seconds = 0.02
    try:
        engine._refresh(context())
        assert entered.is_set()
        assert engine.status()["errors"] == {"fake": "TimeoutError"}
        engine._provider_retry_at["fake"] = 0
        engine._refresh(context())
        assert engine._provider_inflight == {"fake"}
        assert engine._latest is None
    finally:
        release.set()


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "1.1", "-0.1"])
def test_invalid_model_numbers_are_rejected(bad):
    with pytest.raises(ValueError):
        _parse_signal("fake", "test", '{"probability_up":' + bad + ',"confidence":0.7,"risk_multiplier":0.8}', 0)
