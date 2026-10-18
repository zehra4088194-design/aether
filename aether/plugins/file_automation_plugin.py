"""
Aether - File Automation Plugin
===================================
Module 5 of 5

Purpose
-------
Reference / starter plugin demonstrating the AetherPlugin contract from
agent_loop.py. Exposes file search, move/rename, batch-organize, and
open-file as agent tools, backed by SystemInterface (Module 1) so no
OS-specific code lives here.

This file is the template Codex (or any future contributor) should copy
when adding new plugins (browser_automation_plugin.py, voice_plugin.py,
coding_agent_plugin.py, trading_plugin.py, etc.) — same shape every time:
  1. subclass AetherPlugin
  2. set name / description / resource_cost
  3. implement get_tools() returning ToolSpec objects
  4. mark any destructive action requires_confirmation=True
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

from aether.agent_loop import AetherPlugin, ToolSpec, ToolResult
from aether.system_interface import SystemInterface


class FileAutomationPlugin(AetherPlugin):
    name = "file_automation"
    description = "Search, open, move, rename, and batch-organize files on the local machine."
    resource_cost = "light"

    def __init__(self, sysiface: SystemInterface, default_root: Optional[Path] = None):
        self.sysiface = sysiface
        self.default_root = default_root or Path.home()

    # ---- lifecycle -----------------------------------------------------------------
    def on_enable(self) -> None:
        pass  # nothing to warm up for this plugin

    def on_disable(self) -> None:
        pass

    # ---- tool handlers ----------------------------------------------------------------
    def _search_files(self, args: dict) -> ToolResult:
        root = Path(args.get("root", str(self.default_root))).expanduser()
        pattern = args.get("pattern", "*")
        max_results = int(args.get("max_results", 50))

        if not root.exists():
            return ToolResult(ok=False, output=f"Path does not exist: {root}")

        matches = self.sysiface.search_files(root, pattern, max_results=max_results)
        preview = "\n".join(str(p) for p in matches[:20])
        summary = f"Found {len(matches)} file(s) matching '{pattern}' under {root}.\n{preview}"
        return ToolResult(ok=True, output=summary, data={"matches": [str(p) for p in matches]})

    def _open_file(self, args: dict) -> ToolResult:
        path = Path(args["path"]).expanduser()
        if not path.exists():
            return ToolResult(ok=False, output=f"File not found: {path}")
        self.sysiface.open_file(path)
        return ToolResult(ok=True, output=f"Opened {path}")

    def _move_file(self, args: dict) -> ToolResult:
        src = Path(args["source"]).expanduser()
        dst = Path(args["destination"]).expanduser()
        if not src.exists():
            return ToolResult(ok=False, output=f"Source does not exist: {src}")
        new_path = self.sysiface.move_file(src, dst)
        return ToolResult(ok=True, output=f"Moved {src} -> {new_path}")

    def _batch_organize(self, args: dict) -> ToolResult:
        """Organize a folder's files into subfolders by extension.
        e.g. all .pdf -> /PDFs, all .jpg/.png -> /Images"""
        root = Path(args["root"]).expanduser()
        if not root.exists() or not root.is_dir():
            return ToolResult(ok=False, output=f"Not a valid directory: {root}")

        category_map = {
            ".pdf": "PDFs", ".doc": "Documents", ".docx": "Documents",
            ".jpg": "Images", ".jpeg": "Images", ".png": "Images", ".gif": "Images",
            ".mp4": "Videos", ".mov": "Videos", ".mkv": "Videos",
            ".mp3": "Audio", ".wav": "Audio",
            ".zip": "Archives", ".rar": "Archives", ".7z": "Archives",
        }

        moved = 0
        for item in root.iterdir():
            if item.is_file():
                folder_name = category_map.get(item.suffix.lower())
                if folder_name:
                    dest_dir = root / folder_name
                    dest_dir.mkdir(exist_ok=True)
                    self.sysiface.move_file(item, dest_dir / item.name)
                    moved += 1

        return ToolResult(ok=True, output=f"Organized {moved} file(s) in {root} by type.")

    # ---- tool registration --------------------------------------------------------------
    def get_tools(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="search_files",
                description="Search for files under a root directory matching a glob pattern (e.g. '*.pdf').",
                input_schema={
                    "type": "object",
                    "properties": {
                        "root": {"type": "string", "description": "Directory to search under."},
                        "pattern": {"type": "string", "description": "Glob pattern, e.g. '*.docx'."},
                        "max_results": {"type": "integer", "description": "Cap on results returned."},
                    },
                    "required": ["pattern"],
                },
                handler=self._search_files,
                requires_confirmation=False,
                resource_cost="light",
            ),
            ToolSpec(
                name="open_file",
                description="Open a file with its default associated application.",
                input_schema={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
                handler=self._open_file,
                requires_confirmation=False,
                resource_cost="light",
            ),
            ToolSpec(
                name="move_file",
                description="Move or rename a file from one path to another.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "source": {"type": "string"},
                        "destination": {"type": "string"},
                    },
                    "required": ["source", "destination"],
                },
                handler=self._move_file,
                requires_confirmation=True,  # modifies the filesystem -> needs user OK
                resource_cost="light",
            ),
            ToolSpec(
                name="batch_organize_by_type",
                description=(
                    "Organize all files in a directory into subfolders by file type "
                    "(PDFs, Images, Videos, Audio, Documents, Archives)."
                ),
                input_schema={
                    "type": "object",
                    "properties": {"root": {"type": "string"}},
                    "required": ["root"],
                },
                handler=self._batch_organize,
                requires_confirmation=True,  # moves many files at once -> needs user OK
                resource_cost="medium",
            ),
        ]
