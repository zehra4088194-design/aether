from __future__ import annotations

from aether.llm_provider import LLMResponse
from aether.plugins.camera_vision_plugin import CameraVisionPlugin


class RecordingManagedLLM:
    def __init__(self) -> None:
        self.messages = []

    def chat(self, *, messages, **_kwargs) -> LLMResponse:
        self.messages = messages
        return LLMResponse(text="A desk is visible.")


def test_camera_description_routes_through_managed_llm() -> None:
    llm = RecordingManagedLLM()
    plugin = CameraVisionPlugin(llm)  # type: ignore[arg-type]

    assert plugin._describe_image(b"image-bytes", "What is visible?") == "A desk is visible."
    assert llm.messages[0]["images"]
