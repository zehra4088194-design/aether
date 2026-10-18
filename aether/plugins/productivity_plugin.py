"""Local notes/tasks plus explicitly confirmed headline retrieval."""

from __future__ import annotations

import json
import threading
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import psutil
import requests

from aether.agent_loop import AetherPlugin, ToolResult, ToolSpec


class ProductivityPlugin(AetherPlugin):
    name = "productivity"
    description = "Manage local notes and tasks, inspect system health, and fetch chosen news feeds."
    resource_cost = "light"

    def __init__(self, data_path: Path) -> None:
        self.data_path = data_path
        self._lock = threading.RLock()

    def _load(self) -> dict:
        with self._lock:
            if not self.data_path.exists():
                return {"notes": [], "tasks": []}
            try:
                data = json.loads(self.data_path.read_text(encoding="utf-8"))
                return data if isinstance(data, dict) else {"notes": [], "tasks": []}
            except (json.JSONDecodeError, OSError):
                return {"notes": [], "tasks": []}

    def _save(self, data: dict) -> None:
        with self._lock:
            self.data_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.data_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            temporary.replace(self.data_path)

    def _list_notes(self, _args: dict) -> ToolResult:
        notes = self._load().get("notes", [])
        return ToolResult(ok=True, output=f"{len(notes)} note(s).", data={"notes": notes})

    def _create_note(self, args: dict) -> ToolResult:
        data = self._load()
        note = {
            "id": str(uuid.uuid4()),
            "title": str(args["title"]),
            "content": str(args.get("content", "")),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        data.setdefault("notes", []).append(note)
        self._save(data)
        return ToolResult(ok=True, output=f"Created note '{note['title']}'.", data=note)

    def _list_tasks(self, _args: dict) -> ToolResult:
        tasks = self._load().get("tasks", [])
        return ToolResult(ok=True, output=f"{len(tasks)} task(s).", data={"tasks": tasks})

    def _create_task(self, args: dict) -> ToolResult:
        data = self._load()
        task = {
            "id": str(uuid.uuid4()),
            "title": str(args["title"]),
            "due": args.get("due"),
            "done": False,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        data.setdefault("tasks", []).append(task)
        self._save(data)
        return ToolResult(ok=True, output=f"Created task '{task['title']}'.", data=task)

    def _complete_task(self, args: dict) -> ToolResult:
        data = self._load()
        for task in data.get("tasks", []):
            if task.get("id") == args["id"]:
                task["done"] = True
                task["completed_at"] = datetime.now(timezone.utc).isoformat()
                self._save(data)
                return ToolResult(ok=True, output=f"Completed task '{task['title']}'.", data=task)
        return ToolResult(ok=False, output="Task not found.")

    def _system_health(self, _args: dict) -> ToolResult:
        battery = psutil.sensors_battery()
        data = {
            "cpu_percent": psutil.cpu_percent(interval=0.15),
            "ram_percent": psutil.virtual_memory().percent,
            "disk_percent": psutil.disk_usage(Path.home().anchor).percent,
            "battery_percent": battery.percent if battery else None,
            "plugged_in": battery.power_plugged if battery else None,
        }
        return ToolResult(ok=True, output=json.dumps(data), data=data)

    def _headlines(self, args: dict) -> ToolResult:
        feed_url = str(args.get("feed_url", "https://feeds.bbci.co.uk/news/rss.xml"))
        response = requests.get(feed_url, timeout=12, headers={"User-Agent": "Aether/1.0"})
        response.raise_for_status()
        root = ET.fromstring(response.content)
        items = []
        for item in root.findall(".//item")[: min(int(args.get("limit", 8)), 20)]:
            items.append(
                {
                    "title": (item.findtext("title") or "").strip(),
                    "link": (item.findtext("link") or "").strip(),
                    "published": (item.findtext("pubDate") or "").strip(),
                }
            )
        return ToolResult(
            ok=True,
            output="\n".join(f"- {item['title']}" for item in items),
            data={"headlines": items, "source": feed_url},
        )

    def get_tools(self) -> list[ToolSpec]:
        return [
            ToolSpec("list_notes", "List locally stored notes.", {"type": "object", "properties": {}}, self._list_notes),
            ToolSpec(
                "create_note",
                "Create a local note.",
                {
                    "type": "object",
                    "properties": {"title": {"type": "string"}, "content": {"type": "string"}},
                    "required": ["title"],
                },
                self._create_note,
                requires_confirmation=True,
            ),
            ToolSpec("list_tasks", "List locally stored tasks.", {"type": "object", "properties": {}}, self._list_tasks),
            ToolSpec(
                "create_task",
                "Create a local task/reminder.",
                {
                    "type": "object",
                    "properties": {"title": {"type": "string"}, "due": {"type": "string"}},
                    "required": ["title"],
                },
                self._create_task,
                requires_confirmation=True,
            ),
            ToolSpec(
                "complete_task",
                "Mark a local task complete.",
                {
                    "type": "object",
                    "properties": {"id": {"type": "string"}},
                    "required": ["id"],
                },
                self._complete_task,
                requires_confirmation=True,
            ),
            ToolSpec(
                "get_system_health",
                "Read current CPU, memory, disk, and battery status.",
                {"type": "object", "properties": {}},
                self._system_health,
            ),
            ToolSpec(
                "get_headlines",
                "Retrieve recent headlines from a user-selected RSS feed.",
                {
                    "type": "object",
                    "properties": {"feed_url": {"type": "string"}, "limit": {"type": "integer"}},
                },
                self._headlines,
                requires_confirmation=True,
            ),
        ]
