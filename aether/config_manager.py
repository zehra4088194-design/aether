"""Thread-safe YAML settings and secret-safe model/voice persistence."""

from __future__ import annotations

import copy
import os
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional

import yaml

from aether.model_registry import ModelEntry, ModelKind, ModelRegistry
from aether.system_interface import SystemInterface


DEFAULT_CONFIG: dict[str, Any] = {
    "app": {"name": "Aether", "wake_word": "Hey Aether", "first_run_complete": False},
    "performance": {"tier_override": None},
    "llm": {
        "mode": "local_preferred",
        "cloud": {
            "provider": "anthropic",
            "model": "claude-sonnet-4-6",
            "api_key_env_var": "ANTHROPIC_API_KEY",
        },
        "local": {"ollama_host": "http://localhost:11434", "model_override": None},
    },
    "models": {"default_id": None, "entries": []},
    "vision": {"local_model": "llava:7b"},
    "voice": {
        "active_profile_id": "amara",
        "global_hotkey": "ctrl+alt+space",
        "wake_word_engine": "openwakeword",
        "wakeword_model_path": None,
        "elevenlabs_voice_id": "",
        "stt_model_override": None,
        "tts_engine": "piper",
        "speak_responses": False,
    },
    "persona": {
        "assistant_name": "Aether",
        "user_name": "",
        "style": "warm, concise, transparent",
        "languages": ["English", "Urdu", "Roman Urdu"],
    },
    "privacy": {
        "consent_completed": False,
        "voice_recording": False,
        "camera_vision": False,
        "screen_vision": False,
        "browsing_history": False,
        "memory_storage": False,
        "telemetry": False,
    },
    "diagnostics": {"enabled": False},
    "plugins": {
        "file_automation": True,
        "browser_automation": True,
        "voice": False,
        "coding_agent": False,
        "memory": False,
        "camera_vision": False,
        "proactive_engine": False,
        "trading": False,
        "productivity": True,
        "visual_intelligence": True,
    },
    "memory": {"vector_store": "chroma", "max_items_override": None},
    "sub_agents": {"max_parallel": 4},
    "ui": {"theme": "aether", "high_contrast": False, "reduced_motion": False},
    "updates": {
        "enabled": True,
        "manifest_url": "https://github.com/aether-desktop/aether/releases/latest/download/latest.json",
    },
}


def _deep_merge(base: dict, overlay: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


class ConfigManager:
    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self._lock = threading.RLock()
        self._data: dict[str, Any] = {}
        self.load()

    def load(self) -> dict[str, Any]:
        with self._lock:
            existing: dict = {}
            if self.path.exists():
                parsed = yaml.safe_load(self.path.read_text(encoding="utf-8"))
                if isinstance(parsed, dict):
                    existing = parsed
            self._data = _deep_merge(DEFAULT_CONFIG, existing)
            return copy.deepcopy(self._data)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._data)

    def get(self, dotted_path: str, default: Any = None) -> Any:
        with self._lock:
            current: Any = self._data
            for part in dotted_path.split("."):
                if not isinstance(current, dict) or part not in current:
                    return default
                current = current[part]
            return copy.deepcopy(current)

    def set(self, dotted_path: str, value: Any) -> None:
        sensitive = {part.lower() for part in dotted_path.split(".")}
        if sensitive & {"api_key", "password", "secret", "token"}:
            raise ValueError("Secrets must be stored through SystemInterface credential storage.")
        with self._lock:
            current = self._data
            parts = dotted_path.split(".")
            for part in parts[:-1]:
                current = current.setdefault(part, {})
                if not isinstance(current, dict):
                    raise ValueError(f"Cannot set nested key under non-object: {part}")
            current[parts[-1]] = copy.deepcopy(value)
            self.save()

    def update_section(self, section: str, values: dict) -> None:
        for key in values:
            if key.lower() in {"api_key", "password", "secret", "token"}:
                raise ValueError("Secrets must be stored through SystemInterface credential storage.")
        with self._lock:
            current = self._data.get(section, {})
            if not isinstance(current, dict):
                current = {}
            self._data[section] = _deep_merge(current, values)
            self.save()

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
            temp_path.write_text(
                yaml.safe_dump(self._data, sort_keys=False, allow_unicode=True), encoding="utf-8"
            )
            temp_path.replace(self.path)

    def hydrate_model_registry(self) -> ModelRegistry:
        registry = ModelRegistry()
        entries = self.get("models.entries", [])
        default_id = self.get("models.default_id")
        for raw in entries:
            if not isinstance(raw, dict):
                continue
            try:
                entry = ModelEntry(
                    id=str(raw["id"]),
                    display_name=str(raw["display_name"]),
                    kind=ModelKind(raw["kind"]),
                    model_id=str(raw["model_id"]),
                    endpoint_url=raw.get("endpoint_url"),
                    api_key_env_var=raw.get("api_key_env_var"),
                    is_default=str(raw["id"]) == default_id or bool(raw.get("is_default", False)),
                    notes=str(raw.get("notes", "")),
                )
                registry.import_entry(entry)
            except (KeyError, TypeError, ValueError):
                continue
        return registry

    def persist_model_registry(self, registry: ModelRegistry) -> None:
        entries = []
        for entry in registry.list_models():
            raw = asdict(entry)
            raw["kind"] = entry.kind.value
            entries.append(raw)
        default = registry.get_default()
        with self._lock:
            self._data["models"] = {
                "default_id": default.id if default else None,
                "entries": entries,
            }
            self.save()


def model_credential_service(entry_id: str) -> str:
    return f"model:{entry_id}"


def store_model_api_key(sysiface: SystemInterface, entry_id: str, api_key: str) -> None:
    sysiface.store_credential(model_credential_service(entry_id), "api_key", api_key)


def resolve_model_api_key(sysiface: SystemInterface, entry: ModelEntry) -> Optional[str]:
    if entry.api_key_env_var:
        from_environment = os.environ.get(entry.api_key_env_var)
        if from_environment:
            return from_environment
    credential = sysiface.get_credential(model_credential_service(entry.id), "api_key")
    return credential.secret if credential else None
