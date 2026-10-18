"""
Aether - SystemInterface Module
=================================
Module 1 of 5

Purpose
-------
Every OS-specific call in Aether (file ops, process control, clipboard,
notifications, credential storage, volume/brightness control, etc.) MUST
go through this module. Nothing else in the codebase should ever import
`pywin32`, `winreg`, or any Windows-only library directly.

Why: the project brief requires that Aether be portable to macOS/Linux
later without rewriting the core engine. By hiding every OS call behind
`SystemInterface` (an abstract base class) and providing a single
concrete `WindowsSystemInterface` implementation, a future
`MacSystemInterface` / `LinuxSystemInterface` can be dropped in without
touching agent_loop.py, llm_provider.py, or any plugin.

Usage
-----
    from aether.system_interface import get_system_interface

    sysiface = get_system_interface()   # auto-selects Windows impl on Windows
    sysiface.notify("Aether", "Task complete")
    sysiface.set_volume(50)
"""

from __future__ import annotations

import abc
import hashlib
import platform
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional


# --------------------------------------------------------------------------- #
# Data structures
# --------------------------------------------------------------------------- #

@dataclass
class ProcessInfo:
    pid: int
    name: str
    exe: Optional[str]
    cpu_percent: float
    memory_mb: float


@dataclass
class CredentialEntry:
    service: str
    username: str
    secret: str  # decrypted value, only ever held in memory transiently


@dataclass
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


# --------------------------------------------------------------------------- #
# Abstract interface — the ONLY contract the rest of Aether depends on
# --------------------------------------------------------------------------- #

class SystemInterface(abc.ABC):
    """Abstract OS boundary. Add new capabilities here first, then implement
    per-OS. Never add Windows/Mac/Linux-specific method names — keep the
    contract OS-neutral (e.g. `set_volume`, not `set_volume_via_pycaw`)."""

    # ---- Process / app control -------------------------------------------------
    @abc.abstractmethod
    def launch_app(self, path_or_name: str, args: Optional[list[str]] = None) -> int:
        """Launch an application, return its PID."""

    @abc.abstractmethod
    def close_app(self, pid: int, force: bool = False) -> bool:
        """Terminate a process by PID."""

    @abc.abstractmethod
    def list_processes(self) -> list[ProcessInfo]:
        """Return currently running processes with resource usage."""

    # ---- File operations --------------------------------------------------------
    @abc.abstractmethod
    def search_files(self, root: Path, pattern: str, max_results: int = 200) -> list[Path]:
        ...

    @abc.abstractmethod
    def move_file(self, src: Path, dst: Path) -> Path:
        ...

    @abc.abstractmethod
    def open_file(self, path: Path) -> None:
        """Open a file with its default associated application."""

    # ---- Clipboard ---------------------------------------------------------------
    @abc.abstractmethod
    def get_clipboard(self) -> str:
        ...

    @abc.abstractmethod
    def set_clipboard(self, text: str) -> None:
        ...

    # ---- Notifications -------------------------------------------------------------
    @abc.abstractmethod
    def notify(self, title: str, message: str) -> None:
        ...

    # ---- System controls -------------------------------------------------------------
    @abc.abstractmethod
    def set_volume(self, percent: int) -> None:
        ...

    @abc.abstractmethod
    def set_brightness(self, percent: int) -> None:
        ...

    @abc.abstractmethod
    def toggle_wifi(self, enabled: bool) -> bool:
        ...

    # ---- Credential storage (encrypted) ----------------------------------------------
    @abc.abstractmethod
    def store_credential(self, service: str, username: str, secret: str) -> None:
        ...

    @abc.abstractmethod
    def get_credential(self, service: str, username: str) -> Optional[CredentialEntry]:
        ...

    @abc.abstractmethod
    def delete_credential(self, service: str, username: str) -> bool:
        ...

    # ---- Scheduling ---------------------------------------------------------------------
    @abc.abstractmethod
    def schedule_task(self, name: str, run_at_cron: str, command: str) -> bool:
        """Register a recurring background task with the OS scheduler."""

    # ---- App integration / isolated execution -----------------------------------------
    @abc.abstractmethod
    def app_data_dir(self) -> Path:
        """Return Aether's writable per-user application-data directory."""

    @abc.abstractmethod
    def device_fingerprint(self) -> str:
        """Return a stable, privacy-preserving identifier for this device."""

    @abc.abstractmethod
    def register_global_hotkey(self, shortcut: str, callback: Callable[[], None]) -> str:
        """Register a system-wide shortcut and return an opaque registration id."""

    @abc.abstractmethod
    def unregister_global_hotkey(self, registration_id: str) -> None:
        ...

    @abc.abstractmethod
    def available_sandbox_backends(self) -> list[str]:
        """Return supported execution backends in preference-neutral order."""

    @abc.abstractmethod
    def run_isolated_command(
        self,
        command: list[str],
        *,
        cwd: Path,
        timeout_sec: int,
        backend: str = "subprocess",
    ) -> CommandResult:
        """Run a command using a selected sandbox backend."""


# --------------------------------------------------------------------------- #
# Windows implementation
# --------------------------------------------------------------------------- #

class WindowsSystemInterface(SystemInterface):
    """Concrete Windows implementation. Imports of pywin32 / winreg / pycaw
    are done lazily inside methods so this module can still be *imported*
    (for type-checking / tests) on non-Windows dev machines."""

    def __init__(self) -> None:
        self._hotkeys: dict[str, object] = {}
        if platform.system() != "Windows":
            # Allow import for cross-platform CI/testing, but warn loudly.
            print(
                "[Aether][WARN] WindowsSystemInterface instantiated on a "
                f"non-Windows host ({platform.system()}). Most calls will fail.",
                file=sys.stderr,
            )

    # ---- Process / app control -------------------------------------------------
    def launch_app(self, path_or_name: str, args: Optional[list[str]] = None) -> int:
        args = args or []
        proc = subprocess.Popen([path_or_name, *args], shell=False)
        return proc.pid

    def close_app(self, pid: int, force: bool = False) -> bool:
        import psutil

        try:
            p = psutil.Process(pid)
            p.kill() if force else p.terminate()
            return True
        except psutil.NoSuchProcess:
            return False

    def list_processes(self) -> list[ProcessInfo]:
        import psutil

        out: list[ProcessInfo] = []
        for p in psutil.process_iter(["pid", "name", "exe", "cpu_percent", "memory_info"]):
            try:
                info = p.info
                mem = info["memory_info"].rss / (1024 * 1024) if info.get("memory_info") else 0.0
                out.append(
                    ProcessInfo(
                        pid=info["pid"],
                        name=info["name"] or "",
                        exe=info.get("exe"),
                        cpu_percent=info.get("cpu_percent") or 0.0,
                        memory_mb=round(mem, 1),
                    )
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return out

    # ---- File operations --------------------------------------------------------
    def search_files(self, root: Path, pattern: str, max_results: int = 200) -> list[Path]:
        results: list[Path] = []
        for p in root.rglob(pattern):
            results.append(p)
            if len(results) >= max_results:
                break
        return results

    def move_file(self, src: Path, dst: Path) -> Path:
        dst.parent.mkdir(parents=True, exist_ok=True)
        return Path(shutil.move(str(src), str(dst)))

    def open_file(self, path: Path) -> None:
        import os

        os.startfile(str(path))  # noqa: S606 (Windows-only, expected)

    # ---- Clipboard ---------------------------------------------------------------
    def get_clipboard(self) -> str:
        import win32clipboard  # pywin32

        win32clipboard.OpenClipboard()
        try:
            return win32clipboard.GetClipboardData()
        except Exception:
            return ""
        finally:
            win32clipboard.CloseClipboard()

    def set_clipboard(self, text: str) -> None:
        import win32clipboard

        win32clipboard.OpenClipboard()
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardText(text)
        win32clipboard.CloseClipboard()

    # ---- Notifications -------------------------------------------------------------
    def notify(self, title: str, message: str) -> None:
        from win10toast import ToastNotifier  # lightweight, no admin rights needed

        ToastNotifier().show_toast(title, message, duration=5, threaded=True)

    # ---- System controls -------------------------------------------------------------
    def set_volume(self, percent: int) -> None:
        from ctypes import cast, POINTER
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

        percent = max(0, min(100, percent))
        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        volume = cast(interface, POINTER(IAudioEndpointVolume))
        volume.SetMasterVolumeLevelScalar(percent / 100.0, None)

    def set_brightness(self, percent: int) -> None:
        import screen_brightness_control as sbc

        sbc.set_brightness(max(0, min(100, percent)))

    def toggle_wifi(self, enabled: bool) -> bool:
        state = "enable" if enabled else "disable"
        # Requires the Wi-Fi adapter's interface name; "Wi-Fi" is the Windows default.
        result = subprocess.run(
            ["netsh", "interface", "set", "interface", "Wi-Fi", state],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

    # ---- Credential storage (Windows Credential Manager) ------------------------------
    def store_credential(self, service: str, username: str, secret: str) -> None:
        import keyring  # backed by Windows Credential Manager on Windows

        keyring.set_password(f"aether:{service}", username, secret)

    def get_credential(self, service: str, username: str) -> Optional[CredentialEntry]:
        import keyring

        secret = keyring.get_password(f"aether:{service}", username)
        if secret is None:
            return None
        return CredentialEntry(service=service, username=username, secret=secret)

    def delete_credential(self, service: str, username: str) -> bool:
        import keyring

        try:
            keyring.delete_password(f"aether:{service}", username)
            return True
        except keyring.errors.PasswordDeleteError:
            return False

    # ---- Scheduling ---------------------------------------------------------------------
    def schedule_task(self, name: str, run_at_cron: str, command: str) -> bool:
        """Minimal wrapper around schtasks.exe. `run_at_cron` here is simplified
        to a daily HH:MM time string (e.g. "07:30") rather than full cron syntax,
        since Windows Task Scheduler doesn't speak cron natively."""
        result = subprocess.run(
            [
                "schtasks", "/Create", "/TN", f"Aether_{name}",
                "/TR", command, "/SC", "DAILY", "/ST", run_at_cron, "/F",
            ],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

    # ---- App integration / isolated execution -----------------------------------------
    def app_data_dir(self) -> Path:
        import os

        base = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir()))
        path = base / "Aether"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def device_fingerprint(self) -> str:
        """Hash Windows' machine identifier before it leaves the OS boundary."""
        import winreg

        access = winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0)
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Cryptography",
            0,
            access,
        ) as key:
            machine_guid = str(winreg.QueryValueEx(key, "MachineGuid")[0])
        return hashlib.sha256(f"aether-license-v1:{machine_guid}".encode("utf-8")).hexdigest()

    def register_global_hotkey(self, shortcut: str, callback: Callable[[], None]) -> str:
        import uuid

        try:
            import keyboard
        except ImportError as exc:
            raise RuntimeError("Global hotkeys require the optional 'keyboard' package.") from exc
        registration_id = str(uuid.uuid4())
        handle = keyboard.add_hotkey(shortcut, callback, suppress=False)
        self._hotkeys[registration_id] = handle
        return registration_id

    def unregister_global_hotkey(self, registration_id: str) -> None:
        handle = self._hotkeys.pop(registration_id, None)
        if handle is None:
            return
        try:
            import keyboard

            keyboard.remove_hotkey(handle)
        except (ImportError, KeyError, ValueError):
            pass

    def available_sandbox_backends(self) -> list[str]:
        backends = ["subprocess"]
        if shutil.which("docker"):
            backends.append("docker")
        if shutil.which("wsl"):
            backends.append("wsl2")
        return backends

    def run_isolated_command(
        self,
        command: list[str],
        *,
        cwd: Path,
        timeout_sec: int,
        backend: str = "subprocess",
    ) -> CommandResult:
        cwd = cwd.resolve()
        if not cwd.exists() or not cwd.is_dir():
            raise ValueError(f"Invalid execution directory: {cwd}")

        actual = command
        run_cwd = cwd
        if backend == "docker":
            if not shutil.which("docker"):
                raise RuntimeError("Docker is not available.")
            actual = [
                "docker",
                "run",
                "--rm",
                "--network=none",
                "--memory=1g",
                "--cpus=1",
                "-v",
                f"{cwd}:/workspace:rw",
                "-w",
                "/workspace",
                "python:3.12-alpine",
                *command,
            ]
        elif backend == "wsl2":
            if not shutil.which("wsl"):
                raise RuntimeError("WSL2 is not available.")
            # `wslpath` conversion is intentionally kept inside this Windows
            # implementation rather than leaking into the coding plugin.
            converted = subprocess.run(
                ["wsl", "wslpath", "-a", str(cwd)], capture_output=True, text=True, timeout=10
            )
            if converted.returncode != 0:
                raise RuntimeError(converted.stderr.strip() or "Could not map the workspace into WSL2.")
            actual = ["wsl", "--cd", converted.stdout.strip(), "--", *command]
            run_cwd = Path.cwd()
        elif backend != "subprocess":
            raise ValueError(f"Unknown sandbox backend: {backend}")

        try:
            completed = subprocess.run(
                actual,
                cwd=run_cwd,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                shell=False,
            )
            return CommandResult(completed.returncode, completed.stdout, completed.stderr)
        except subprocess.TimeoutExpired as exc:
            return CommandResult(
                returncode=124,
                stdout=exc.stdout or "",
                stderr=f"Command timed out after {timeout_sec} seconds.",
            )


# --------------------------------------------------------------------------- #
# Factory — this is the only entry point the rest of the app should use
# --------------------------------------------------------------------------- #

_instance: Optional[SystemInterface] = None


def get_system_interface() -> SystemInterface:
    """Returns a singleton SystemInterface for the current OS.
    Today this always returns WindowsSystemInterface (Windows-only release).
    When macOS/Linux support is added, branch on platform.system() here —
    that is the ONLY place that branch needs to exist."""
    global _instance
    if _instance is None:
        system = platform.system()
        if system == "Windows":
            _instance = WindowsSystemInterface()
        else:
            raise NotImplementedError(
                f"Aether currently only supports Windows. Detected OS: {system}. "
                "macOS/Linux implementations will register here in a future release."
            )
    return _instance
