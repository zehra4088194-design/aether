"""
Aether - Sub-Agent Orchestrator
================================
Module 8

Purpose
-------
Some requests bundle two or more *independent* pieces of work, e.g.
"organize my Downloads, tell me what's on my second screen, and check
disk space." Running these one-by-one inside a single ReAct loop
(agent_loop.py) works, but it's slower than it needs to be and the
transcript gets noisy with unrelated tool calls interleaved together.

This module adds a thin planning layer in front of the normal AgentLoop:

    user message
        -> plan(): ask the model "is this 1 task or several independent ones?"
        -> if several: spawn one short-lived sub-agent per task, run them
           concurrently (each with its own AgentLoop + its own tool subset)
        -> merge each sub-agent's result into a single reply

The user only ever talks to Aether -- sub-agents are invisible workers.
The UI (Module 6, ui_reference_design.html "Sub-Agents" satellites) may
surface them as small orbiting nodes around the core so the user can see
work happening in parallel, but sub-agents never address the user directly
and never ask their own confirmation questions out of band -- confirmation
still routes through the same `confirm_callback` as the main loop.

Safety
------
- A broken or unparsable plan NEVER blocks the request: plan() falls back
  to a single subtask (the original message) on any failure.
- Sub-agents can be restricted to a tool subset (`SubTask.allowed_tools`)
  so, e.g., a "describe my screen" sub-agent isn't handed file-deletion
  tools it has no reason to touch.
- `max_parallel` caps concurrent sub-agents (default 4) so a pathological
  plan can't spawn unbounded threads / LLM calls on Lite-tier hardware.
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Callable, Optional

from aether.llm_provider import ManagedLLM, LLMResponse
from aether.agent_loop import AgentLoop, AgentConfig, PluginRegistry, AgentStep

logger = logging.getLogger("aether.sub_agent_orchestrator")

MAX_PARALLEL_SUBAGENTS = 4

PLANNER_SYSTEM_PROMPT = (
    "You split a user's request into independent subtasks, but ONLY when "
    "it genuinely contains 2 or more separate pieces of work that do not "
    "depend on each other's results. Example that SHOULD split: 'organize "
    "my downloads, tell me what's on my second screen, and check disk "
    "space' -> 3 subtasks. Example that should NOT split: 'find my resume "
    "PDF and rename it' -> 1 subtask (rename depends on find). If in doubt, "
    "return exactly one subtask covering the whole message. "
    'Respond ONLY with JSON, no prose: {"subtasks": [{"label": "short '
    'title", "instruction": "self-contained instruction for this subtask"}]}'
)


@dataclass
class SubTask:
    id: str
    label: str  # short human-readable title shown in the UI, e.g. "Organize Downloads"
    instruction: str  # self-contained instruction handed to the sub-agent
    allowed_tools: Optional[list[str]] = None  # None = inherit all active tools


@dataclass
class SubAgentStatus:
    """Live status snapshot pushed to the UI for one sub-agent node."""

    id: str
    label: str
    state: str  # "queued" | "running" | "done" | "error"
    detail: str = ""
    started_at: Optional[float] = None
    finished_at: Optional[float] = None


class _FilteredRegistry:
    """Read-only view of a PluginRegistry exposing only a chosen subset of
    tools -- keeps, e.g., a file-organizing sub-agent from being offered
    camera/vision tools it doesn't need for its task."""

    def __init__(self, base: PluginRegistry, allowed_tool_names: list[str]) -> None:
        self._base = base
        self._allowed = set(allowed_tool_names)

    def active_tools(self) -> dict:
        return {k: v for k, v in self._base.active_tools().items() if k in self._allowed}

    def as_tool_definitions(self) -> list:
        return [d for d in self._base.as_tool_definitions() if d.name in self._allowed]


class SubAgentOrchestrator:
    """Decomposes a request, runs independent subtasks in parallel via
    disposable AgentLoop instances, and merges the results back into one
    reply. Drop-in alongside AgentLoop -- same constructor shape, plus
    `on_status` for the UI's Sub-Agents panel.

    `on_status(SubAgentStatus)` fires on every state change for a sub-agent.
    `on_step(sub_agent_id, AgentStep)` forwards each sub-agent's normal
    agent_loop steps, tagged with which sub-agent produced them, so the
    Live Activity log can group them.
    """

    def __init__(
        self,
        llm: ManagedLLM,
        registry: PluginRegistry,
        confirm_callback: Optional[Callable[[str, str, dict], bool]] = None,
        on_status: Optional[Callable[[SubAgentStatus], None]] = None,
        on_step: Optional[Callable[[str, AgentStep], None]] = None,
        max_parallel: int = MAX_PARALLEL_SUBAGENTS,
        system_prompt: Optional[str] = None,
    ) -> None:
        self.llm = llm
        self.registry = registry
        self.confirm_callback = confirm_callback or (lambda *_: True)
        self.on_status = on_status or (lambda s: None)
        self.on_step = on_step or (lambda sub_id, step: None)
        self.max_parallel = max_parallel
        self.system_prompt = system_prompt

    # ------------------------------------------------------------------ #
    # Planning
    # ------------------------------------------------------------------ #

    def plan(self, user_message: str) -> list[SubTask]:
        """Ask the model whether this request should be split. Falls back
        to a single subtask (the whole message) on any parsing failure --
        a broken planner must never block the user's request."""
        try:
            response: LLMResponse = self.llm.chat(
                messages=[{"role": "user", "content": user_message}],
                tools=[],
                system=PLANNER_SYSTEM_PROMPT,
            )
            data = json.loads(response.text)
            raw_tasks = data.get("subtasks") or []
            if not raw_tasks:
                raise ValueError("planner returned no subtasks")
            return [
                SubTask(id=str(uuid.uuid4())[:8], label=t["label"], instruction=t["instruction"])
                for t in raw_tasks
            ]
        except Exception as e:
            logger.warning("Planner failed (%s) -- falling back to a single task.", e)
            return [SubTask(id=str(uuid.uuid4())[:8], label="Task", instruction=user_message)]

    # ------------------------------------------------------------------ #
    # Execution
    # ------------------------------------------------------------------ #

    def run(self, user_message: str, history: Optional[list[dict]] = None) -> str:
        subtasks = self.plan(user_message)

        if len(subtasks) == 1:
            # Nothing to parallelize -- behave exactly like a plain AgentLoop.
            only = subtasks[0]
            loop = AgentLoop(
                llm=self.llm,
                registry=self.registry,
                confirm_callback=self.confirm_callback,
                on_step=lambda step: self.on_step(only.id, step),
                config=AgentConfig(system_prompt=self.system_prompt) if self.system_prompt else None,
            )
            return loop.run(only.instruction, history=history)

        logger.info("Splitting request into %d parallel sub-agents.", len(subtasks))
        for task in subtasks:
            self.on_status(SubAgentStatus(id=task.id, label=task.label, state="queued"))

        results: dict[str, str] = {}
        workers = min(self.max_parallel, len(subtasks))
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(self._run_subtask, task, history): task for task in subtasks}
            for future in concurrent.futures.as_completed(futures):
                task = futures[future]
                try:
                    results[task.id] = future.result()
                except Exception as e:  # a single sub-agent's crash must not sink the others
                    results[task.id] = f"(this part failed: {e})"
                    self.on_status(
                        SubAgentStatus(id=task.id, label=task.label, state="error", detail=str(e))
                    )

        return self._merge(subtasks, results)

    def _run_subtask(self, task: SubTask, history: Optional[list[dict]]) -> str:
        self.on_status(
            SubAgentStatus(id=task.id, label=task.label, state="running", started_at=time.time())
        )

        registry = self.registry
        if task.allowed_tools is not None:
            registry = _FilteredRegistry(self.registry, task.allowed_tools)

        loop = AgentLoop(
            llm=self.llm,
            registry=registry,
            confirm_callback=self.confirm_callback,
            on_step=lambda step: self.on_step(task.id, step),
            config=AgentConfig(
                system_prompt=(
                    (self.system_prompt + " ") if self.system_prompt else ""
                ) + (
                    "You are a focused Aether sub-agent working on exactly one "
                    f"part of a larger request: {task.instruction}. Stay strictly "
                    "within this task. Be concise -- your output will be merged "
                    "with other sub-agents' results before the user sees it, so "
                    "skip pleasantries and lead with the outcome."
                )
            ),
        )
        output = loop.run(task.instruction, history=history)
        self.on_status(SubAgentStatus(id=task.id, label=task.label, state="done", finished_at=time.time()))
        return output

    def _merge(self, subtasks: list[SubTask], results: dict[str, str]) -> str:
        """Deterministic, inspectable merge -- one labeled section per
        subtask. (A v2 could make one extra LLM call to weave these into a
        single flowing reply; kept simple for v1 so failures are easy to
        trace back to the sub-agent that caused them.)"""
        parts = [f"**{task.label}:** {results.get(task.id, '(no result)')}" for task in subtasks]
        return "\n\n".join(parts)
