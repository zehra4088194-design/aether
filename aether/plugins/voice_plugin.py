"""
Aether - Voice Plugin (wake-word + STT + selectable TTS voices)
================================================================
Module 9

Purpose
-------
Lets the user talk to Aether and hear it talk back, instead of only
typing. Three pieces, all swappable:

    wake word  ->  "Hey Aether" (openWakeWord) or the global hotkey
    STT        ->  faster-whisper, model size picked from TierConfig
                   (tiny/base/small) so Lite-tier machines stay snappy
    TTS        ->  a small curated *voice picker* -- Piper voices run
                   fully offline and are the default; ElevenLabs voices
                   are opt-in/cloud and clearly marked as such. The user
                   picks a voice once in Settings > Voice and every
                   reply after that uses it.

"Reply foran" (reply immediately)
----------------------------------
`speak_streaming()` does NOT wait for the whole LLM answer to finish
before starting to talk: the agent loop's `on_step`/text stream is fed
in sentence-by-sentence, and each finished sentence is synthesized and
queued for playback the moment it's complete. The user hears the first
sentence while the model is still writing the second one, instead of
sitting through the model's full latency in silence.

Safety / privacy
-----------------
- With an explicit custom wake-word model configured, a local mic stream
  checks for that phrase in memory. The global hotkey and mic button work
  without running a background mic stream.
- Captured mic audio is never written to disk or uploaded. Selecting an
  ElevenLabs voice sends assistant reply text (not microphone audio) to
  that cloud service; Piper voices never leave the machine.
- A visible "listening" / "speaking" state is exposed via
  `VoicePlugin.is_listening` / `is_speaking` for the UI's mic indicator
  (same pattern as `CameraVisionPlugin.is_camera_active`).
"""

from __future__ import annotations

import logging
import os
import queue
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from aether.agent_loop import AetherPlugin, ToolSpec, ToolResult
from aether.hardware_probe import TierConfig
from aether.system_interface import SystemInterface, get_system_interface

logger = logging.getLogger("aether.voice_plugin")


# --------------------------------------------------------------------------- #
# Voice catalogue
# --------------------------------------------------------------------------- #

@dataclass
class VoiceProfile:
    """One selectable voice. Offline (Piper) voices need no credential;
    cloud (ElevenLabs) voices resolve their API key via
    SystemInterface.get_credential at synth time, same as any other
    secret in Aether -- never stored in plaintext config."""

    id: str
    display_name: str
    engine: str  # "piper" | "elevenlabs"
    voice_ref: str  # piper .onnx model name, or elevenlabs voice_id
    style: str  # short human description shown in the picker
    offline: bool
    sample_phrase: str = "Hey, I'm ready whenever you are."


# A small, deliberately curated starting set -- warm/friendly defaults
# rather than a flat robotic default. Piper voices ship free & offline;
# users can still add more via the same "bring your own" pattern as
# model_registry.py if Codex wants to extend this list.
BUILTIN_VOICES: list[VoiceProfile] = [
    VoiceProfile(
        id="amara",
        display_name="Amara",
        engine="piper",
        voice_ref="en_US-amy-medium",
        style="warm, friendly, a little playful",
        offline=True,
        sample_phrase="Hi! I organized your Downloads folder just now.",
    ),
    VoiceProfile(
        id="rohan",
        display_name="Rohan",
        engine="piper",
        voice_ref="en_US-ryan-medium",
        style="calm, steady, easy to listen to for long sessions",
        offline=True,
        sample_phrase="Sure -- give me one second to check that for you.",
    ),
    VoiceProfile(
        id="nadia",
        display_name="Nadia",
        engine="piper",
        voice_ref="en_GB-alba-medium",
        style="soft-spoken, gentle, good for late-night use",
        offline=True,
        sample_phrase="All done -- want me to read the summary out loud?",
    ),
    VoiceProfile(
        id="zeke",
        display_name="Zeke",
        engine="piper",
        voice_ref="en_US-joe-medium",
        style="upbeat, energetic, a bit cheeky",
        offline=True,
        sample_phrase="Nice! Three tasks done, zero problems. Not bad, right?",
    ),
    VoiceProfile(
        id="custom-elevenlabs",
        display_name="Custom (ElevenLabs)",
        engine="elevenlabs",
        voice_ref="",  # user supplies their own voice_id in Settings
        style="cloud voice cloning -- pick any ElevenLabs voice you own",
        offline=False,
        sample_phrase="This voice streams from the cloud, so it needs an API key.",
    ),
]


class VoiceRegistry:
    """Tracks the catalogue + which voice is currently active. Mirrors
    the shape of ModelRegistry (model_registry.py) on purpose -- same
    "pick one from a list, persist the choice" pattern, just for voices
    instead of models."""

    def __init__(self, voices: Optional[list[VoiceProfile]] = None) -> None:
        self._voices = {v.id: v for v in (voices or BUILTIN_VOICES)}
        self._active_id = "amara"  # a friendly default, not a flat robotic one

    def list_voices(self) -> list[VoiceProfile]:
        return list(self._voices.values())

    def get(self, voice_id: str) -> Optional[VoiceProfile]:
        return self._voices.get(voice_id)

    @property
    def active(self) -> VoiceProfile:
        return self._voices[self._active_id]

    def set_active(self, voice_id: str) -> None:
        if voice_id not in self._voices:
            raise ValueError(f"Unknown voice id: {voice_id}")
        self._active_id = voice_id
        logger.info("Active voice set to %s", voice_id)


# --------------------------------------------------------------------------- #
# Speech-to-text
# --------------------------------------------------------------------------- #

class SpeechToText:
    """Thin wrapper around faster-whisper. Model size comes from
    TierConfig.whisper_model_size so Lite-tier machines load "tiny"
    instead of stalling on "small"."""

    def __init__(self, tier_config: TierConfig) -> None:
        self.model_size = tier_config.whisper_model_size
        self._model = None  # lazy-loaded on first use

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        from faster_whisper import WhisperModel  # imported lazily -- heavy dep

        self._model = WhisperModel(self.model_size, device="auto", compute_type="int8")
        logger.info("Loaded faster-whisper model: %s", self.model_size)

    def transcribe(self, audio: object) -> str:
        self._ensure_loaded()
        segments, _info = self._model.transcribe(audio, beam_size=5)
        return " ".join(seg.text.strip() for seg in segments).strip()


# --------------------------------------------------------------------------- #
# Wake-word trigger (openWakeWord, fully local)
# --------------------------------------------------------------------------- #

class WakeWordListener:
    """Streams 16 kHz PCM frames into openWakeWord on a background thread.

    A custom ``Hey Aether`` ONNX/TFLite model path can be supplied from
    settings.  If it is absent, the listener still initializes the bundled
    openWakeWord catalogue and only fires when a model whose name contains
    ``aether`` is present.  The global hotkey therefore remains a reliable
    fallback on every installation.
    """

    def __init__(
        self,
        poll_hz: float,
        on_detected: Callable[[], None],
        model_path: Optional[str] = None,
        threshold: float = 0.55,
    ) -> None:
        self.poll_hz = max(1.0, poll_hz)
        self.on_detected = on_detected
        self.model_path = model_path
        self.threshold = threshold
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        if not self.model_path:
            logger.info("Wake-word monitor is off until a custom Hey Aether model is configured.")
            return
        model_path = Path(self.model_path).expanduser()
        if not model_path.is_file():
            logger.warning("Wake-word model was not found: %s", model_path)
            return
        self.model_path = str(model_path)
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="AetherWakeWord", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _run(self) -> None:
        try:
            from openwakeword.model import Model
            import numpy as np
            import sounddevice as sd

            model = Model(wakeword_models=[self.model_path])
            target_fragment = Path(self.model_path).stem.lower()
            desired = max(1280, int(16000 / self.poll_hz))
            blocksize = max(1280, round(desired / 1280) * 1280)
            cooldown_until = 0.0

            with sd.RawInputStream(
                samplerate=16000,
                blocksize=blocksize,
                channels=1,
                dtype="int16",
            ) as stream:
                while not self._stop.is_set():
                    frame, overflowed = stream.read(blocksize)
                    if overflowed:
                        logger.debug("Wake-word audio input overflowed.")
                    scores = model.predict(np.frombuffer(frame, dtype=np.int16))
                    if time.monotonic() < cooldown_until:
                        continue
                    matched = [
                        score
                        for name, score in scores.items()
                        if target_fragment in name.lower() or "aether" in name.lower()
                    ]
                    if matched and max(matched) >= self.threshold:
                        cooldown_until = time.monotonic() + 2.0
                        self.on_detected()
        except Exception as exc:
            # Hotkey/button input remains available; missing audio hardware or
            # model assets must not disable the rest of Aether.
            logger.warning("Wake-word listener unavailable: %s", exc)


# --------------------------------------------------------------------------- #
# Text-to-speech (streaming, sentence-by-sentence for fast replies)
# --------------------------------------------------------------------------- #

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


class TextToSpeech:
    """Synthesizes and plays speech using whichever VoiceProfile is
    currently active. `speak_streaming` is the "reply foran" path: feed
    it partial text as the model generates it (e.g. from
    AgentLoop.on_step for kind == "assistant_text") and it speaks each
    finished sentence immediately instead of waiting for the full reply."""

    def __init__(
        self,
        registry: VoiceRegistry,
        sysiface: SystemInterface,
        on_state_change: Optional[Callable[[bool], None]] = None,
    ) -> None:
        self.registry = registry
        self.sysiface = sysiface
        self._on_state_change = on_state_change or (lambda speaking: None)
        self._speaking = False
        self._buffer = ""
        # Capture the selected voice when work is queued.  This matters for
        # previews: the UI can audition a voice without changing the voice
        # used by normal assistant replies that are already in flight.
        self._queue: "queue.Queue[tuple[str, str]]" = queue.Queue()
        self._worker: Optional[threading.Thread] = None

    @property
    def is_speaking(self) -> bool:
        return self._speaking

    def feed(self, text_chunk: str) -> None:
        """Call repeatedly as the LLM streams tokens. Splits off complete
        sentences and queues them for immediate playback."""
        self._buffer += text_chunk
        parts = _SENTENCE_SPLIT_RE.split(self._buffer)
        # Keep the last (possibly incomplete) fragment in the buffer.
        *complete, self._buffer = parts if len(parts) > 1 else ([], parts[0])
        for sentence in complete:
            sentence = sentence.strip()
            if sentence:
                self._queue.put((sentence, self.registry.active.id))
        self._ensure_worker()

    def flush(self) -> None:
        """Call once the LLM has finished this turn -- speaks whatever's
        left in the buffer even if it didn't end in punctuation."""
        remainder = self._buffer.strip()
        self._buffer = ""
        if remainder:
            self._queue.put((remainder, self.registry.active.id))
        self._ensure_worker()

    def speak(self, text: str, voice_id: Optional[str] = None) -> None:
        """Non-streaming convenience path -- speaks one complete string."""
        selected_id = voice_id or self.registry.active.id
        if self.registry.get(selected_id) is None:
            raise ValueError(f"Unknown voice id: {selected_id}")
        self._queue.put((text, selected_id))
        self._ensure_worker()

    def _ensure_worker(self) -> None:
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._drain_loop, daemon=True)
            self._worker.start()

    def _drain_loop(self) -> None:
        while True:
            try:
                sentence, voice_id = self._queue.get(timeout=0.5)
            except queue.Empty:
                return
            self._speaking = True
            self._on_state_change(True)
            try:
                voice = self.registry.get(voice_id)
                if voice is None:
                    raise ValueError(f"Unknown voice id: {voice_id}")
                self._synthesize_and_play(sentence, voice)
            except Exception as e:  # a TTS hiccup must never crash the agent loop
                logger.warning("TTS failed for voice %s: %s", voice_id, e)
            finally:
                if self._queue.empty():
                    self._speaking = False
                    self._on_state_change(False)

    def _synthesize_and_play(self, sentence: str, voice: VoiceProfile) -> None:
        if voice.engine == "piper":
            self._speak_piper(sentence, voice)
        elif voice.engine == "elevenlabs":
            self._speak_elevenlabs(sentence, voice)
        else:
            raise ValueError(f"Unknown TTS engine: {voice.engine}")

    def _speak_piper(self, sentence: str, voice: VoiceProfile) -> None:
        from piper import PiperVoice  # imported lazily -- heavy dep, offline engine

        configured = os.environ.get("AETHER_PIPER_VOICE_DIR")
        voice_root = Path(configured) if configured else Path(self.sysiface.app_data_dir()) / "voices"
        model_path = voice_root / f"{voice.voice_ref}.onnx"
        if not model_path.exists():
            raise RuntimeError(
                f"Offline voice model is not installed: {model_path}. "
                "Run scripts/build_desktop.ps1 or piper.download_voices first."
            )
        import io
        import wave

        model = PiperVoice.load(str(model_path))
        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, "wb") as wav_file:
            model.synthesize_wav(sentence, wav_file)
        _play_pcm(wav_buffer.getvalue())

    def _speak_elevenlabs(self, sentence: str, voice: VoiceProfile) -> None:
        credential = self.sysiface.get_credential("elevenlabs", "api_key")
        if not credential:
            raise RuntimeError(
                "No ElevenLabs API key stored -- add one in Settings > Voice before "
                "selecting a cloud voice."
            )
        import requests

        resp = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice.voice_ref}",
            headers={"xi-api-key": credential.secret},
            json={"text": sentence},
            timeout=15,
        )
        resp.raise_for_status()
        _play_pcm(resp.content)


def _play_pcm(audio_bytes: bytes) -> None:
    """Plays raw synthesized audio. Kept as a single seam so Codex can
    swap the backend (sounddevice, simpleaudio, etc.) without touching
    anything above this line."""
    import sounddevice as sd
    import soundfile as sf
    import io

    data, samplerate = sf.read(io.BytesIO(audio_bytes))
    sd.play(data, samplerate)
    sd.wait()


# --------------------------------------------------------------------------- #
# Plugin
# --------------------------------------------------------------------------- #

class VoicePlugin(AetherPlugin):
    name = "voice"
    description = "Wake-word listening, speech-to-text, and a selectable text-to-speech voice."
    resource_cost = "medium"

    def __init__(
        self,
        tier_config: TierConfig,
        sysiface: Optional[SystemInterface] = None,
        *,
        active_voice_id: str = "amara",
        global_hotkey: str = "ctrl+alt+space",
        wakeword_model_path: Optional[str] = None,
        elevenlabs_voice_id: str = "",
        on_trigger: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.tier_config = tier_config
        self.sysiface = sysiface or get_system_interface()
        self.registry = VoiceRegistry()
        if self.registry.get(active_voice_id):
            self.registry.set_active(active_voice_id)
        cloud_profile = self.registry.get("custom-elevenlabs")
        if cloud_profile is not None:
            cloud_profile.voice_ref = elevenlabs_voice_id.strip()
        self.stt = SpeechToText(tier_config)
        self.is_listening = False
        self.tts = TextToSpeech(self.registry, self.sysiface, on_state_change=self._set_speaking)
        self.is_speaking = False
        self.global_hotkey = global_hotkey
        self.on_trigger = on_trigger or (lambda source: None)
        self._hotkey_registration: Optional[str] = None
        self._wakeword = WakeWordListener(
            tier_config.wake_word_poll_hz,
            on_detected=lambda: self._trigger("wake_word"),
            model_path=wakeword_model_path,
        )

    def _set_speaking(self, speaking: bool) -> None:
        self.is_speaking = speaking

    def on_enable(self) -> None:
        logger.info("Voice plugin enabled -- active voice: %s", self.registry.active.display_name)
        try:
            self._hotkey_registration = self.sysiface.register_global_hotkey(
                self.global_hotkey, lambda: self._trigger("hotkey")
            )
        except Exception as exc:
            logger.warning("Global hotkey unavailable: %s", exc)
        self._wakeword.start()

    def on_disable(self) -> None:
        self.is_listening = False
        self._wakeword.stop()
        if self._hotkey_registration:
            self.sysiface.unregister_global_hotkey(self._hotkey_registration)
            self._hotkey_registration = None

    def _trigger(self, source: str) -> None:
        self.on_trigger(source)

    def listen_once(self, seconds: float = 6.0) -> str:
        """Record one bounded utterance in memory and transcribe it locally."""
        import sounddevice as sd

        self.is_listening = True
        try:
            sample_rate = 16000
            audio = sd.rec(
                int(max(1.0, min(seconds, 30.0)) * sample_rate),
                samplerate=sample_rate,
                channels=1,
                dtype="float32",
            )
            sd.wait()
            return self.stt.transcribe(audio.reshape(-1))
        finally:
            self.is_listening = False

    def get_tools(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="list_voices",
                description="Lists every available TTS voice the user can pick from.",
                input_schema={"type": "object", "properties": {}},
                handler=self._tool_list_voices,
                requires_confirmation=False,
                resource_cost="light",
            ),
            ToolSpec(
                name="set_active_voice",
                description="Switches the voice Aether uses for spoken replies.",
                input_schema={
                    "type": "object",
                    "properties": {"voice_id": {"type": "string"}},
                    "required": ["voice_id"],
                },
                handler=self._tool_set_voice,
                requires_confirmation=False,
                resource_cost="light",
            ),
            ToolSpec(
                name="speak_text",
                description="Speaks a given string aloud using the currently active voice.",
                input_schema={
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
                handler=self._tool_speak,
                requires_confirmation=False,
                resource_cost="medium",
            ),
            ToolSpec(
                name="listen_for_voice_command",
                description="Listen for one short spoken command and transcribe it locally.",
                input_schema={
                    "type": "object",
                    "properties": {"seconds": {"type": "number", "minimum": 1, "maximum": 30}},
                },
                handler=self._tool_listen,
                requires_confirmation=True,
                resource_cost="medium",
            ),
        ]

    def _tool_list_voices(self, _args: dict) -> ToolResult:
        voices = self.registry.list_voices()
        summary = ", ".join(f"{v.display_name} ({v.style})" for v in voices)
        return ToolResult(ok=True, output=summary, data={"voices": [v.id for v in voices]})

    def _tool_set_voice(self, args: dict) -> ToolResult:
        try:
            self.registry.set_active(args["voice_id"])
            return ToolResult(ok=True, output=f"Voice set to {self.registry.active.display_name}.")
        except ValueError as e:
            return ToolResult(ok=False, output=str(e))

    def _tool_speak(self, args: dict) -> ToolResult:
        self.tts.speak(args["text"])
        return ToolResult(ok=True, output="Speaking.")

    def _tool_listen(self, args: dict) -> ToolResult:
        transcript = self.listen_once(float(args.get("seconds", 6)))
        return ToolResult(ok=bool(transcript), output=transcript or "No speech was detected.")
