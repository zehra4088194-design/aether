from __future__ import annotations

from pathlib import Path

import pytest

from aether.config_manager import ConfigManager
from aether.model_registry import ModelKind, ModelValidationError


def test_privacy_defaults_are_off(tmp_path: Path) -> None:
    config = ConfigManager(tmp_path / "aether.config.yaml")
    privacy = config.get("privacy")
    assert privacy["consent_completed"] is False
    assert all(value is False for value in privacy.values())


def test_rejects_plaintext_secret_paths(tmp_path: Path) -> None:
    config = ConfigManager(tmp_path / "aether.config.yaml")
    with pytest.raises(ValueError):
        config.set("models.api_key", "do-not-store")


def test_model_registry_round_trip(tmp_path: Path) -> None:
    config = ConfigManager(tmp_path / "aether.config.yaml")
    registry = config.hydrate_model_registry()
    entry = registry.add_model("Local Test", ModelKind.LOCAL_OLLAMA, "qwen3:4b")
    registry.set_default(entry.id)
    config.persist_model_registry(registry)

    restored = ConfigManager(config.path).hydrate_model_registry()
    assert restored.get_default() is not None
    assert restored.get_default().model_id == "qwen3:4b"


def test_model_registry_entry_can_be_edited_without_changing_identity(tmp_path: Path) -> None:
    config = ConfigManager(tmp_path / "aether.config.yaml")
    registry = config.hydrate_model_registry()
    entry = registry.add_model("Local Test", ModelKind.LOCAL_OLLAMA, "qwen3:4b")

    updated = registry.update_model(
        entry.id,
        display_name="Local Fast",
        model_id="qwen3:8b",
        notes="preferred on AC power",
    )

    assert updated.id == entry.id
    assert updated.display_name == "Local Fast"
    assert updated.model_id == "qwen3:8b"
    assert updated.notes == "preferred on AC power"


def test_model_registry_rejects_non_http_endpoint(tmp_path: Path) -> None:
    registry = ConfigManager(tmp_path / "aether.config.yaml").hydrate_model_registry()
    with pytest.raises(ModelValidationError):
        registry.add_model(
            "Unsafe endpoint",
            ModelKind.CLOUD_OPENAI_COMPATIBLE,
            "some-model",
            endpoint_url="file:///C:/secret",
        )
