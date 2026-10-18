from __future__ import annotations

from aether.hardware_probe import PerformanceTier, TIER_DEFAULTS
from aether.plugins.voice_plugin import TextToSpeech, VoicePlugin, VoiceRegistry, WakeWordListener


def test_voice_preview_captures_requested_profile_without_switching_active_voice() -> None:
    registry = VoiceRegistry()
    registry.set_active("amara")
    tts = TextToSpeech(registry, object())  # synthesis is never invoked in this queue-level test
    tts._ensure_worker = lambda: None

    tts.speak("Preview line", voice_id="nadia")

    assert registry.active.id == "amara"
    assert tts._queue.get_nowait() == ("Preview line", "nadia")


def test_cloud_voice_id_is_configuration_not_a_secret() -> None:
    plugin = VoicePlugin(
        TIER_DEFAULTS[PerformanceTier.LITE],
        object(),  # type: ignore[arg-type]
        elevenlabs_voice_id="configured-voice-id",
    )
    assert plugin.registry.get("custom-elevenlabs").voice_ref == "configured-voice-id"


def test_missing_wakeword_model_does_not_open_background_microphone() -> None:
    listener = WakeWordListener(10, lambda: None)
    listener.start()
    assert listener._thread is None


def test_agent_requested_microphone_capture_requires_confirmation() -> None:
    plugin = VoicePlugin(TIER_DEFAULTS[PerformanceTier.LITE], object())  # type: ignore[arg-type]
    tool = next(item for item in plugin.get_tools() if item.name == "listen_for_voice_command")
    assert tool.requires_confirmation
