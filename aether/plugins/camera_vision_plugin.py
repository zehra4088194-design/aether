"""
Aether - Camera & Screen Vision Plugin
==========================================
Module 7 of 7

Purpose
-------
Lets Aether answer things like "what's in front of the camera right now"
or "what's on my screen" by capturing a single frame and describing it
with a local, offline vision-capable model (e.g. `llava` or `moondream`
served through Ollama) — no cloud upload required. A cloud vision model
can optionally be used instead if the user has configured one via
`model_registry.py`, but camera/screen capture defaults to fully local.

Privacy — read this before wiring into the UI
----------------------------------------------
Camera access is one of the most sensitive permissions an app can have.
This plugin follows strict rules, matching Section 12 and the NEW
first-run consent screen from `improved_master_prompt.md`:

1. The camera tool is DISABLED by default (`camera_enabled=False` at
   construction) and only becomes usable after the user explicitly turns
   "Camera Vision" on in Settings (a per-feature toggle, not bundled with
   anything else).
2. EVERY single camera capture requires confirmation
   (`requires_confirmation=True`) — there is no "always allow" fast path
   in this plugin. If the product later adds one, it must be a separate,
   explicit, clearly-labeled opt-in (mirrors the trading module's rule
   about no silent auto-actions).
3. No frame is ever written to disk or sent anywhere by default — it is
   processed in memory and discarded immediately after generating the
   description. If the user wants to save a frame, that is a distinct,
   separately-confirmed action.
4. A visible camera-active indicator must be shown in the UI any time
   the camera is actually open (handled by the UI layer, but this
   plugin exposes `is_camera_active` so the UI can bind to it).
5. Screen capture ("what's on my screen") is less sensitive than camera
   but still requires confirmation, since the screen may show private
   information (passwords, messages, etc.).

Resource cost: "heavy" — vision models are RAM/VRAM hungry, so this
plugin is auto-disabled on Lite tier by the adaptive engine unless the
user explicitly force-enables it (and is warned about performance
impact when they do).
"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass
from typing import Optional

from aether.agent_loop import AetherPlugin, ToolSpec, ToolResult
from aether.llm_provider import ManagedLLM, LLMProviderError


@dataclass
class VisionModelConfig:
    """Which model answers vision queries. Defaults to a local Ollama
    vision model; can be swapped for a cloud one via ModelRegistry if
    the user explicitly configures that (still gated by the same
    confirmation rules above)."""

    ollama_model: str = "llava:7b"
    ollama_host: str = "http://localhost:11434"


class CameraVisionPlugin(AetherPlugin):
    name = "camera_vision"
    description = (
        "Describes what the webcam or screen currently shows, using a local "
        "offline vision model. Camera access is off by default and every "
        "capture requires explicit confirmation."
    )
    resource_cost = "heavy"

    def __init__(
        self,
        llm: Optional[ManagedLLM] = None,
        vision_config: Optional[VisionModelConfig] = None,
        camera_enabled: bool = False,
        screen_enabled: bool = False,
    ):
        self.llm = llm
        self.vision_config = vision_config or VisionModelConfig()
        self.camera_enabled = camera_enabled  # Settings-controlled master switch
        self.screen_enabled = screen_enabled
        self.is_camera_active = False          # UI binds an on-screen indicator to this

    # ---- lifecycle -----------------------------------------------------------------
    def on_enable(self) -> None:
        pass

    def on_disable(self) -> None:
        self.is_camera_active = False

    # ---- internal: talk to a local vision model via Ollama ------------------------------
    def _describe_image(self, image_bytes: bytes, question: str) -> str:
        b64 = base64.b64encode(image_bytes).decode("utf-8")
        if self.llm is None:
            raise LLMProviderError("No managed local vision model is configured.")
        response = self.llm.chat(
            messages=[{"role": "user", "content": question, "images": [b64]}],
            tools=[],
            system=(
                "Describe only what is visible in the supplied in-memory image. "
                "Do not infer identity or sensitive attributes. State uncertainty clearly."
            ),
        )
        return response.text.strip()

    # ---- tool handlers ----------------------------------------------------------------
    def _describe_camera(self, args: dict) -> ToolResult:
        if not self.camera_enabled:
            return ToolResult(
                ok=False,
                output=(
                    "Camera Vision is turned off in Settings. Enable it under "
                    "Settings > Privacy > Camera Vision to use this feature."
                ),
            )

        question = args.get("question", "Describe what is visible in this image in detail.")

        try:
            self.is_camera_active = True
            frame_bytes = self._capture_camera_frame()
            if frame_bytes is None:
                return ToolResult(ok=False, output="No camera found or camera could not be accessed.")
            description = self._describe_image(frame_bytes, question)
        finally:
            self.is_camera_active = False  # always release the "camera active" indicator

        return ToolResult(ok=True, output=description)

    def _describe_screen(self, args: dict) -> ToolResult:
        if not self.screen_enabled:
            return ToolResult(
                ok=False,
                output=(
                    "Screen Vision is turned off in Settings. Enable it under "
                    "Settings > Privacy > Screen Vision to use this feature."
                ),
            )
        question = args.get("question", "Describe what is currently on this screen.")
        frame_bytes = self._capture_screen()
        description = self._describe_image(frame_bytes, question)
        return ToolResult(ok=True, output=description)

    # ---- capture helpers --------------------------------------------------------------
    def _capture_camera_frame(self) -> Optional[bytes]:
        """Grabs exactly one frame, then immediately releases the camera.
        Nothing is written to disk."""
        import cv2  # opencv-python

        cap = cv2.VideoCapture(0)
        try:
            if not cap.isOpened():
                return None
            ok, frame = cap.read()
            if not ok:
                return None
            success, buffer = cv2.imencode(".jpg", frame)
            if not success:
                return None
            return buffer.tobytes()
        finally:
            cap.release()  # release immediately, never keep the camera open idly

    def _capture_screen(self) -> bytes:
        import mss

        with mss.mss() as sct:
            monitor = sct.monitors[0]  # full virtual screen across all monitors
            shot = sct.grab(monitor)
            buf = io.BytesIO()
            import PIL.Image as Image

            img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
            img.save(buf, format="JPEG", quality=85)
            return buf.getvalue()

    # ---- tool registration --------------------------------------------------------------
    def get_tools(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="describe_camera_view",
                description=(
                    "Capture a single frame from the webcam and describe what is visible. "
                    "Requires Camera Vision to be enabled in Settings and confirmation on "
                    "every use."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": "Optional specific question about the camera view.",
                        }
                    },
                },
                handler=self._describe_camera,
                requires_confirmation=True,
                resource_cost="heavy",
            ),
            ToolSpec(
                name="describe_screen_view",
                description="Take a screenshot and describe what is currently on screen.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": "Optional specific question about the screen contents.",
                        }
                    },
                },
                handler=self._describe_screen,
                requires_confirmation=True,
                resource_cost="medium",
            ),
        ]
