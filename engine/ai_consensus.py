from __future__ import annotations

import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime
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
        generated_at=datetime.utcnow().isoformat(timespec="seconds") + "Z",
    )


class AIAdvisoryEngine:
    """Cached multi-provider advisory layer that never holds up the 1-second trading loop."""

    def __init__(self, providers: tuple[_Provider, ...] | None = None) -> None:
        self.providers = providers if providers is not None else providers_from_env()
        self.refresh_seconds = _env_float("PAPER_AI_REFRESH_SECONDS", 60.0, minimum=10.0, maximum=900.0)
        self.timeout_seconds = _env_float("PAPER_AI_TIMEOUT_SECONDS", 6.0, minimum=1.0, maximum=30.0)
        self._lock = threading.Lock()
        self._latest: AIConsensus | None = None
        self._last_started = 0.0
        self._inflight = False
        self._last_errors: dict[str, str] = {}

    @property
    def configured(self) -> bool:
        return bool(self.providers)

    def status(self) -> dict[str, Any]:
        with self._lock:
            latest = self._latest
            errors = dict(self._last_errors)
            inflight = self._inflight
        return {
            "configured": self.configured,
            "providers": [provider.name for provider in self.providers],
            "models": {provider.name: provider.model for provider in self.providers},
            "inflight": inflight,
            "errors": errors,
            "latest": None if latest is None else latest.as_dict(),
            "refresh_seconds": self.refresh_seconds,
            "data_policy": "tape_only_no_external_current_information",
        }

    def latest_or_request(self, context: AIContext) -> AIConsensus | None:
        now = time.monotonic()
        should_start = False
        with self._lock:
            latest = self._latest
            if self.providers and not self._inflight and now - self._last_started >= self.refresh_seconds:
                self._inflight = True
                self._last_started = now
                should_start = True
        if should_start:
            threading.Thread(target=self._refresh, args=(context,), name="spy0dte-ai-advisory", daemon=True).start()
        return latest

    def _refresh(self, context: AIContext) -> None:
        signals: list[AIProviderSignal] = []
        errors: dict[str, str] = {}
        try:
            with ThreadPoolExecutor(max_workers=max(1, len(self.providers))) as pool:
                futures = {pool.submit(provider.evaluate, context, self.timeout_seconds): provider for provider in self.providers}
                for future in as_completed(futures):
                    provider = futures[future]
                    try:
                        signals.append(future.result())
                    except Exception as exc:
                        errors[provider.name] = f"{type(exc).__name__}: {exc}"[:240]
                        log.warning("AI provider %s failed: %s", provider.name, exc)
            consensus = combine_ai_signals(tuple(sorted(signals, key=lambda item: item.provider)))
            with self._lock:
                if consensus is not None:
                    self._latest = consensus
                self._last_errors = errors
        finally:
            with self._lock:
                self._inflight = False
