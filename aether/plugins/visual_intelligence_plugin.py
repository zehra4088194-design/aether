"""Generate inspectable Mermaid diagrams and chart specifications through ManagedLLM."""

from __future__ import annotations

import json
import re

from aether.agent_loop import AetherPlugin, ToolResult, ToolSpec
from aether.llm_provider import ManagedLLM


class VisualIntelligencePlugin(AetherPlugin):
    name = "visual_intelligence"
    description = "Turn ideas and data into diagrams, mind maps, timelines, and charts."
    resource_cost = "medium"

    def __init__(self, llm: ManagedLLM) -> None:
        self.llm = llm

    def _diagram(self, args: dict) -> ToolResult:
        kind = str(args.get("kind", "flowchart"))
        response = self.llm.chat(
            messages=[{"role": "user", "content": str(args["prompt"])}],
            tools=[],
            system=(
                f"Create a clear {kind} as Mermaid source. Return only a Mermaid code block. "
                "Keep labels short, escape punctuation safely, and use no external resources."
            ),
        )
        source = response.text.strip()
        match = re.search(r"```(?:mermaid)?\s*(.*?)```", source, re.DOTALL | re.IGNORECASE)
        if match:
            source = match.group(1).strip()
        return ToolResult(
            ok=bool(source),
            output=source,
            data={"visual_type": "mermaid", "kind": kind, "source": source},
        )

    def _chart(self, args: dict) -> ToolResult:
        data = args.get("data")
        chart = {
            "type": args.get("chart_type", "bar"),
            "title": args.get("title", "Aether chart"),
            "data": data,
        }
        return ToolResult(ok=True, output=json.dumps(chart), data={"visual_type": "chart", **chart})

    def get_tools(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                "create_visual_diagram",
                "Create Mermaid source for a flowchart, mind map, sequence, ER, class, Gantt, timeline, or pie visual.",
                {
                    "type": "object",
                    "properties": {
                        "prompt": {"type": "string"},
                        "kind": {
                            "type": "string",
                            "enum": ["flowchart", "mindmap", "sequence", "er", "class", "gantt", "timeline", "pie"],
                        },
                    },
                    "required": ["prompt"],
                },
                self._diagram,
                requires_confirmation=False,
            ),
            ToolSpec(
                "create_data_chart",
                "Create an inspectable chart specification from supplied labels and values.",
                {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "chart_type": {"type": "string", "enum": ["bar", "line", "pie", "radar"]},
                        "data": {"type": "object"},
                    },
                    "required": ["data"],
                },
                self._chart,
                requires_confirmation=False,
            ),
        ]
