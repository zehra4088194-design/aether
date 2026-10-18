"""
Aether - Model Registry
==========================
Module 6 of 7

Purpose
-------
The original LLMProvider (Module 3) hardcodes exactly two backends:
Anthropic (cloud) and Ollama (local). This module turns that into an
open, user-editable registry so the Settings UI can let a person add
ANY model they want — another cloud API (OpenAI, Mistral, a
self-hosted endpoint, Groq, etc.) or any local Ollama model already
pulled on their machine — without touching code.

Everything here is just configuration + a thin adapter; the actual
request/response handling still goes through `llm_provider.py`'s
`LLMProvider` interface so `agent_loop.py` never has to change.

Persistence: entries are stored in `aether.config.yaml` under
`models:` so they survive restarts. This module only defines the data
model + validation + the adapter that turns a `ModelEntry` into a live
`LLMProvider` instance.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

from aether.llm_provider import CloudProvider, LocalProvider, LLMProvider
from aether.hardware_probe import TierConfig


class ModelKind(str, enum.Enum):
    CLOUD_ANTHROPIC = "cloud_anthropic"
    CLOUD_OPENAI_COMPATIBLE = "cloud_openai_compatible"  # OpenAI, Groq, Mistral, self-hosted, etc.
    LOCAL_OLLAMA = "local_ollama"


@dataclass
class ModelEntry:
    """One model the user has added in Settings > Models."""

    id: str
    display_name: str          # what the user sees in the model picker, e.g. "My Work Claude"
    kind: ModelKind
    model_id: str               # e.g. "claude-sonnet-4-6", "gpt-4o", "llama3:8b-instruct-q4_K_M"
    endpoint_url: Optional[str] = None       # required for CLOUD_OPENAI_COMPATIBLE / custom Ollama host
    api_key_env_var: Optional[str] = None    # name of the env var holding the key — never the key itself
    is_default: bool = False
    notes: str = ""


@dataclass
class ModelValidationError(Exception):
    message: str

    def __str__(self) -> str:
        return self.message


class ModelRegistry:
    """In-memory registry, hydrated from / persisted to aether.config.yaml's
    `models:` section by ConfigManager. This class owns validation and
    instantiation logic so it remains UI-agnostic."""

    def __init__(self) -> None:
        self._entries: dict[str, ModelEntry] = {}

    # ---- CRUD used by the Settings UI --------------------------------------------
    def add_model(
        self,
        display_name: str,
        kind: ModelKind,
        model_id: str,
        endpoint_url: Optional[str] = None,
        api_key_env_var: Optional[str] = None,
        notes: str = "",
    ) -> ModelEntry:
        self._validate(kind, model_id, endpoint_url)
        entry = ModelEntry(
            id=str(uuid.uuid4()),
            display_name=display_name,
            kind=kind,
            model_id=model_id,
            endpoint_url=endpoint_url,
            api_key_env_var=api_key_env_var,
            notes=notes,
        )
        self._entries[entry.id] = entry
        return entry

    def import_entry(self, entry: ModelEntry) -> None:
        """Hydrate a previously validated entry without generating a new id."""
        self._validate(entry.kind, entry.model_id, entry.endpoint_url)
        self._entries[entry.id] = entry

    def update_model(
        self,
        model_entry_id: str,
        *,
        display_name: Optional[str] = None,
        kind: Optional[ModelKind] = None,
        model_id: Optional[str] = None,
        endpoint_url: Optional[str] = None,
        api_key_env_var: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> ModelEntry:
        entry = self._entries.get(model_entry_id)
        if entry is None:
            raise ModelValidationError(f"No such model entry: {model_entry_id}")
        next_kind = kind or entry.kind
        next_model_id = model_id if model_id is not None else entry.model_id
        next_endpoint = endpoint_url if endpoint_url is not None else entry.endpoint_url
        self._validate(next_kind, next_model_id, next_endpoint)
        entry.display_name = display_name if display_name is not None else entry.display_name
        entry.kind = next_kind
        entry.model_id = next_model_id
        entry.endpoint_url = next_endpoint
        entry.api_key_env_var = (
            api_key_env_var if api_key_env_var is not None else entry.api_key_env_var
        )
        entry.notes = notes if notes is not None else entry.notes
        return entry

    def remove_model(self, model_entry_id: str) -> bool:
        return self._entries.pop(model_entry_id, None) is not None

    def list_models(self) -> list[ModelEntry]:
        return list(self._entries.values())

    def get_model(self, model_entry_id: str) -> Optional[ModelEntry]:
        return self._entries.get(model_entry_id)

    def set_default(self, model_entry_id: str) -> None:
        if model_entry_id not in self._entries:
            raise ModelValidationError(f"No such model entry: {model_entry_id}")
        for entry in self._entries.values():
            entry.is_default = entry.id == model_entry_id

    def get_default(self) -> Optional[ModelEntry]:
        for entry in self._entries.values():
            if entry.is_default:
                return entry
        return next(iter(self._entries.values()), None)

    # ---- Validation -----------------------------------------------------------------
    def _validate(self, kind: ModelKind, model_id: str, endpoint_url: Optional[str]) -> None:
        if not model_id.strip():
            raise ModelValidationError("Model ID cannot be empty.")
        if kind == ModelKind.CLOUD_OPENAI_COMPATIBLE and not endpoint_url:
            raise ModelValidationError(
                "A custom/OpenAI-compatible cloud model requires an endpoint URL."
            )
        if endpoint_url:
            parsed = urlparse(endpoint_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ModelValidationError("Model endpoint must be a valid http(s) URL.")

    # ---- Turning a stored entry into a live provider -------------------------------------
    def instantiate(
        self, entry: ModelEntry, tier_config: TierConfig, api_key: Optional[str] = None
    ) -> LLMProvider:
        """Build a live LLMProvider from a stored ModelEntry.

        `api_key` is resolved by the caller (e.g. read from
        os.environ[entry.api_key_env_var] or Windows Credential Manager)
        and passed in here — this module never reads secrets itself, to
        keep secret-resolution in one place (system_interface.py /
        settings layer).
        """
        if entry.kind == ModelKind.CLOUD_ANTHROPIC:
            return CloudProvider(api_key=api_key or "", model=entry.model_id)

        if entry.kind == ModelKind.CLOUD_OPENAI_COMPATIBLE:
            return OpenAICompatibleProvider(
                endpoint_url=entry.endpoint_url or "",
                api_key=api_key or "",
                model=entry.model_id,
            )

        if entry.kind == ModelKind.LOCAL_OLLAMA:
            provider = LocalProvider(tier_config=tier_config, model_override=entry.model_id)
            if entry.endpoint_url:
                provider.OLLAMA_URL = entry.endpoint_url.rstrip("/") + "/api/chat"
            return provider

        raise ModelValidationError(f"Unsupported model kind: {entry.kind}")

    def discover_ollama_models(self, host: str = "http://localhost:11434") -> list[dict]:
        """Return models already pulled locally without downloading anything."""
        import requests

        try:
            response = requests.get(host.rstrip("/") + "/api/tags", timeout=3)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise ModelValidationError(f"Could not reach Ollama: {exc}") from exc
        models = response.json().get("models", [])
        return [
            {
                "name": str(item.get("name", "")),
                "size": int(item.get("size", 0)),
                "modified_at": item.get("modified_at"),
                "details": item.get("details", {}),
            }
            for item in models
            if item.get("name")
        ]


# --------------------------------------------------------------------------- #
# Generic OpenAI-compatible provider — covers OpenAI itself, Groq, Mistral,
# Together, self-hosted vLLM/LM Studio endpoints, etc., since almost all of
# them speak the same /chat/completions shape.
# --------------------------------------------------------------------------- #

class OpenAICompatibleProvider(LLMProvider):
    def __init__(
        self,
        endpoint_url: str,
        api_key: str,
        model: str,
        input_cost_per_million: float = 1.0,
        output_cost_per_million: float = 3.0,
    ):
        self.endpoint_url = endpoint_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.input_cost_per_million = input_cost_per_million
        self.output_cost_per_million = output_cost_per_million

    def is_available(self) -> bool:
        return bool(self.endpoint_url)

    def chat(self, messages, tools=None, system=None, max_tokens=1024):
        import requests
        from aether.llm_provider import LLMResponse, ToolCall, LLMProviderError

        payload_messages = list(messages)
        if system:
            payload_messages = [{"role": "system", "content": system}, *payload_messages]

        body: dict = {"model": self.model, "messages": payload_messages, "max_tokens": max_tokens}
        if tools:
            body["tools"] = [
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

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            resp = requests.post(
                f"{self.endpoint_url}/chat/completions", headers=headers, json=body, timeout=60
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            raise LLMProviderError(f"OpenAI-compatible provider failed: {e}") from e

        data = resp.json()
        choice = data["choices"][0]["message"]
        tool_calls = []
        for i, call in enumerate(choice.get("tool_calls", []) or []):
            fn = call.get("function", {})
            import json as _json

            args = fn.get("arguments", "{}")
            args = _json.loads(args) if isinstance(args, str) else args
            tool_calls.append(ToolCall(id=call.get("id", str(i)), name=fn.get("name", ""), arguments=args))

        return LLMResponse(
            text=choice.get("content") or "",
            tool_calls=tool_calls,
            stop_reason="tool_use" if tool_calls else "end_turn",
            raw=data,
            input_tokens=int(data.get("usage", {}).get("prompt_tokens", 0)),
            output_tokens=int(data.get("usage", {}).get("completion_tokens", 0)),
        )
