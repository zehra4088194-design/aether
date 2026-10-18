"""Checksum verification and explicit trust gating for third-party plugins."""

from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
from dataclasses import dataclass
from pathlib import Path

from aether.agent_loop import AetherPlugin


UNVERIFIED_WARNING = "This plugin is unverified — only install from sources you trust."


@dataclass
class PluginVerification:
    verified: bool
    plugin_id: str
    reason: str
    warning: str = ""


def verify_plugin_manifest(manifest_path: Path) -> PluginVerification:
    manifest_path = manifest_path.resolve()
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return PluginVerification(False, manifest_path.parent.name, f"Invalid manifest: {exc}", UNVERIFIED_WARNING)

    plugin_id = str(manifest.get("id") or manifest_path.parent.name)
    checksums = manifest.get("checksums")
    if not isinstance(checksums, dict) or not checksums:
        return PluginVerification(False, plugin_id, "No signed/checksummed files declared.", UNVERIFIED_WARNING)

    root = manifest_path.parent
    for relative, expected in checksums.items():
        path = (root / relative).resolve()
        if root != path and root not in path.parents:
            return PluginVerification(False, plugin_id, f"Unsafe manifest path: {relative}", UNVERIFIED_WARNING)
        if not path.is_file():
            return PluginVerification(False, plugin_id, f"Missing plugin file: {relative}", UNVERIFIED_WARNING)
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if not hmac.compare_digest(actual.lower(), str(expected).lower()):
            return PluginVerification(False, plugin_id, f"Checksum mismatch: {relative}", UNVERIFIED_WARNING)
    return PluginVerification(True, plugin_id, "All declared SHA-256 checksums match.")


class ThirdPartyPluginLoader:
    def inspect(self, plugin_dir: Path) -> PluginVerification:
        return verify_plugin_manifest(plugin_dir / "plugin.json")

    def load(self, plugin_dir: Path, *, allow_unverified: bool = False) -> AetherPlugin:
        plugin_dir = plugin_dir.resolve()
        manifest_path = plugin_dir / "plugin.json"
        verification = verify_plugin_manifest(manifest_path)
        if not verification.verified and not allow_unverified:
            raise PermissionError(f"{verification.reason} {UNVERIFIED_WARNING}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        entrypoint = str(manifest.get("entrypoint", "plugin.py:AetherThirdPartyPlugin"))
        module_file, _, class_name = entrypoint.partition(":")
        class_name = class_name or "AetherThirdPartyPlugin"
        module_path = (plugin_dir / module_file).resolve()
        if plugin_dir not in module_path.parents:
            raise ValueError("Plugin entrypoint escapes its plugin directory.")
        spec = importlib.util.spec_from_file_location(f"aether_third_party_{verification.plugin_id}", module_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Could not load plugin entrypoint: {module_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        plugin_class = getattr(module, class_name)
        plugin = plugin_class()
        if not isinstance(plugin, AetherPlugin):
            raise TypeError("Third-party entrypoint must return an AetherPlugin subclass.")
        return plugin
