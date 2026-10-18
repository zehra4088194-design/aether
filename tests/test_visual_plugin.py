from __future__ import annotations

from aether.llm_provider import LLMResponse
from aether.plugins.visual_intelligence_plugin import VisualIntelligencePlugin


class DiagramLLM:
    def chat(self, **_kwargs) -> LLMResponse:
        return LLMResponse(text="```mermaid\nerDiagram\n  USER ||--o{ TASK : owns\n```")


def test_visual_tool_exposes_and_generates_er_diagrams() -> None:
    plugin = VisualIntelligencePlugin(DiagramLLM())  # type: ignore[arg-type]
    tool = next(item for item in plugin.get_tools() if item.name == "create_visual_diagram")

    assert "er" in tool.input_schema["properties"]["kind"]["enum"]
    result = tool.handler({"kind": "er", "prompt": "Users own tasks"})
    assert result.ok
    assert result.data["kind"] == "er"
    assert result.output.startswith("erDiagram")
