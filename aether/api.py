"""Loopback-only FastAPI/WS bridge consumed by the Tauri frontend."""

from __future__ import annotations

import asyncio
import os
import secrets
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Any, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from aether.llm_provider import LLMProviderError
from aether.license_manager import LicenseManager
from aether.model_registry import ModelValidationError
from aether.runtime import AetherRuntime


API_HOST = "127.0.0.1"
API_PORT = int(os.environ.get("AETHER_API_PORT", "8765"))
API_TOKEN = os.environ.get("AETHER_API_TOKEN") or secrets.token_urlsafe(32)

_runtime: Optional[AetherRuntime] = None
_runtime_lock = asyncio.Lock()
_license: Optional[LicenseManager] = None


def _parent_process_alive(pid: int) -> bool:
    """Return whether the desktop process that owns this sidecar is alive."""

    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        process_query_limited_information = 0x1000
        still_active = 259
        handle = ctypes.windll.kernel32.OpenProcess(
            process_query_limited_information, False, pid
        )
        if not handle:
            return False
        exit_code = ctypes.c_ulong()
        try:
            return bool(
                ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
                and exit_code.value == still_active
            )
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)

    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    return True


def _start_parent_watchdog() -> None:
    """Prevent a packaged backend from surviving a crashed desktop shell."""

    raw_pid = os.environ.get("AETHER_PARENT_PID", "")
    try:
        parent_pid = int(raw_pid)
    except ValueError:
        return
    if parent_pid <= 0 or parent_pid == os.getpid():
        return

    def monitor() -> None:
        while _parent_process_alive(parent_pid):
            time.sleep(2)
        os._exit(0)

    threading.Thread(target=monitor, name="aether-parent-watchdog", daemon=True).start()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _runtime, _license
    try:
        yield
    finally:
        if _runtime is not None and hasattr(_runtime, "close"):
            await asyncio.to_thread(_runtime.close)
        _runtime = None
        _license = None


app = FastAPI(
    title="Aether Local API",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "tauri://localhost",
        "https://tauri.localhost",
        "http://tauri.localhost",
        "http://localhost:1420",
        "http://127.0.0.1:1420",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type", "X-Aether-Token"],
)


def license_manager() -> LicenseManager:
    global _license
    if _license is None:
        _license = LicenseManager()
    return _license


@app.middleware("http")
async def license_gate(request: Request, call_next):
    public_paths = {"/health", "/v1/license/status", "/v1/license/activate", "/v1/license/deactivate"}
    if request.url.path not in public_paths and request.url.path.startswith("/v1/"):
        status = await asyncio.to_thread(license_manager().local_status)
        if status.required and not status.valid:
            return JSONResponse(
                status_code=402,
                content={"detail": "A valid device activation is required."},
            )
    return await call_next(request)

async def runtime() -> AetherRuntime:
    global _runtime
    if _runtime is None:
        async with _runtime_lock:
            if _runtime is None:
                _runtime = await asyncio.to_thread(AetherRuntime)
    return _runtime


async def require_token(x_aether_token: Optional[str] = Header(default=None)) -> None:
    if not x_aether_token or not secrets.compare_digest(x_aether_token, API_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid Aether API token.")


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=40_000)


class ConfirmationDecision(BaseModel):
    approved: bool


class ToggleRequest(BaseModel):
    enabled: bool


class ToolRequest(BaseModel):
    arguments: dict[str, Any] = Field(default_factory=dict)


class PrivacyRequest(BaseModel):
    voice_recording: bool = False
    camera_vision: bool = False
    screen_vision: bool = False
    browsing_history: bool = False
    memory_storage: bool = False
    telemetry: bool = False


class ModelRequest(BaseModel):
    display_name: str
    kind: str
    model_id: str
    endpoint_url: Optional[str] = None
    api_key_env_var: Optional[str] = None
    notes: str = ""
    api_key: Optional[str] = None
    is_default: bool = False


class ElevenLabsVoiceRequest(BaseModel):
    voice_id: str = Field(min_length=1, max_length=200)
    api_key: Optional[str] = Field(default=None, min_length=8, max_length=500)


class SettingRequest(BaseModel):
    value: Any


class LicenseActivationRequest(BaseModel):
    license_key: str = Field(min_length=16, max_length=120)


@app.get("/health")
async def health() -> dict:
    return {"ok": True, "service": "aether", "version": "0.1.0"}


@app.get("/v1/license/status", dependencies=[Depends(require_token)])
async def license_status() -> dict:
    return (await asyncio.to_thread(license_manager().local_status)).to_dict()


@app.post("/v1/license/activate", dependencies=[Depends(require_token)])
async def activate_license(request: LicenseActivationRequest) -> dict:
    try:
        status = await asyncio.to_thread(license_manager().activate, request.license_key)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return status.to_dict()


@app.post("/v1/license/heartbeat", dependencies=[Depends(require_token)])
async def heartbeat_license() -> dict:
    return (await asyncio.to_thread(license_manager().heartbeat)).to_dict()


@app.post("/v1/license/deactivate", dependencies=[Depends(require_token)])
async def deactivate_license() -> dict:
    try:
        status = await asyncio.to_thread(license_manager().deactivate)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return status.to_dict()


@app.get("/v1/state", dependencies=[Depends(require_token)])
async def get_state() -> dict:
    instance = await runtime()
    return await asyncio.to_thread(instance.state)


@app.post("/v1/privacy", dependencies=[Depends(require_token)])
async def set_privacy(request: PrivacyRequest) -> dict:
    instance = await runtime()
    return await asyncio.to_thread(instance.set_privacy, request.model_dump())


@app.post("/v1/chat", dependencies=[Depends(require_token)])
async def chat(request: ChatRequest) -> dict:
    instance = await runtime()
    try:
        response = await asyncio.to_thread(instance.chat, request.message)
        return {"response": response}
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except LLMProviderError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/v1/tools/{tool_name}", dependencies=[Depends(require_token)])
async def execute_tool(tool_name: str, request: ToolRequest) -> dict:
    instance = await runtime()
    result = await asyncio.to_thread(instance.execute_tool, tool_name, request.arguments)
    return asdict(result)


@app.post("/v1/confirmations/{confirmation_id}", dependencies=[Depends(require_token)])
async def resolve_confirmation(confirmation_id: str, request: ConfirmationDecision) -> dict:
    instance = await runtime()
    if not instance.confirmations.resolve(confirmation_id, request.approved):
        raise HTTPException(status_code=404, detail="Confirmation is no longer pending.")
    return {"resolved": True, "approved": request.approved}


@app.post("/v1/plugins/{plugin_name}", dependencies=[Depends(require_token)])
async def toggle_plugin(plugin_name: str, request: ToggleRequest) -> dict:
    instance = await runtime()
    try:
        await asyncio.to_thread(instance.set_plugin_enabled, plugin_name, request.enabled)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown plugin.") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return {"name": plugin_name, "enabled": request.enabled}


@app.post("/v1/voices/{voice_id}/select", dependencies=[Depends(require_token)])
async def select_voice(voice_id: str) -> dict:
    instance = await runtime()
    voice = instance._plugin_instances.get("voice")
    if voice is None or voice.registry.get(voice_id) is None:
        raise HTTPException(status_code=404, detail="Unknown voice.")
    try:
        return await asyncio.to_thread(instance.select_voice, voice_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/v1/voices/{voice_id}/preview", dependencies=[Depends(require_token)])
async def preview_voice(voice_id: str) -> dict:
    instance = await runtime()
    voice = instance._plugin_instances.get("voice")
    if voice is None or voice.registry.get(voice_id) is None:
        raise HTTPException(status_code=404, detail="Unknown voice.")
    selected = voice.registry.get(voice_id)
    if selected.engine == "elevenlabs":
        if not selected.voice_ref:
            raise HTTPException(status_code=422, detail="Configure an ElevenLabs voice ID first.")
        if not voice.sysiface.get_credential("elevenlabs", "api_key"):
            raise HTTPException(status_code=422, detail="Store an ElevenLabs API key first.")
    voice.tts.speak(selected.sample_phrase, voice_id=voice_id)
    return {"previewing": voice_id}


@app.put("/v1/voices/elevenlabs/configure", dependencies=[Depends(require_token)])
async def configure_elevenlabs_voice(request: ElevenLabsVoiceRequest) -> dict:
    instance = await runtime()
    try:
        return await asyncio.to_thread(
            instance.configure_elevenlabs_voice, request.voice_id, request.api_key
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/v1/models/ollama", dependencies=[Depends(require_token)])
async def discover_ollama() -> dict:
    instance = await runtime()
    host = instance.config.get("llm.local.ollama_host", "http://localhost:11434")
    try:
        models = await asyncio.to_thread(instance.model_registry.discover_ollama_models, host)
    except ModelValidationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"models": models}


@app.post("/v1/models", dependencies=[Depends(require_token)])
async def add_model(request: ModelRequest) -> dict:
    instance = await runtime()
    payload = request.model_dump(exclude={"api_key"})
    try:
        entry = await asyncio.to_thread(instance.add_model, payload, request.api_key)
    except (ModelValidationError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    result = asdict(entry)
    result["kind"] = entry.kind.value
    return result


@app.put("/v1/models/{entry_id}", dependencies=[Depends(require_token)])
async def update_model(entry_id: str, request: ModelRequest) -> dict:
    instance = await runtime()
    payload = request.model_dump(exclude={"api_key"})
    try:
        entry = await asyncio.to_thread(instance.update_model, entry_id, payload, request.api_key)
    except ModelValidationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    result = asdict(entry)
    result["kind"] = entry.kind.value
    return result


@app.post("/v1/models/{entry_id}/default", dependencies=[Depends(require_token)])
async def set_default_model(entry_id: str) -> dict:
    instance = await runtime()
    try:
        await asyncio.to_thread(instance.set_default_model, entry_id)
    except ModelValidationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return instance.model_state()


@app.delete("/v1/models/{entry_id}", dependencies=[Depends(require_token)])
async def delete_model(entry_id: str) -> dict:
    instance = await runtime()
    removed = await asyncio.to_thread(instance.remove_model, entry_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Unknown model entry.")
    return {"removed": True}


@app.post("/v1/diagnostics/export", dependencies=[Depends(require_token)])
async def export_diagnostics() -> dict:
    instance = await runtime()
    path = await asyncio.to_thread(instance.export_diagnostics)
    return {"path": str(path), "uploaded": False}


@app.put("/v1/settings/{setting_path:path}", dependencies=[Depends(require_token)])
async def set_setting(setting_path: str, request: SettingRequest) -> dict:
    instance = await runtime()
    dotted_path = setting_path.replace("/", ".")
    try:
        await asyncio.to_thread(instance.set_setting, dotted_path, request.value)
    except (PermissionError, ValueError) as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return {"path": dotted_path, "value": request.value}


@app.websocket("/v1/events")
async def events_socket(websocket: WebSocket, token: str = "") -> None:
    if not token or not secrets.compare_digest(token, API_TOKEN):
        await websocket.close(code=4401)
        return
    status = await asyncio.to_thread(license_manager().local_status)
    if status.required and not status.valid:
        await websocket.close(code=4402)
        return
    await websocket.accept()
    instance = await runtime()
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=300)

    def listener(event: dict) -> None:
        def enqueue() -> None:
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(event)

        loop.call_soon_threadsafe(enqueue)

    unsubscribe = instance.events.subscribe(listener)
    try:
        await websocket.send_json({"type": "snapshot", "data": instance.state()})
        while True:
            event = await queue.get()
            await websocket.send_json(event)
    except WebSocketDisconnect:
        pass
    finally:
        unsubscribe()


def main() -> None:
    import uvicorn

    _start_parent_watchdog()
    # The Windows sidecar is packaged without a console. Uvicorn's default
    # logging configuration installs stream formatters and crashes when
    # sys.stdout/sys.stderr are unavailable in that mode.
    uvicorn.run(
        app,
        host=API_HOST,
        port=API_PORT,
        log_level="info",
        log_config=None,
        access_log=False,
    )


if __name__ == "__main__":
    main()
