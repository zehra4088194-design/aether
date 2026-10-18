from __future__ import annotations

from collections import deque

from aether.agent_loop import AetherPlugin, AgentLoop, AgentStep, PluginRegistry, ToolResult, ToolSpec
from aether.llm_provider import LLMResponse, ToolCall


class ScriptedLLM:
    def __init__(self, *responses: LLMResponse) -> None:
        self.responses = deque(responses)

    def chat(self, **_kwargs) -> LLMResponse:
        return self.responses.popleft()


class RecordingPlugin(AetherPlugin):
    name = "recording"

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def handler(self, args: dict) -> ToolResult:
        self.calls.append(args)
        return ToolResult(ok=True, output=f"handled {args['value']}")

    def get_tools(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="record_value",
                description="Record a value for a routing test.",
                input_schema={
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                },
                handler=self.handler,
                requires_confirmation=True,
            )
        ]


def make_registry() -> tuple[PluginRegistry, RecordingPlugin]:
    registry = PluginRegistry()
    plugin = RecordingPlugin()
    registry.register(plugin)
    return registry, plugin


def test_routes_tool_call_and_returns_followup_answer() -> None:
    registry, plugin = make_registry()
    steps: list[AgentStep] = []
    llm = ScriptedLLM(
        LLMResponse(tool_calls=[ToolCall(id="1", name="record_value", arguments={"value": "ok"})]),
        LLMResponse(text="Finished."),
    )
    loop = AgentLoop(llm=llm, registry=registry, confirm_callback=lambda *_: True, on_step=steps.append)

    assert loop.run("record this") == "Finished."
    assert plugin.calls == [{"value": "ok"}]
    assert [step.kind for step in steps] == ["tool_call", "tool_result", "assistant_text"]


def test_confirmation_decline_prevents_handler_execution() -> None:
    registry, plugin = make_registry()
    steps: list[AgentStep] = []
    llm = ScriptedLLM(
        LLMResponse(tool_calls=[ToolCall(id="1", name="record_value", arguments={"value": "blocked"})]),
        LLMResponse(text="Cancelled."),
    )
    loop = AgentLoop(llm=llm, registry=registry, confirm_callback=lambda *_: False, on_step=steps.append)

    assert loop.run("record this") == "Cancelled."
    assert plugin.calls == []
    assert any(step.kind == "confirmation_needed" for step in steps)


def test_missing_confirmation_callback_denies_by_default() -> None:
    registry, plugin = make_registry()
    llm = ScriptedLLM(
        LLMResponse(tool_calls=[ToolCall(id="1", name="record_value", arguments={"value": "blocked"})]),
        LLMResponse(text="Not executed."),
    )

    assert AgentLoop(llm=llm, registry=registry).run("record this") == "Not executed."
    assert plugin.calls == []


def test_unknown_tool_is_reported_without_crashing() -> None:
    registry, _plugin = make_registry()
    steps: list[AgentStep] = []
    llm = ScriptedLLM(
        LLMResponse(tool_calls=[ToolCall(id="x", name="missing_tool", arguments={})]),
        LLMResponse(text="I could not run that tool."),
    )
    loop = AgentLoop(llm=llm, registry=registry, on_step=steps.append)

    assert loop.run("use a missing tool") == "I could not run that tool."
    assert any(step.content == "Unknown tool: missing_tool" for step in steps)
