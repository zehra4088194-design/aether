"""Opt-in proactive pattern detection that can suggest but never auto-act."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Optional

from aether.agent_loop import AetherPlugin, ToolResult, ToolSpec
from aether.hardware_probe import TierConfig
from aether.llm_provider import ManagedLLM


class ProactiveEnginePlugin(AetherPlugin):
    name = "proactive_engine"
    description = "Detect repeated patterns and offer transparent suggestions without acting."
    resource_cost = "medium"

    def __init__(self, tier_config: TierConfig, llm: ManagedLLM, enabled: Optional[bool] = None) -> None:
        self.tier_config = tier_config
        self.llm = llm
        self.enabled_by_preference = (
            tier_config.proactive_mode_default_on if enabled is None else bool(enabled)
        )

    def _detect(self, args: dict) -> ToolResult:
        events = args.get("events", [])
        if not self.enabled_by_preference:
            return ToolResult(ok=False, output="Proactive suggestions are disabled in Settings.")
        if not isinstance(events, list):
            return ToolResult(ok=False, output="events must be a list.")
        normalized = [str(event).strip() for event in events if str(event).strip()]
        repeated = [(event, count) for event, count in Counter(normalized).most_common() if count >= 2]
        suggestions = [
            {
                "title": f"Automate repeated activity ({count}×)",
                "suggestion": f"Would you like Aether to help streamline: {event}?",
                "requires_user_action": True,
            }
            for event, count in repeated[:5]
        ]
        return ToolResult(
            ok=True,
            output=(
                "\n".join(item["suggestion"] for item in suggestions)
                if suggestions
                else "No strong repeated pattern was found."
            ),
            data={"suggestions": suggestions, "generated_at": datetime.utcnow().isoformat() + "Z"},
        )

    def get_tools(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="suggest_from_activity_patterns",
                description="Analyze supplied activity labels and return suggestions only; never execute them.",
                input_schema={
                    "type": "object",
                    "properties": {"events": {"type": "array", "items": {"type": "string"}}},
                    "required": ["events"],
                },
                handler=self._detect,
                requires_confirmation=False,
                resource_cost="medium",
            )
        ]
