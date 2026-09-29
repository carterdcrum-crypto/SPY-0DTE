from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

import requests

log = logging.getLogger("spy0dte.ai")


def _clip(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, float(value)))


def _env_float(name: str, default: float, *, minimum: float, maximum: float) -> float:
    value = float(os.environ.get(name, str(default)))
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


@dataclass(frozen=True)
class AIContext:
    market_time: str
    data_mode: str
    spot: float
    horizon_minutes: float
    minutes_to_close: float
    quant_probability_up: float
    quant_expected_log_return: float
    quant_volatility: float
    quant_agreement: float
    quant_calibration: float
    quant_regime_match: float
    recent_spot_returns: tuple[float, ...]
    option_call_median_move: float
    option_put_median_move: float
    event_state: str = "unknown"

    def as_prompt_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["recent_spot_returns"] = list(self.recent_spot_returns[-30:])
        return payload


@dataclass(frozen=True)
class AIProviderSignal:
    provider: str
    model: str
    probability_up: float
    confidence: float
    risk_multiplier: float
    regime: str
    event_risk: str
    rationale: str
    latency_ms: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AIConsensus:
    signals: tuple[AIProviderSignal, ...]
    probability_up: float
    confidence: float
    risk_multiplier: float
    disagreement: float
    generated_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "active": bool(self.signals),
            "probability_up": self.probability_up,
            "confidence": self.confidence,
            "risk_multiplier": self.risk_multiplier,
            "disagreement": self.disagreement,
            "generated_at": self.generated_at,
            "providers": [signal.as_dict() for signal in self.signals],
            "policy": "advisory_only; may veto/reduce risk; never submits orders or raises hard limits",
        }


_SCHEMA = {
    "type": "object",
    "properties": {
        "probability_up": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "risk_multiplier": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "regime": {"type": "string"},
        "event_risk": {"type": "string"},
        "rationale": {"type": "string"},
    },
    "required": [
        "probability_up",
        "confidence",
        "risk_multiplier",
        "regime",
        "event_risk",
        "rationale",
    ],
    "additionalProperties": False,
}

_SYSTEM = """You are an advisory market-state classifier for a PAPER-ONLY SPY 0DTE research system.
Use ONLY the numerical/timestamped market snapshot supplied in the prompt. Do not use outside market
knowledge, imagined headlines, or current web information. This is especially important when
data_mode is DELAYED_SIMULATION: using outside information would leak the future into the paper test.

Return a cautious structured assessment. probability_up is your probability SPY rises over the
stated horizon. confidence measures how much the supplied snapshot supports that assessment.
risk_multiplier must be between 0 and 1 and can only reduce risk; use lower values for ambiguous,
unstable, event-like, or out-of-distribution conditions. Never recommend a contract, quantity,
broker action, leverage, or override of risk limits."""


def _extract_json_text(value: Any) -> str | None:
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("{") and text.endswith("}"):
            return text
        return None
    if isinstance(value, list):
        for item in value:
            found = _extract_json_text(item)
            if found:
                return found
        return None
    if isinstance(value, dict):
        for key in ("output_text", "text", "content"):
            if key in value:
                found = _extract_json_text(value[key])
                if found:
                    return found
        for item in value.values():
            found = _extract_json_text(item)
            if found:
                return found
    return None


def _parse_signal(provider: str, model: str, payload: Any, latency_ms: int) -> AIProviderSignal:
    text = _extract_json_text(payload)
    if not text:
        raise ValueError(f"{provider} response did not contain a JSON object")
    raw = json.loads(text)
    for field in ("probability_up", "confidence", "risk_multiplier"):
        value = float(raw[field])
        if not math.isfinite(value) or not 0.0 <= value <= 1.0:
            raise ValueError(f"invalid AI field: {field}")
    return AIProviderSignal(
        provider=provider,
        model=model,
        probability_up=_clip(float(raw["probability_up"]), 0.0, 1.0),
        confidence=_clip(float(raw["confidence"]), 0.0, 1.0),
        risk_multiplier=_clip(float(raw["risk_multiplier"]), 0.0, 1.0),
        regime=str(raw.get("regime") or "unknown")[:80],
        event_risk=str(raw.get("event_risk") or "unknown")[:80],
        rationale=str(raw.get("rationale") or "")[:400],
        latency_ms=max(0, int(latency_ms)),
    )


class _Provider:
    name: str
    model: str

    def evaluate(self, context: AIContext, timeout_seconds: float) -> AIProviderSignal:
        raise NotImplementedError


class OpenAIProvider(_Provider):
    name = "openai"

    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model

    def evaluate(self, context: AIContext, timeout_seconds: float) -> AIProviderSignal:
        started = time.monotonic()
        response = requests.post(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json={
                "model": self.model,
                "input": [
                    {"role": "system", "content": [{"type": "input_text", "text": _SYSTEM}]},
                    {"role": "user", "content": [{"type": "input_text", "text": json.dumps(context.as_prompt_payload(), separators=(",", ":"))}]},
                ],
                "text": {"format": {"type": "json_schema", "name": "spy_0dte_market_advice", "strict": True, "schema": _SCHEMA}},
                "max_output_tokens": 300,
            },
            timeout=timeout_seconds,
        )
        response.raise_for_status()
        return _parse_signal(self.name, self.model, response.json(), int((time.monotonic() - started) * 1000))


class AnthropicProvider(_Provider):
    name = "anthropic"

    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model

    def evaluate(self, context: AIContext, timeout_seconds: float) -> AIProviderSignal:
        started = time.monotonic()
        prompt = f"{_SYSTEM}\nReturn ONLY JSON matching this schema: {json.dumps(_SCHEMA, separators=(',', ':'))}\nSNAPSHOT={json.dumps(context.as_prompt_payload(), separators=(',', ':'))}"
        response = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": self.model, "max_tokens": 300, "temperature": 0, "messages": [{"role": "user", "content": prompt}]},
            timeout=timeout_seconds,
        )
        response.raise_for_status()
        return _parse_signal(self.name, self.model, response.json(), int((time.monotonic() - started) * 1000))


class GeminiProvider(_Provider):
    name = "gemini"

    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model

    def evaluate(self, context: AIContext, timeout_seconds: float) -> AIProviderSignal:
        started = time.monotonic()
        prompt = f"{_SYSTEM}\nSNAPSHOT={json.dumps(context.as_prompt_payload(), separators=(',', ':'))}"
        response = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent",
            headers={"x-goog-api-key": self.api_key, "Content-Type": "application/json"},
            json={
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0, "maxOutputTokens": 300, "responseMimeType": "application/json", "responseSchema": _SCHEMA},
            },
            timeout=timeout_seconds,
        )
        response.raise_for_status()
        return _parse_signal(self.name, self.model, response.json(), int((time.monotonic() - started) * 1000))


def providers_from_env() -> tuple[_Provider, ...]:
    providers: list[_Provider] = []
    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if openai_key:
        providers.append(OpenAIProvider(openai_key, os.environ.get("OPENAI_TRADING_MODEL", "gpt-5.6-luna").strip() or "gpt-5.6-luna"))
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if anthropic_key:
        providers.append(AnthropicProvider(anthropic_key, os.environ.get("ANTHROPIC_TRADING_MODEL", "claude-haiku-4-5-20251001").strip() or "claude-haiku-4-5-20251001"))
    gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if gemini_key:
        providers.append(GeminiProvider(gemini_key, os.environ.get("GEMINI_TRADING_MODEL", "gemini-3.8-flash").strip() or "gemini-3.8-flash"))
    return tuple(providers)


def combine_ai_signals(signals: tuple[AIProviderSignal, ...]) -> AIConsensus | None:
    if not signals:
        return None
    raw_weights = [max(0.05, signal.confidence) for signal in signals]
    total = sum(raw_weights)
    weights = [value / total for value in raw_weights]
    probability_up = sum(w * s.probability_up for w, s in zip(weights, signals))
    confidence = sum(w * s.confidence for w, s in zip(weights, signals))
    risk_multiplier = sum(w * s.risk_multiplier for w, s in zip(weights, signals))
    disagreement = sum(w * abs(s.probability_up - probability_up) for w, s in zip(weights, signals))
    risk_multiplier *= _clip(1.0 - 1.5 * disagreement, 0.35, 1.0)
    return AIConsensus(
        signals=signals,
        probability_up=_clip(probability_up, 0.0, 1.0),
        confidence=_clip(confidence, 0.0, 1.0),
        risk_multiplier=_clip(risk_multiplier, 0.0, 1.0),
        disagreement=_clip(disagreement, 0.0, 0.5),
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )


class AIAdvisoryEngine:
    """Cached multi-provider advisory layer that never holds up the 1-second trading loop."""

    def __init__(self, providers: tuple[_Provider, ...] | None = None) -> None:
        self.providers = providers if providers is not None else providers_from_env()
        self.refresh_seconds = _env_float("PAPER_AI_REFRESH_SECONDS", 60.0, minimum=10.0, maximum=900.0)
        self.timeout_seconds = _env_float("PAPER_AI_TIMEOUT_SECONDS", 6.0, minimum=1.0, maximum=30.0)
        self.max_age_seconds = _env_float("PAPER_AI_MAX_AGE_SECONDS", 120.0, minimum=10.0, maximum=900.0)
        self.max_spot_move = _env_float("PAPER_AI_MAX_SPOT_MOVE", 0.003, minimum=0.0001, maximum=0.05)
        self._lock = threading.Lock()
        self._latest: AIConsensus | None = None
        self._latest_context: AIContext | None = None
        self._current_context: AIContext | None = None
        self._latest_started = float("-inf")
        self._last_started = float("-inf")
        self._inflight = False
        self._last_errors: dict[str, str] = {}
        self._provider_inflight: set[str] = set()
        self._provider_failures: dict[str, int] = {}
        self._provider_retry_at: dict[str, float] = {}

    @property
    def configured(self) -> bool:
        return bool(self.providers)

    def status(self) -> dict[str, Any]:
        with self._lock:
            now = time.monotonic()
            latest = self._usable_latest(self._current_context, now)
            errors = dict(self._last_errors)
            inflight = self._inflight
            age = None if self._latest is None else max(0.0, now - self._latest_started)
            health = {
                provider.name: {
                    "inflight": provider.name in self._provider_inflight,
                    "consecutive_failures": self._provider_failures.get(provider.name, 0),
                    "retry_in_seconds": max(0.0, self._provider_retry_at.get(provider.name, 0.0) - now),
                }
                for provider in self.providers
            }
        return {
            "configured": self.configured,
            "providers": [provider.name for provider in self.providers],
            "models": {provider.name: provider.model for provider in self.providers},
            "inflight": inflight,
            "errors": errors,
            "latest": None if latest is None else latest.as_dict(),
            "refresh_seconds": self.refresh_seconds,
            "max_age_seconds": self.max_age_seconds,
            "age_seconds": age,
            "fallback": "quant_only" if latest is None else "quant_ai_hybrid",
            "provider_health": health,
            "data_policy": "tape_only_no_external_current_information",
        }

    def _usable_latest(self, context: AIContext | None, now: float) -> AIConsensus | None:
        if self._latest is None or now - self._latest_started > self.max_age_seconds:
            return None
        previous = self._latest_context
        if context is None or previous is None or context.data_mode != previous.data_mode:
            return None
        try:
            current_time = datetime.fromisoformat(context.market_time.replace("Z", "+00:00"))
            previous_time = datetime.fromisoformat(previous.market_time.replace("Z", "+00:00"))
            market_age = (current_time - previous_time).total_seconds()
            if current_time.date() != previous_time.date() or not 0.0 <= market_age <= self.max_age_seconds:
                return None
        except (TypeError, ValueError):
            return None
        if previous.spot <= 0 or abs(context.spot / previous.spot - 1.0) > self.max_spot_move:
            return None
        if abs(context.horizon_minutes - previous.horizon_minutes) > max(0.5, previous.horizon_minutes * 0.5):
            return None
        return self._latest

    def latest_or_request(self, context: AIContext) -> AIConsensus | None:
        now = time.monotonic()
        should_start = False
        with self._lock:
            self._current_context = context
            latest = self._usable_latest(context, now)
            if self.providers and not self._inflight and now - self._last_started >= self.refresh_seconds:
                self._inflight = True
                self._last_started = now
                should_start = True
        if should_start:
            threading.Thread(target=self._refresh, args=(context, now), name="spy0dte-ai-advisory", daemon=True).start()
        return latest

    def _refresh(self, context: AIContext, requested_at: float | None = None) -> None:
        requested_at = time.monotonic() if requested_at is None else requested_at
        signals: list[AIProviderSignal] = []
        errors: dict[str, str] = {}
        try:
            with self._lock:
                available = [p for p in self.providers if p.name not in self._provider_inflight
                             and time.monotonic() >= self._provider_retry_at.get(p.name, 0.0)]
                self._provider_inflight.update(p.name for p in available)
            if not available:
                return

            # Bounded daemon workers: a provider ignoring its socket timeout
            # cannot pin the advisory coordinator or create unlimited threads.
            deadline = time.monotonic() + self.timeout_seconds
            condition = threading.Condition()
            results: dict[str, tuple[AIProviderSignal | None, str | None]] = {}

            def evaluate(provider: _Provider) -> None:
                signal, error = None, None
                try:
                    signal = provider.evaluate(context, self.timeout_seconds)
                except Exception as exc:
                    error = type(exc).__name__
                finally:
                    with condition:
                        if time.monotonic() <= deadline:
                            results[provider.name] = (signal, error)
                        condition.notify_all()
                    with self._lock:
                        self._provider_inflight.discard(provider.name)

            for provider in available:
                threading.Thread(target=evaluate, args=(provider,), name=f"spy0dte-ai-{provider.name}", daemon=True).start()
            with condition:
                condition.wait_for(lambda: len(results) == len(available), timeout=max(0.0, deadline - time.monotonic()))
                completed = dict(results)
            for provider in available:
                signal, error = completed.get(provider.name, (None, "TimeoutError"))
                with self._lock:
                    if signal is not None:
                        signals.append(signal)
                        self._provider_failures[provider.name] = 0
                        self._provider_retry_at[provider.name] = 0.0
                        self._last_errors.pop(provider.name, None)
                    else:
                        errors[provider.name] = error or "InvalidResponse"
                        failures = self._provider_failures.get(provider.name, 0) + 1
                        self._provider_failures[provider.name] = failures
                        delay = min(900.0, self.refresh_seconds * 2 ** min(failures - 1, 6))
                        self._provider_retry_at[provider.name] = time.monotonic() + delay
                        log.warning("AI provider=%s failed type=%s retry_in=%.1fs", provider.name, errors[provider.name], delay)
            consensus = combine_ai_signals(tuple(sorted(signals, key=lambda item: item.provider)))
            with self._lock:
                if consensus is not None:
                    self._latest = consensus
                    self._latest_context = context
                    self._latest_started = requested_at
                self._last_errors.update(errors)
        finally:
            with self._lock:
                self._inflight = False
