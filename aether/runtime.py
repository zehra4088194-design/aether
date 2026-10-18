"""Aether application runtime: one safe wiring point for API and Tauri."""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
import zipfile
from collections import deque
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import psutil

from aether.agent_loop import AgentConfig, AgentLoop, AgentStep, PluginRegistry, ToolResult
from aether.config_manager import (
    ConfigManager,
    resolve_model_api_key,
    store_model_api_key,
)
from aether.hardware_probe import HardwareProbe, PerformanceTier
from aether.llm_provider import CloudProvider, LocalProvider, ManagedLLM
from aether.model_registry import ModelEntry, ModelKind, ModelRegistry
from aether.sub_agent_orchestrator import SubAgentOrchestrator, SubAgentStatus
from aether.system_interface import SystemInterface, get_system_interface
from aether.plugins.browser_automation_plugin import BrowserAutomationPlugin
from aether.plugins.camera_vision_plugin import CameraVisionPlugin
from aether.plugins.coding_agent_plugin import CodingAgentPlugin
from aether.plugins.file_automation_plugin import FileAutomationPlugin
from aether.plugins.memory_plugin import MemoryPlugin
from aether.plugins.proactive_engine_plugin import ProactiveEnginePlugin
from aether.plugins.productivity_plugin import ProductivityPlugin
from aether.plugins.trading_plugin import TradingPlugin
from aether.plugins.visual_intelligence_plugin import VisualIntelligencePlugin
from aether.plugins.voice_plugin import VoicePlugin


class EventBus:
    def __init__(self, history_size: int = 500) -> None:
        self._listeners: list[Callable[[dict], None]] = []
        self._history: deque[dict] = deque(maxlen=history_size)
        self._lock = threading.RLock()

    def emit(self, event_type: str, data: dict) -> dict:
        event = {
            "id": str(uuid.uuid4()),
            "type": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": data,
        }
        with self._lock:
            self._history.append(event)
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(event)
            except Exception:
                continue
        return event

    def subscribe(self, listener: Callable[[dict], None]) -> Callable[[], None]:
        with self._lock:
            self._listeners.append(listener)

        def unsubscribe() -> None:
            with self._lock:
                if listener in self._listeners:
                    self._listeners.remove(listener)

        return unsubscribe

    def history(self) -> list[dict]:
        with self._lock:
            return list(self._history)


class ConfirmationBroker:
    def __init__(self, events: EventBus, timeout_sec: int = 300) -> None:
        self.events = events
        self.timeout_sec = timeout_sec
        self._pending: dict[str, dict] = {}
        self._lock = threading.RLock()

    def request(self, tool_name: str, description: str, arguments: dict) -> bool:
        confirmation_id = str(uuid.uuid4())
        event = threading.Event()
        payload = {
            "id": confirmation_id,
            "tool": tool_name,
            "description": description,
            "arguments": arguments,
            "expires_in_sec": self.timeout_sec,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        with self._lock:
            self._pending[confirmation_id] = {**payload, "event": event, "decision": None}
        self.events.emit("confirmation_required", payload)
        event.wait(self.timeout_sec)
        with self._lock:
            pending = self._pending.pop(confirmation_id, {"decision": False})
        approved = bool(pending.get("decision"))
        self.events.emit(
            "confirmation_resolved",
            {"id": confirmation_id, "tool": tool_name, "approved": approved},
        )
        return approved

    def resolve(self, confirmation_id: str, approved: bool) -> bool:
        with self._lock:
            pending = self._pending.get(confirmation_id)
            if pending is None:
                return False
            pending["decision"] = bool(approved)
            pending["event"].set()
            return True

    def pending(self) -> list[dict]:
        with self._lock:
            return [
                {key: value for key, value in item.items() if key not in {"event", "decision"}}
                for item in self._pending.values()
            ]


class AetherRuntime:
    def __init__(
        self,
        *,
        sysiface: Optional[SystemInterface] = None,
        config_path: Optional[Path] = None,
    ) -> None:
        self.sysiface = sysiface or get_system_interface()
        self.data_dir = self.sysiface.app_data_dir()
        env_config = os.environ.get("AETHER_CONFIG_PATH")
        self.config_path = config_path or (Path(env_config) if env_config else self._prepare_user_config())
        self.config = ConfigManager(self.config_path)
        self.events = EventBus()
        self.confirmations = ConfirmationBroker(self.events)
        self.hardware = HardwareProbe()
        self.hardware.detect()
        override = self.config.get("performance.tier_override")
        if override:
            try:
                self.hardware.set_manual_tier(PerformanceTier(override))
            except ValueError:
                pass
        self.model_registry = self.config.hydrate_model_registry()
        self.llm = self._build_managed_llm()
        self.registry = PluginRegistry()
        self._plugin_instances: dict[str, object] = {}
        self._active_subagents: dict[str, dict] = {}
        self._throttled_plugins: set[str] = set()
        self._resource_throttled = False
        self._conversation: list[dict] = []
        self._chat_lock = threading.Lock()
        self._register_plugins()
        self.direct_loop = AgentLoop(
            llm=self.llm,
            registry=self.registry,
            confirm_callback=self.confirmations.request,
            on_step=self._on_direct_step,
            config=AgentConfig(system_prompt=self._system_prompt()),
        )
        self.orchestrator = SubAgentOrchestrator(
            llm=self.llm,
            registry=self.registry,
            confirm_callback=self.confirmations.request,
            on_status=self._on_subagent_status,
            on_step=self._on_agent_step,
            max_parallel=min(max(int(self.config.get("sub_agents.max_parallel", 4)), 1), 4),
            system_prompt=self._system_prompt(),
        )
        self.hardware.start_monitoring(self._on_throttle, on_tier_change=self._on_tier_change)

    def _prepare_user_config(self) -> Path:
        user_path = self.data_dir / "aether.config.yaml"
        if not user_path.exists():
            project_path = Path(__file__).resolve().parent.parent / "aether.config.yaml"
            if project_path.exists():
                user_path.write_text(project_path.read_text(encoding="utf-8"), encoding="utf-8")
        return user_path

    def _system_prompt(self) -> str:
        persona = self.config.get("persona", {})
        assistant_name = str(persona.get("assistant_name", "Aether"))
        user_name = str(persona.get("user_name", "")).strip()
        style = str(persona.get("style", "warm, concise, transparent"))
        languages = ", ".join(str(item) for item in persona.get("languages", []))
        language_context = languages or "the user's language"
        user_context = f" Address the user as {user_name}." if user_name else ""
        return (
            f"You are {assistant_name}, a helpful, transparent Windows desktop assistant. "
            f"Your communication style is {style}. You may respond in {language_context}."
            f"{user_context} Use available tools to complete requests. Narrate briefly before acting. "
            "Never claim success without a tool result, and never pressure the user to approve an action."
        )

    def _legacy_providers(self):
        cloud_cfg = self.config.get("llm.cloud", {})
        local_cfg = self.config.get("llm.local", {})
        cloud_key = os.environ.get(str(cloud_cfg.get("api_key_env_var", "ANTHROPIC_API_KEY")), "")
        cloud = CloudProvider(api_key=cloud_key, model=str(cloud_cfg.get("model", "claude-sonnet-4-6")))
        local = LocalProvider(
            tier_config=self.hardware.config,
            model_override=local_cfg.get("model_override"),
        )
        host = str(local_cfg.get("ollama_host", "http://localhost:11434")).rstrip("/")
        local.OLLAMA_URL = host + "/api/chat"
        mode = self.config.get("llm.mode", "local_preferred")
        if mode == "cloud_only":
            return cloud, None
        if mode == "local_only":
            return local, None
        if mode == "cloud_preferred":
            return cloud, local
        return local, cloud

    def _build_managed_llm(self) -> ManagedLLM:
        default = self.model_registry.get_default()
        if default:
            provider = self.model_registry.instantiate(
                default,
                self.hardware.config,
                resolve_model_api_key(self.sysiface, default),
            )
            preferred, fallback = provider, None
        else:
            preferred, fallback = self._legacy_providers()
        return ManagedLLM(
            preferred,
            fallback,
            on_provider_state=lambda state: self.events.emit("provider_state", state),
        )

    def _build_vision_llm(self) -> ManagedLLM:
        local_cfg = self.config.get("llm.local", {})
        provider = LocalProvider(
            tier_config=self.hardware.config,
            model_override=str(self.config.get("vision.local_model", "llava:7b")),
        )
        provider.OLLAMA_URL = (
            str(local_cfg.get("ollama_host", "http://localhost:11434")).rstrip("/") + "/api/chat"
        )
        return ManagedLLM(
            provider,
            on_provider_state=lambda state: self.events.emit(
                "provider_state", {**state, "scope": "vision"}
            ),
        )

    def _enabled(self, plugin_name: str, default: bool = False) -> bool:
        return bool(self.config.get(f"plugins.{plugin_name}", default))

    def _register(self, plugin, *, enabled: bool) -> None:
        self._plugin_instances[plugin.name] = plugin
        self.registry.register(plugin, enabled=enabled)

    def _register_plugins(self) -> None:
        privacy = self.config.get("privacy", {})
        self._register(FileAutomationPlugin(self.sysiface), enabled=self._enabled("file_automation", True))
        self._register(
            BrowserAutomationPlugin(self.sysiface, self.llm),
            enabled=self._enabled("browser_automation", True),
        )
        voice = VoicePlugin(
            self.hardware.config,
            self.sysiface,
            active_voice_id=str(self.config.get("voice.active_profile_id", "amara")),
            global_hotkey=str(self.config.get("voice.global_hotkey", "ctrl+alt+space")),
            wakeword_model_path=self.config.get("voice.wakeword_model_path"),
            elevenlabs_voice_id=str(self.config.get("voice.elevenlabs_voice_id", "")),
            on_trigger=lambda source: self.events.emit("voice_trigger", {"source": source}),
        )
        self._register(
            voice,
            enabled=self._enabled("voice") and bool(privacy.get("voice_recording")),
        )
        self.vision_llm = self._build_vision_llm()
        camera = CameraVisionPlugin(
            self.vision_llm,
            camera_enabled=bool(privacy.get("camera_vision")),
            screen_enabled=bool(privacy.get("screen_vision")),
        )
        self._register(
            camera,
            enabled=self._enabled("camera_vision") and (
                bool(privacy.get("camera_vision")) or bool(privacy.get("screen_vision"))
            ),
        )
        self._register(
            CodingAgentPlugin(self.sysiface, self.hardware.config, self.data_dir / "coding-workspace"),
            enabled=self._enabled("coding_agent"),
        )
        memory = MemoryPlugin(
            self.hardware.config,
            self.llm,
            self.data_dir / "memory",
            on_summary_due=lambda count: self.events.emit(
                "memory_summary_due", {"record_count": count, "action": "summarize_memories"}
            ),
        )
        self._register(
            memory,
            enabled=self._enabled("memory") and bool(privacy.get("memory_storage")),
        )
        self._register(
            ProactiveEnginePlugin(
                self.hardware.config,
                self.llm,
                enabled=self._enabled("proactive_engine", self.hardware.config.proactive_mode_default_on),
            ),
            enabled=self._enabled("proactive_engine", self.hardware.config.proactive_mode_default_on),
        )
        self._register(TradingPlugin(), enabled=self._enabled("trading"))
        self._register(
            ProductivityPlugin(self.data_dir / "productivity.json"),
            enabled=self._enabled("productivity", True),
        )
        self._register(
            VisualIntelligencePlugin(self.llm),
            enabled=self._enabled("visual_intelligence", True),
        )

    def _on_direct_step(self, step: AgentStep) -> None:
        self.events.emit("agent_step", {"agent_id": "main", **asdict(step)})

    def _on_agent_step(self, agent_id: str, step: AgentStep) -> None:
        self.events.emit("agent_step", {"agent_id": agent_id, **asdict(step)})

    def _on_subagent_status(self, status: SubAgentStatus) -> None:
        payload = asdict(status)
        self._active_subagents[status.id] = payload
        self.events.emit("subagent_status", payload)

    def _on_throttle(self, active: bool) -> None:
        self._resource_throttled = active
        if active:
            disabled = self.registry.disable_heavy_plugins()
            self._throttled_plugins.update(disabled)
            restored: list[str] = []
        else:
            disabled = []
            restored = []
            privacy = self.config.get("privacy", {})
            for name in list(self._throttled_plugins):
                privacy_allowed = name != "camera_vision" or bool(
                    privacy.get("camera_vision") or privacy.get("screen_vision")
                )
                if self._enabled(name) and privacy_allowed:
                    self.registry.enable(name)
                    restored.append(name)
                self._throttled_plugins.discard(name)
        self.events.emit(
            "throttle",
            {"active": active, "disabled_plugins": disabled, "restored_plugins": restored},
        )

    def _on_tier_change(self, previous: PerformanceTier, current: PerformanceTier) -> None:
        with self._chat_lock:
            self.rebuild_llm()
            if self._resource_throttled:
                self._throttled_plugins.update(self.registry.disable_heavy_plugins())
        self.events.emit("tier_changed", {"previous": previous.value, "current": current.value})

    def chat(self, message: str) -> str:
        if not self.config.get("privacy.consent_completed", False):
            raise PermissionError("Complete the first-run privacy choices before using Aether.")
        with self._chat_lock:
            self.events.emit("chat_started", {"message": message})
            history = list(self._conversation[-24:])
            result = self.orchestrator.run(message, history=history)
            self._conversation.extend(
                [{"role": "user", "content": message}, {"role": "assistant", "content": result}]
            )
            # This continuous local backup is covered by the separately worded
            # first-run "Memory storage" consent. Agent-requested ad-hoc memory
            # writes still use the central per-action confirmation broker.
            memory = self._plugin_instances.get("memory")
            if isinstance(memory, MemoryPlugin) and self.registry.is_enabled("memory"):
                try:
                    memory.add_record(
                        json.dumps({"user": message, "assistant": result}, ensure_ascii=False),
                        kind="conversation",
                        metadata={"consent": "memory_storage"},
                    )
                except RuntimeError:
                    pass
            voice = self._plugin_instances.get("voice")
            if (
                isinstance(voice, VoicePlugin)
                and self.registry.is_enabled("voice")
                and self.config.get("voice.speak_responses", False)
            ):
                voice.tts.feed(result)
                voice.tts.flush()
            self.events.emit("chat_finished", {"message": message, "response": result})
            return result

    def execute_tool(self, name: str, arguments: Optional[dict] = None) -> ToolResult:
        return self.direct_loop.execute_tool(name, arguments)

    def set_privacy(self, choices: dict) -> dict:
        allowed = {
            "voice_recording",
            "camera_vision",
            "screen_vision",
            "browsing_history",
            "memory_storage",
            "telemetry",
        }
        sanitized = {key: bool(value) for key, value in choices.items() if key in allowed}
        sanitized["consent_completed"] = True
        self.config.update_section("privacy", sanitized)
        self.config.set("app.first_run_complete", True)
        # Sensitive plugins remain opt-in and mirror the corresponding consent.
        mappings = {
            "voice": sanitized.get("voice_recording", False),
            "camera_vision": sanitized.get("camera_vision", False) or sanitized.get("screen_vision", False),
            "memory": sanitized.get("memory_storage", False),
        }
        for plugin_name, enabled in mappings.items():
            self.config.set(f"plugins.{plugin_name}", enabled)
            if enabled:
                self.registry.enable(plugin_name)
            else:
                self.registry.disable(plugin_name)
        camera = self._plugin_instances.get("camera_vision")
        if isinstance(camera, CameraVisionPlugin):
            camera.camera_enabled = sanitized.get("camera_vision", False)
            camera.screen_enabled = sanitized.get("screen_vision", False)
        return self.config.get("privacy")

    def set_plugin_enabled(self, name: str, enabled: bool) -> None:
        privacy_guard = {
            "voice": "voice_recording",
            "memory": "memory_storage",
        }
        required = privacy_guard.get(name)
        if enabled and required and not self.config.get(f"privacy.{required}", False):
            raise PermissionError(f"Enable {required.replace('_', ' ')} consent first.")
        if enabled and name == "camera_vision" and not (
            self.config.get("privacy.camera_vision", False)
            or self.config.get("privacy.screen_vision", False)
        ):
            raise PermissionError("Enable camera or screen vision consent first.")
        if enabled:
            self.registry.enable(name)
        else:
            self.registry.disable(name)
        self.config.set(f"plugins.{name}", enabled)
        self.events.emit("plugin_state", {"name": name, "enabled": enabled})

    def select_voice(self, voice_id: str) -> dict:
        voice = self._plugin_instances["voice"]
        assert isinstance(voice, VoicePlugin)
        profile = voice.registry.get(voice_id)
        if profile is None:
            raise ValueError(f"Unknown voice id: {voice_id}")
        if profile.engine == "elevenlabs" and (
            not profile.voice_ref or not self.sysiface.get_credential("elevenlabs", "api_key")
        ):
            raise ValueError("Configure the ElevenLabs voice ID and API key before selecting it.")
        voice.registry.set_active(voice_id)
        self.config.set("voice.active_profile_id", voice_id)
        return asdict(voice.registry.active)

    def configure_elevenlabs_voice(self, voice_id: str, api_key: Optional[str] = None) -> dict:
        voice_id = voice_id.strip()
        if not voice_id:
            raise ValueError("ElevenLabs voice ID cannot be empty.")
        if api_key:
            self.sysiface.store_credential("elevenlabs", "api_key", api_key)
        self.config.set("voice.elevenlabs_voice_id", voice_id)
        voice = self._plugin_instances.get("voice")
        if isinstance(voice, VoicePlugin):
            profile = voice.registry.get("custom-elevenlabs")
            if profile is not None:
                profile.voice_ref = voice_id
        return {"configured": True, "voice_id": voice_id, "has_api_key": bool(api_key)}

    def rebuild_llm(self) -> None:
        self.llm = self._build_managed_llm()
        # Existing plugin instances retain the old ManagedLLM, so rebuild the
        # complete lightweight registry at the explicit Settings switch point.
        for state in self.registry.plugin_states():
            if state["enabled"]:
                self.registry.disable(state["name"])
        self.registry = PluginRegistry()
        self._plugin_instances = {}
        self._register_plugins()
        self.direct_loop = AgentLoop(
            llm=self.llm,
            registry=self.registry,
            confirm_callback=self.confirmations.request,
            on_step=self._on_direct_step,
            config=AgentConfig(system_prompt=self._system_prompt()),
        )
        self.orchestrator = SubAgentOrchestrator(
            llm=self.llm,
            registry=self.registry,
            confirm_callback=self.confirmations.request,
            on_status=self._on_subagent_status,
            on_step=self._on_agent_step,
            max_parallel=min(max(int(self.config.get("sub_agents.max_parallel", 4)), 1), 4),
            system_prompt=self._system_prompt(),
        )
        self.events.emit("model_switched", self.model_state())

    def add_model(self, payload: dict, api_key: Optional[str] = None) -> ModelEntry:
        entry = self.model_registry.add_model(
            display_name=str(payload["display_name"]),
            kind=ModelKind(payload["kind"]),
            model_id=str(payload["model_id"]),
            endpoint_url=payload.get("endpoint_url"),
            api_key_env_var=payload.get("api_key_env_var"),
            notes=str(payload.get("notes", "")),
        )
        if api_key:
            store_model_api_key(self.sysiface, entry.id, api_key)
        if payload.get("is_default") or self.model_registry.get_default() is None:
            self.model_registry.set_default(entry.id)
        self.config.persist_model_registry(self.model_registry)
        self.rebuild_llm()
        return entry

    def update_model(
        self, entry_id: str, payload: dict, api_key: Optional[str] = None
    ) -> ModelEntry:
        entry = self.model_registry.update_model(
            entry_id,
            display_name=str(payload["display_name"]),
            kind=ModelKind(payload["kind"]),
            model_id=str(payload["model_id"]),
            endpoint_url=payload.get("endpoint_url"),
            api_key_env_var=payload.get("api_key_env_var"),
            notes=str(payload.get("notes", "")),
        )
        if api_key:
            store_model_api_key(self.sysiface, entry.id, api_key)
        if payload.get("is_default"):
            self.model_registry.set_default(entry.id)
        self.config.persist_model_registry(self.model_registry)
        self.rebuild_llm()
        return entry

    def set_default_model(self, entry_id: str) -> None:
        self.model_registry.set_default(entry_id)
        self.config.persist_model_registry(self.model_registry)
        self.rebuild_llm()

    def remove_model(self, entry_id: str) -> bool:
        removed = self.model_registry.remove_model(entry_id)
        if removed:
            self.sysiface.delete_credential(f"model:{entry_id}", "api_key")
            self.config.persist_model_registry(self.model_registry)
            self.rebuild_llm()
        return removed

    def model_state(self) -> dict:
        default = self.model_registry.get_default()
        return {
            "default_id": default.id if default else None,
            "models": [
                {**asdict(entry), "kind": entry.kind.value} for entry in self.model_registry.list_models()
            ],
            "last_used": self.llm.last_used,
            "providers": self.llm.provider_states(),
        }

    def state(self) -> dict:
        vm = psutil.virtual_memory()
        battery = self.hardware.snapshot.battery
        voice = self._plugin_instances.get("voice")
        camera = self._plugin_instances.get("camera_vision")
        usage = self.llm.usage
        vision_usage = self.vision_llm.usage
        return {
            "app": {"name": "Aether", "version": "0.1.0"},
            "privacy": self.config.get("privacy"),
            "tier": self.hardware.tier.value,
            "tier_config": asdict(self.hardware.config),
            "resources": {
                "cpu_percent": psutil.cpu_percent(interval=None),
                "ram_percent": vm.percent,
                "ram_available_mb": round(vm.available / 1024 / 1024),
                "battery_percent": battery.percent if battery.present else None,
                "plugged_in": battery.plugged_in if battery.present else None,
            },
            "plugins": self.registry.plugin_states(),
            "models": self.model_state(),
            "voice": {
                "is_listening": voice.is_listening if isinstance(voice, VoicePlugin) else False,
                "is_speaking": voice.is_speaking if isinstance(voice, VoicePlugin) else False,
                "speak_responses": bool(self.config.get("voice.speak_responses", False)),
                "global_hotkey": self.config.get("voice.global_hotkey", "ctrl+alt+space"),
                "wakeword_model_path": self.config.get("voice.wakeword_model_path"),
                "elevenlabs_voice_id": self.config.get("voice.elevenlabs_voice_id", ""),
                "active_id": voice.registry.active.id if isinstance(voice, VoicePlugin) else None,
                "profiles": [asdict(item) for item in voice.registry.list_voices()]
                if isinstance(voice, VoicePlugin)
                else [],
            },
            "camera_active": camera.is_camera_active if isinstance(camera, CameraVisionPlugin) else False,
            "subagents": list(self._active_subagents.values()),
            "usage": {
                "input_tokens": usage.input_tokens + vision_usage.input_tokens,
                "output_tokens": usage.output_tokens + vision_usage.output_tokens,
                "estimated_cost_usd": usage.estimated_cost_usd + vision_usage.estimated_cost_usd,
            },
            "events": self.events.history()[-50:],
            "confirmations": self.confirmations.pending(),
            "vision": {"local_model": self.config.get("vision.local_model", "llava:7b")},
            "ui": self.config.get("ui"),
            "persona": self.config.get("persona"),
        }

    def set_setting(self, path: str, value) -> None:
        allowed = {
            "ui.high_contrast",
            "ui.reduced_motion",
            "voice.speak_responses",
            "voice.global_hotkey",
            "voice.wakeword_model_path",
            "voice.elevenlabs_voice_id",
            "vision.local_model",
            "performance.tier_override",
            "persona.assistant_name",
            "persona.user_name",
            "persona.style",
            "persona.languages",
        }
        if path not in allowed:
            raise PermissionError("This setting cannot be changed through the local API.")
        self.config.set(path, value)
        if path == "performance.tier_override":
            self.hardware.set_manual_tier(PerformanceTier(value) if value else None)
            with self._chat_lock:
                self.rebuild_llm()
                if self._resource_throttled:
                    self._throttled_plugins.update(self.registry.disable_heavy_plugins())
        elif path == "voice.global_hotkey":
            voice = self._plugin_instances.get("voice")
            if isinstance(voice, VoicePlugin):
                was_enabled = self.registry.is_enabled("voice")
                if was_enabled:
                    self.registry.disable("voice")
                voice.global_hotkey = str(value)
                if was_enabled:
                    self.registry.enable("voice")
        elif path in {"voice.wakeword_model_path", "voice.elevenlabs_voice_id"}:
            with self._chat_lock:
                self.rebuild_llm()
                if self._resource_throttled:
                    self._throttled_plugins.update(self.registry.disable_heavy_plugins())
        elif path == "vision.local_model":
            with self._chat_lock:
                self.rebuild_llm()
                if self._resource_throttled:
                    self._throttled_plugins.update(self.registry.disable_heavy_plugins())
        self.events.emit("setting_changed", {"path": path, "value": value})

    def export_diagnostics(self, destination: Optional[Path] = None) -> Path:
        destination = destination or (
            self.data_dir / f"diagnostics-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"
        )
        state = self.state()
        state["models"]["models"] = [
            {key: value for key, value in model.items() if key not in {"api_key", "secret"}}
            for model in state["models"]["models"]
        ]
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("state.json", json.dumps(state, indent=2, default=str))
            archive.writestr("events.json", json.dumps(self.events.history(), indent=2, default=str))
            archive.writestr(
                "about.txt",
                "Aether diagnostics bundle generated manually. No data was uploaded automatically.\n",
            )
        return destination

    def close(self) -> None:
        self.hardware.stop_monitoring()
        for state in self.registry.plugin_states():
            if state["enabled"]:
                self.registry.disable(state["name"])
