"""
Aether - Agent Loop & Plugin Orchestrator
=============================================
Module 4 of 5

Purpose
-------
Implements the ReAct-style loop (reason -> select tool -> act -> observe
-> respond) that lets Aether chain multiple actions to complete a
multi-step request. Also defines the `AetherPlugin` base class and a
`PluginRegistry` that plugins register themselves with — this is the
contract every plugin (voice, browser, file automation, coding agent,
trading, etc.) must follow so the agent loop can discover and call their
functions as "tools" without any hardcoded per-plugin logic.

Safety
------
Per Section 12 of the brief: any tool marked `requires_confirmation=True`
will NOT execute until `confirm_callback(action_description)` returns True.
This is enforced here, centrally, so no individual plugin can bypass it.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, Optional

from aether.llm_provider import ManagedLLM, ToolDefinition, ToolCall, LLMResponse

logger = logging.getLogger("aether.agent_loop")


# --------------------------------------------------------------------------- #
# Plugin contract
# --------------------------------------------------------------------------- #

@dataclass
class ToolSpec:
    """One callable function a plugin exposes to the agent."""

    name: str
    description: str
    input_schema: dict
    handler: Callable[[dict], "ToolResult"]
    requires_confirmation: bool = False
    resource_cost: str = "light"  # "light" | "medium" | "heavy" -- read by adaptive engine


@dataclass
class ToolResult:
    ok: bool
    output: str
    data: Optional[dict] = None


class AetherPlugin:
    """Base class every plugin subclasses. See file_automation_plugin.py
    (Module 5) for a full reference implementation."""

    name: str = "unnamed_plugin"
    description: str = ""
    resource_cost: str = "light"  # default cost tier if the plugin has no finer-grained tools

    def get_tools(self) -> list[ToolSpec]:
        raise NotImplementedError

    def on_enable(self) -> None:
        """Called when the plugin is activated (e.g. after tier detection
        determines it should run). Override to open connections, load
        models, etc."""

    def on_disable(self) -> None:
        """Called when low-resource mode or the user disables this plugin.
        Override to release resources cleanly."""


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #

class PluginRegistry:
    def __init__(self) -> None:
        self._plugins: dict[str, AetherPlugin] = {}
        self._enabled: set[str] = set()

    def register(self, plugin: AetherPlugin, enabled: bool = True) -> None:
        self._plugins[plugin.name] = plugin
        if enabled:
            self.enable(plugin.name)

    def enable(self, name: str) -> None:
        plugin = self._plugins[name]
        if name not in self._enabled:
            plugin.on_enable()
            self._enabled.add(name)

    def disable(self, name: str) -> None:
        plugin = self._plugins[name]
        if name in self._enabled:
            plugin.on_disable()
            self._enabled.discard(name)

    def disable_heavy_plugins(self) -> list[str]:
        """Called by the adaptive engine on Lite tier / under system load.
        Returns the names of plugins that were disabled."""
        disabled = []
        for name, plugin in self._plugins.items():
            if plugin.resource_cost == "heavy" and name in self._enabled:
                self.disable(name)
                disabled.append(name)
        return disabled

    def active_tools(self) -> dict[str, ToolSpec]:
        """Flat map of tool_name -> ToolSpec across all enabled plugins."""
        tools: dict[str, ToolSpec] = {}
        for name in self._enabled:
            for tool in self._plugins[name].get_tools():
                tools[tool.name] = tool
        return tools

    def as_tool_definitions(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(name=t.name, description=t.description, input_schema=t.input_schema)
            for t in self.active_tools().values()
        ]

    def plugin_states(self) -> list[dict]:
        return [
            {
                "name": name,
                "description": plugin.description,
                "resource_cost": plugin.resource_cost,
                "enabled": name in self._enabled,
                "tools": [tool.name for tool in plugin.get_tools()],
            }
            for name, plugin in sorted(self._plugins.items())
        ]

    def is_enabled(self, name: str) -> bool:
        return name in self._enabled

    def get_plugin(self, name: str) -> Optional[AetherPlugin]:
        return self._plugins.get(name)


# --------------------------------------------------------------------------- #
# Agent loop
# --------------------------------------------------------------------------- #

@dataclass
class AgentStep:
    kind: str  # "assistant_text" | "tool_call" | "tool_result" | "confirmation_needed"
    content: str
    data: Optional[dict] = None


@dataclass
class AgentConfig:
    max_iterations: int = 8
    system_prompt: str = (
        "You are Aether, a helpful, transparent AI desktop assistant. "
        "Use the available tools to complete the user's request. "
        "Narrate briefly what you are about to do before acting. "
        "Never claim an action succeeded unless a tool result confirms it."
    )


class AgentLoop:
    """The ReAct engine: reason -> select tool -> act -> observe -> respond.

    `confirm_callback` is called (name, description, arguments) -> bool
    for any tool flagged `requires_confirmation=True`. The UI layer wires
    this to an actual confirmation dialog / toast.

    `on_step` is an optional callback invoked after every AgentStep, used
    to populate the live activity log in the UI (full transparency
    requirement from Section 12).
    """

    def __init__(
        self,
        llm: ManagedLLM,
        registry: PluginRegistry,
        confirm_callback: Optional[Callable[[str, str, dict], bool]] = None,
        on_step: Optional[Callable[[AgentStep], None]] = None,
        config: Optional[AgentConfig] = None,
    ):
        self.llm = llm
        self.registry = registry
        # Safe-by-default: a headless/miswired client must never silently
        # approve a destructive or external action.
        self.confirm_callback = confirm_callback or (lambda *_: False)
        self.on_step = on_step or (lambda step: None)
        self.config = config or AgentConfig()

    def _emit(self, step: AgentStep) -> None:
        logger.info("[%s] %s", step.kind, step.content)
        self.on_step(step)

    def run(self, user_message: str, history: Optional[list[dict]] = None) -> str:
        """Runs the full ReAct loop for one user turn. `history` is the
        prior conversation (list of {"role": ..., "content": ...} dicts);
        pass in relevant retrieved memories here too (Module 7 concern)."""

        messages = list(history or [])
        messages.append({"role": "user", "content": user_message})

        tools = self.registry.as_tool_definitions()
        final_text = ""

        for iteration in range(self.config.max_iterations):
            response: LLMResponse = self.llm.chat(
                messages=messages,
                tools=tools,
                system=self.config.system_prompt,
            )

            if response.text:
                self._emit(AgentStep(kind="assistant_text", content=response.text))
                final_text = response.text

            if not response.tool_calls:
                # No tool calls -> the agent is done reasoning, return final answer.
                break

            # Record the assistant turn (with its tool_use requests) before executing.
            messages.append({"role": "assistant", "content": response.text or ""})

            tool_results_content = []
            for call in response.tool_calls:
                result = self._execute_tool(call)
                tool_results_content.append(
                    {"tool_call_id": call.id, "name": call.name, "content": result.output}
                )

            # Feed tool results back in as the next "observation" for the model.
            messages.append(
                {
                    "role": "user",
                    "content": json.dumps({"tool_results": tool_results_content}),
                }
            )

        return final_text

    def _execute_tool(self, call: ToolCall) -> ToolResult:
        tool = self.registry.active_tools().get(call.name)
        if tool is None:
            result = ToolResult(ok=False, output=f"Unknown tool: {call.name}")
            self._emit(AgentStep(kind="tool_result", content=result.output, data={"tool": call.name}))
            return result

        self._emit(
            AgentStep(
                kind="tool_call",
                content=f"{tool.name}({json.dumps(call.arguments)})",
                data={"tool": call.name, "args": call.arguments},
            )
        )

        if tool.requires_confirmation:
            approved = self.confirm_callback(tool.name, tool.description, call.arguments)
            if not approved:
                result = ToolResult(ok=False, output="User declined to confirm this action.")
                self._emit(AgentStep(kind="confirmation_needed", content=result.output))
                return result

        start = time.time()
        try:
            result = tool.handler(call.arguments)
        except Exception as e:  # plugin bugs must never crash the whole loop
            result = ToolResult(ok=False, output=f"Tool '{tool.name}' raised an error: {e}")

        elapsed = round(time.time() - start, 2)
        self._emit(
            AgentStep(
                kind="tool_result",
                content=f"{tool.name} -> {result.output} ({elapsed}s)",
                data={"tool": tool.name, "ok": result.ok},
            )
        )
        return result

    def execute_tool(self, name: str, arguments: Optional[dict] = None) -> ToolResult:
        """Execute one registered tool through the same confirmation/audit seam.

        Settings and memory UI actions use this instead of calling plugin
        handlers directly, preserving the central safety guarantee even when
        no LLM turn is involved.
        """
        return self._execute_tool(
            ToolCall(id=str(uuid.uuid4()), name=name, arguments=arguments or {})
        )
