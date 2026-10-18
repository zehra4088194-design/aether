"""Confirmed coding tools with workspace boundaries and tier-aware sandboxes."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from aether.agent_loop import AetherPlugin, ToolResult, ToolSpec
from aether.hardware_probe import PerformanceTier, TierConfig
from aether.system_interface import SystemInterface


class CodingAgentPlugin(AetherPlugin):
    name = "coding_agent"
    description = "Inspect, edit, test, and version code inside an explicitly scoped workspace."
    resource_cost = "heavy"

    def __init__(
        self,
        sysiface: SystemInterface,
        tier_config: TierConfig,
        workspace_root: Path,
    ) -> None:
        self.sysiface = sysiface
        self.tier_config = tier_config
        self.workspace_root = workspace_root.expanduser().resolve()
        self.workspace_root.mkdir(parents=True, exist_ok=True)

    def available_backends(self) -> list[str]:
        backends = ["subprocess"]
        if self.tier_config.tier == PerformanceTier.PERFORMANCE:
            backends = self.sysiface.available_sandbox_backends()
        return backends

    def _safe_path(self, raw: str) -> Path:
        candidate = (self.workspace_root / raw).resolve() if not Path(raw).is_absolute() else Path(raw).resolve()
        if candidate != self.workspace_root and self.workspace_root not in candidate.parents:
            raise ValueError("Path is outside the configured coding workspace.")
        return candidate

    def _read_code(self, args: dict) -> ToolResult:
        path = self._safe_path(args["path"])
        if not path.is_file():
            return ToolResult(ok=False, output=f"File not found: {path}")
        text = path.read_text(encoding="utf-8", errors="replace")
        max_chars = min(int(args.get("max_chars", 30_000)), 100_000)
        return ToolResult(ok=True, output=text[:max_chars], data={"path": str(path), "truncated": len(text) > max_chars})

    def _syntax_check(self, path: Path, source: str) -> Optional[str]:
        language_by_suffix = {
            ".py": "python",
            ".js": "javascript",
            ".jsx": "javascript",
            ".ts": "typescript",
            ".tsx": "tsx",
            ".rs": "rust",
            ".go": "go",
            ".java": "java",
            ".c": "c",
            ".cpp": "cpp",
        }
        language = language_by_suffix.get(path.suffix.lower())
        if not language:
            return None
        try:
            from tree_sitter_language_pack import get_parser

            tree = get_parser(language).parse(source.encode("utf-8"))
            if tree.root_node.has_error:
                return f"tree-sitter found a syntax error in the proposed {language} source."
        except ImportError:
            return None
        return None

    def _apply_edit(self, args: dict) -> ToolResult:
        path = self._safe_path(args["path"])
        old_text = str(args.get("old_text", ""))
        new_text = str(args.get("new_text", ""))
        create = bool(args.get("create", False))
        if not path.exists() and not create:
            return ToolResult(ok=False, output=f"File not found: {path}")
        original = path.read_text(encoding="utf-8") if path.exists() else ""
        if old_text:
            occurrences = original.count(old_text)
            if occurrences != 1:
                return ToolResult(
                    ok=False,
                    output=f"Expected old_text exactly once, found {occurrences}; no file was changed.",
                )
            proposed = original.replace(old_text, new_text, 1)
        else:
            proposed = new_text
        syntax_error = self._syntax_check(path, proposed)
        if syntax_error:
            return ToolResult(ok=False, output=f"{syntax_error} No file was changed.")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(proposed, encoding="utf-8")
        return ToolResult(ok=True, output=f"Updated {path}", data={"path": str(path)})

    def _run_code(self, args: dict) -> ToolResult:
        command = args.get("command")
        if not isinstance(command, list) or not command or not all(isinstance(v, str) for v in command):
            return ToolResult(ok=False, output="command must be a non-empty string array.")
        backend = str(args.get("backend", "subprocess"))
        if backend not in self.available_backends():
            return ToolResult(
                ok=False,
                output=f"Sandbox backend '{backend}' is unavailable. Available: {self.available_backends()}",
            )
        result = self.sysiface.run_isolated_command(
            command,
            cwd=self.workspace_root,
            timeout_sec=min(max(int(args.get("timeout_sec", 30)), 1), 300),
            backend=backend,
        )
        combined = (result.stdout + ("\n" if result.stdout and result.stderr else "") + result.stderr).strip()
        return ToolResult(
            ok=result.returncode == 0,
            output=combined or f"Command exited with code {result.returncode}.",
            data={"returncode": result.returncode, "backend": backend},
        )

    def _git(self, args: dict, operation: str) -> ToolResult:
        if operation == "status":
            command = ["git", "status", "--short"]
        elif operation == "diff":
            command = ["git", "diff", "--", str(args.get("path", "."))]
        elif operation == "branch":
            command = ["git", "switch", "-c", str(args["name"])]
        elif operation == "commit":
            command = ["git", "commit", "-am", str(args["message"])]
        elif operation == "push":
            command = ["git", "push", str(args.get("remote", "origin")), str(args["branch"])]
        else:
            raise ValueError(operation)
        result = self.sysiface.run_isolated_command(
            command, cwd=self.workspace_root, timeout_sec=120, backend="subprocess"
        )
        output = (result.stdout + "\n" + result.stderr).strip()
        return ToolResult(ok=result.returncode == 0, output=output or f"git {operation} completed.")

    def get_tools(self) -> list[ToolSpec]:
        common_path = {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        }
        return [
            ToolSpec(
                name="read_code_file",
                description="Read a UTF-8 text file inside the configured coding workspace.",
                input_schema=common_path,
                handler=self._read_code,
                requires_confirmation=False,
            ),
            ToolSpec(
                name="apply_syntax_aware_edit",
                description="Create or replace an exact block in a code file, validating syntax when supported.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "old_text": {"type": "string"},
                        "new_text": {"type": "string"},
                        "create": {"type": "boolean"},
                    },
                    "required": ["path", "new_text"],
                },
                handler=self._apply_edit,
                requires_confirmation=True,
                resource_cost="medium",
            ),
            ToolSpec(
                name="run_code",
                description="Execute a command in the coding workspace using subprocess, Docker, or WSL2 sandboxing.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "command": {"type": "array", "items": {"type": "string"}},
                        "backend": {"type": "string", "enum": ["subprocess", "docker", "wsl2"]},
                        "timeout_sec": {"type": "integer"},
                    },
                    "required": ["command"],
                },
                handler=self._run_code,
                requires_confirmation=True,
                resource_cost="heavy",
            ),
            ToolSpec(
                name="git_status",
                description="Show concise Git working-tree status.",
                input_schema={"type": "object", "properties": {}},
                handler=lambda args: self._git(args, "status"),
                requires_confirmation=False,
            ),
            ToolSpec(
                name="git_diff",
                description="Show an uncommitted Git diff without changing repository state.",
                input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
                handler=lambda args: self._git(args, "diff"),
                requires_confirmation=False,
            ),
            ToolSpec(
                name="git_create_branch",
                description="Create and switch to a new Git branch.",
                input_schema={
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
                handler=lambda args: self._git(args, "branch"),
                requires_confirmation=True,
            ),
            ToolSpec(
                name="git_commit",
                description="Commit tracked working-tree changes with a message.",
                input_schema={
                    "type": "object",
                    "properties": {"message": {"type": "string"}},
                    "required": ["message"],
                },
                handler=lambda args: self._git(args, "commit"),
                requires_confirmation=True,
            ),
            ToolSpec(
                name="git_push",
                description="Push a branch to a configured Git remote.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "remote": {"type": "string"},
                        "branch": {"type": "string"},
                    },
                    "required": ["branch"],
                },
                handler=lambda args: self._git(args, "push"),
                requires_confirmation=True,
            ),
        ]
