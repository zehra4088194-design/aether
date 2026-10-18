"""
Aether - LLMProvider Module
==============================
Module 3 of 5

Purpose
-------
A single interface (`LLMProvider`) that both a cloud backend (Anthropic /
OpenAI) and a local backend (Ollama) implement identically, including
tool-use/function-calling, so the agent loop (Module 4) never needs to
know or care which backend is active. Includes automatic fallback:
cloud -> local if offline, local -> cloud if no local model installed.
"""

from __future__ import annotations

import abc
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import requests

from aether.hardware_probe import TierConfig


# --------------------------------------------------------------------------- #
# Shared message / tool schema (backend-agnostic)
# --------------------------------------------------------------------------- #

@dataclass
class ToolDefinition:
    name: str
    description: str
    input_schema: dict  # JSON schema, same shape Anthropic/OpenAI both accept


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = "end_turn"
    raw: Any = None
    input_tokens: int = 0
    output_tokens: int = 0


class LLMProviderError(RuntimeError):
    """Raised when a provider fails in a way that should trigger fallback."""


# --------------------------------------------------------------------------- #
# Abstract interface
# --------------------------------------------------------------------------- #

class LLMProvider(abc.ABC):
    @abc.abstractmethod
    def is_available(self) -> bool:
        """Cheap health check (no API key needed to answer, or a local
        ping to Ollama). Used by the fallback manager below."""

    @abc.abstractmethod
    def chat(
        self,
        messages: list[dict],
        tools: Optional[list[ToolDefinition]] = None,
        system: Optional[str] = None,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        ...


# --------------------------------------------------------------------------- #
# Cloud provider (Anthropic) — OpenAI can be added as a sibling class using
# the same interface; kept to one implementation here to stay focused.
# --------------------------------------------------------------------------- #

class CloudProvider(LLMProvider):
    """Calls the Anthropic Messages API. The user supplies their own key
    via aether.config.yaml or an environment variable — never bundle a key
    with the app."""

    API_URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6"):
        self.api_key = api_key
        self.model = model

    def is_available(self) -> bool:
        if not self.api_key:
            return False
        try:
            requests.get("https://api.anthropic.com", timeout=3)
            return True
        except requests.RequestException:
            return False

    def _to_anthropic_tools(self, tools: Optional[list[ToolDefinition]]) -> Optional[list[dict]]:
        if not tools:
            return None
        return [
            {"name": t.name, "description": t.description, "input_schema": t.input_schema}
            for t in tools
        ]

    def chat(
        self,
        messages: list[dict],
        tools: Optional[list[ToolDefinition]] = None,
        system: Optional[str] = None,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        body: dict = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system:
            body["system"] = system
        anthropic_tools = self._to_anthropic_tools(tools)
        if anthropic_tools:
            body["tools"] = anthropic_tools

        try:
            resp = requests.post(self.API_URL, headers=headers, json=body, timeout=60)
            resp.raise_for_status()
        except requests.RequestException as e:
            raise LLMProviderError(f"Cloud provider request failed: {e}") from e

        data = resp.json()
        text_parts = []
        tool_calls: list[ToolCall] = []
        for block in data.get("content", []):
            if block.get("type") == "text":
                text_parts.append(block["text"])
            elif block.get("type") == "tool_use":
                tool_calls.append(
                    ToolCall(id=block["id"], name=block["name"], arguments=block.get("input", {}))
                )

        return LLMResponse(
            text="\n".join(text_parts),
            tool_calls=tool_calls,
            stop_reason=data.get("stop_reason", "end_turn"),
            raw=data,
            input_tokens=int(data.get("usage", {}).get("input_tokens", 0)),
            output_tokens=int(data.get("usage", {}).get("output_tokens", 0)),
        )


# --------------------------------------------------------------------------- #
# Local provider (Ollama)
# --------------------------------------------------------------------------- #

class LocalProvider(LLMProvider):
    """Talks to a locally running Ollama instance. Model choice is tier-aware:
    pass the detected TierConfig so a Lite-tier machine never accidentally
    tries to load a 13B model."""

    OLLAMA_URL = "http://localhost:11434/api/chat"

    # Simple size-to-model map; override via config if the user has
    # different models pulled.
    TIER_MODEL_MAP = {
        0: "phi3:mini",          # local disabled tier fallback / tiny option
        7: "llama3:8b-instruct-q4_K_M",
        13: "llama3:13b-instruct-q4_K_M",
    }

    def __init__(self, tier_config: TierConfig, model_override: Optional[str] = None):
        self.tier_config = tier_config
        self.model = model_override or self._pick_model(tier_config)

    def _pick_model(self, tier_config: TierConfig) -> str:
        cap = tier_config.local_llm_max_params_b
        # pick the largest model at or below the tier's cap
        candidates = sorted(self.TIER_MODEL_MAP.items())
        chosen = candidates[0][1]
        for size, name in candidates:
            if size <= cap:
                chosen = name
        return chosen

    def is_available(self) -> bool:
        if not self.tier_config.allow_local_llm:
            return False
        base_url = self.OLLAMA_URL.rsplit("/api/chat", 1)[0]
        try:
            r = requests.get(f"{base_url}/api/tags", timeout=2)
            return r.status_code == 200
        except requests.RequestException:
            return False

    def _to_ollama_tools(self, tools: Optional[list[ToolDefinition]]) -> Optional[list[dict]]:
        if not tools:
            return None
        # Ollama's OpenAI-compatible tool schema
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema,
                },
            }
            for t in tools
        ]

    def chat(
        self,
        messages: list[dict],
        tools: Optional[list[ToolDefinition]] = None,
        system: Optional[str] = None,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        payload_messages = list(messages)
        if system:
            payload_messages = [{"role": "system", "content": system}, *payload_messages]

        body: dict = {
            "model": self.model,
            "messages": payload_messages,
            "stream": False,
        }
        ollama_tools = self._to_ollama_tools(tools)
        if ollama_tools:
            body["tools"] = ollama_tools

        try:
            resp = requests.post(self.OLLAMA_URL, json=body, timeout=120)
            resp.raise_for_status()
        except requests.RequestException as e:
            raise LLMProviderError(f"Local provider request failed: {e}") from e

        data = resp.json()
        message = data.get("message", {})
        tool_calls: list[ToolCall] = []
        for i, call in enumerate(message.get("tool_calls", []) or []):
            fn = call.get("function", {})
            args = fn.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            tool_calls.append(ToolCall(id=str(i), name=fn.get("name", ""), arguments=args))

        return LLMResponse(
            text=message.get("content", ""),
            tool_calls=tool_calls,
            stop_reason="tool_use" if tool_calls else "end_turn",
            raw=data,
            input_tokens=int(data.get("prompt_eval_count", 0)),
            output_tokens=int(data.get("eval_count", 0)),
        )


# --------------------------------------------------------------------------- #
# Fallback manager — what the rest of Aether actually talks to
# --------------------------------------------------------------------------- #

@dataclass
class ProviderCircuitState:
    consecutive_failures: int = 0
    cooling_until: float = 0.0
    last_error: str = ""

    @property
    def cooling_down(self) -> bool:
        return self.cooling_until > time.monotonic()


@dataclass
class SessionUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0.0


class ManagedLLM:
    """Wraps a preferred + fallback provider. Tries preferred first; on
    LLMProviderError or unavailability, transparently switches. Exposes
    which backend actually served the last request for UI transparency."""

    def __init__(
        self,
        preferred: LLMProvider,
        fallback: Optional[LLMProvider] = None,
        *,
        failure_threshold: int = 3,
        cooldown_sec: float = 60.0,
        on_provider_state: Optional[Callable[[dict], None]] = None,
    ):
        self.preferred = preferred
        self.fallback = fallback
        self.last_used: str = ""
        self.failure_threshold = max(1, failure_threshold)
        self.cooldown_sec = max(1.0, cooldown_sec)
        self.on_provider_state = on_provider_state or (lambda _state: None)
        self._states: dict[int, ProviderCircuitState] = {}
        self._usage = SessionUsage()
        self._lock = threading.RLock()

    def _state_for(self, provider: LLMProvider) -> ProviderCircuitState:
        with self._lock:
            return self._states.setdefault(id(provider), ProviderCircuitState())

    def _provider_name(self, provider: LLMProvider) -> str:
        model = getattr(provider, "model", "")
        return f"{type(provider).__name__}:{model}" if model else type(provider).__name__

    def _record_failure(self, provider: LLMProvider, error: Exception) -> None:
        state = self._state_for(provider)
        with self._lock:
            state.consecutive_failures += 1
            state.last_error = str(error)
            if state.consecutive_failures >= self.failure_threshold:
                state.cooling_until = time.monotonic() + self.cooldown_sec
                self.on_provider_state(
                    {
                        "provider": self._provider_name(provider),
                        "state": "cooling_down",
                        "cooldown_sec": self.cooldown_sec,
                        "reason": state.last_error,
                    }
                )

    def _record_success(self, provider: LLMProvider, response: LLMResponse) -> None:
        state = self._state_for(provider)
        with self._lock:
            state.consecutive_failures = 0
            state.cooling_until = 0.0
            state.last_error = ""
            self._usage.input_tokens += response.input_tokens
            self._usage.output_tokens += response.output_tokens
            self._usage.estimated_cost_usd += self._estimate_cost(provider, response)

    def _estimate_cost(self, provider: LLMProvider, response: LLMResponse) -> float:
        if isinstance(provider, LocalProvider):
            return 0.0
        input_rate = float(getattr(provider, "input_cost_per_million", 3.0))
        output_rate = float(getattr(provider, "output_cost_per_million", 15.0))
        return (
            response.input_tokens * input_rate + response.output_tokens * output_rate
        ) / 1_000_000

    def _can_try(self, provider: LLMProvider) -> bool:
        state = self._state_for(provider)
        if not state.cooling_down:
            if state.cooling_until:
                state.cooling_until = 0.0
                state.consecutive_failures = 0
                self.on_provider_state(
                    {"provider": self._provider_name(provider), "state": "ready", "reason": "cooldown ended"}
                )
            return True
        return False

    @property
    def usage(self) -> SessionUsage:
        with self._lock:
            return SessionUsage(
                input_tokens=self._usage.input_tokens,
                output_tokens=self._usage.output_tokens,
                estimated_cost_usd=round(self._usage.estimated_cost_usd, 6),
            )

    def provider_states(self) -> list[dict]:
        providers = [self.preferred, *([self.fallback] if self.fallback else [])]
        output = []
        for provider in providers:
            state = self._state_for(provider)
            output.append(
                {
                    "provider": self._provider_name(provider),
                    "cooling_down": state.cooling_down,
                    "consecutive_failures": state.consecutive_failures,
                    "cooldown_remaining_sec": round(max(0.0, state.cooling_until - time.monotonic()), 1),
                    "last_error": state.last_error,
                }
            )
        return output

    def chat(self, *args, **kwargs) -> LLMResponse:
        errors: list[str] = []
        if self._can_try(self.preferred) and self.preferred.is_available():
            try:
                self.last_used = type(self.preferred).__name__
                response = self.preferred.chat(*args, **kwargs)
                self._record_success(self.preferred, response)
                return response
            except LLMProviderError as exc:
                self._record_failure(self.preferred, exc)
                errors.append(str(exc))

        if self.fallback and self._can_try(self.fallback) and self.fallback.is_available():
            try:
                self.last_used = type(self.fallback).__name__
                response = self.fallback.chat(*args, **kwargs)
                self._record_success(self.fallback, response)
                return response
            except LLMProviderError as exc:
                self._record_failure(self.fallback, exc)
                errors.append(str(exc))

        raise LLMProviderError(
            "No LLM backend available: preferred and fallback both failed, are cooling down, "
            "or are unconfigured." + (f" Last errors: {'; '.join(errors)}" if errors else "")
        )
