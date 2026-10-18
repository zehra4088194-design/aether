"""
Aether - Hardware Probe & Adaptive Performance Engine
========================================================
Module 2 of 5

Purpose
-------
Detects the host machine's capability on first launch (and periodically
at runtime), classifies it into a PerformanceTier, and exposes a
TierConfig with concrete numeric/boolean settings every other module
(LLMProvider, voice plugin, memory store, UI) should read from — instead
of each module inventing its own thresholds.

Also runs a lightweight background monitor thread that watches system
load and triggers a "throttle" callback if something else on the machine
starts hogging resources — the "good citizen" behavior from the brief.
"""

from __future__ import annotations

import enum
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import psutil

try:
    import pynvml

    pynvml.nvmlInit()
    _NVML_AVAILABLE = True
except Exception:
    _NVML_AVAILABLE = False


# --------------------------------------------------------------------------- #
# Tiers
# --------------------------------------------------------------------------- #

class PerformanceTier(str, enum.Enum):
    LITE = "lite"
    BALANCED = "balanced"
    PERFORMANCE = "performance"


@dataclass
class GPUInfo:
    present: bool = False
    name: str = ""
    vram_mb: int = 0


@dataclass
class BatteryInfo:
    """Portable battery state used by the tier classifier.

    ``present`` is explicit because desktop machines normally return no
    battery object at all.  Keeping this in the hardware snapshot means
    every downstream component sees the same power-aware decision instead
    of inventing its own battery thresholds.
    """

    present: bool = False
    percent: float = 100.0
    plugged_in: bool = True


@dataclass
class HardwareSnapshot:
    total_ram_mb: int
    free_ram_mb: int
    cpu_cores_logical: int
    cpu_cores_physical: int
    cpu_freq_mhz: float
    gpu: GPUInfo
    battery: BatteryInfo = field(default_factory=BatteryInfo)


@dataclass
class TierConfig:
    """Concrete settings derived from a tier. Every module should read
    from this object rather than re-deriving its own thresholds."""

    tier: PerformanceTier
    allow_local_llm: bool
    local_llm_max_params_b: float          # e.g. 0 (disabled), 7, 13+
    whisper_model_size: str                # "tiny" | "base" | "small" | "medium"
    wake_word_poll_hz: float               # how often the wake-word engine samples audio
    vector_memory_max_items: int
    continuous_vision_enabled: bool
    ui_animations_enabled: bool
    background_poll_interval_sec: int      # proactive-engine / scheduler tick rate
    proactive_mode_default_on: bool


TIER_DEFAULTS: dict[PerformanceTier, TierConfig] = {
    PerformanceTier.LITE: TierConfig(
        tier=PerformanceTier.LITE,
        allow_local_llm=False,
        local_llm_max_params_b=0,
        whisper_model_size="tiny",
        wake_word_poll_hz=5.0,
        vector_memory_max_items=2_000,
        continuous_vision_enabled=False,
        ui_animations_enabled=False,
        background_poll_interval_sec=300,
        proactive_mode_default_on=False,
    ),
    PerformanceTier.BALANCED: TierConfig(
        tier=PerformanceTier.BALANCED,
        allow_local_llm=True,
        local_llm_max_params_b=7,
        whisper_model_size="base",
        wake_word_poll_hz=10.0,
        vector_memory_max_items=20_000,
        continuous_vision_enabled=False,
        ui_animations_enabled=True,
        background_poll_interval_sec=60,
        proactive_mode_default_on=False,
    ),
    PerformanceTier.PERFORMANCE: TierConfig(
        tier=PerformanceTier.PERFORMANCE,
        allow_local_llm=True,
        local_llm_max_params_b=13,
        whisper_model_size="small",
        wake_word_poll_hz=20.0,
        vector_memory_max_items=100_000,
        continuous_vision_enabled=True,
        ui_animations_enabled=True,
        background_poll_interval_sec=15,
        proactive_mode_default_on=True,
    ),
}


# --------------------------------------------------------------------------- #
# Probe
# --------------------------------------------------------------------------- #

class HardwareProbe:
    """Run once at startup: `probe.detect()`. Then call
    `probe.start_monitoring(on_throttle=...)` to keep watching for
    resource contention from other apps."""

    # Thresholds — tunable, but keep in one place.
    LITE_RAM_THRESHOLD_MB = 4_096
    PERFORMANCE_RAM_THRESHOLD_MB = 16_384
    PERFORMANCE_MIN_CORES = 8
    LOW_BATTERY_THRESHOLD_PERCENT = 30.0

    def __init__(self) -> None:
        self._snapshot: Optional[HardwareSnapshot] = None
        self._tier: Optional[PerformanceTier] = None
        self._manual_override: Optional[PerformanceTier] = None
        self._monitor_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    # ---- Detection ---------------------------------------------------------------
    def _read_gpu(self) -> GPUInfo:
        if _NVML_AVAILABLE:
            try:
                handle = pynvml.nvmlDeviceGetHandleByIndex(0)
                name = pynvml.nvmlDeviceGetName(handle)
                name = name.decode() if isinstance(name, bytes) else name
                mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
                return GPUInfo(present=True, name=name, vram_mb=int(mem.total / (1024 * 1024)))
            except Exception:
                pass
        # Fallback: try torch.cuda if pynvml unavailable (e.g. non-NVIDIA setups)
        try:
            import torch

            if torch.cuda.is_available():
                props = torch.cuda.get_device_properties(0)
                return GPUInfo(
                    present=True,
                    name=props.name,
                    vram_mb=int(props.total_memory / (1024 * 1024)),
                )
        except Exception:
            pass
        return GPUInfo(present=False)

    def _read_battery(self) -> BatteryInfo:
        try:
            battery = psutil.sensors_battery()
        except (AttributeError, NotImplementedError):
            battery = None
        if battery is None:
            return BatteryInfo()
        return BatteryInfo(
            present=True,
            percent=float(battery.percent),
            plugged_in=bool(battery.power_plugged),
        )

    def detect(self) -> HardwareSnapshot:
        vm = psutil.virtual_memory()
        freq = psutil.cpu_freq()
        snap = HardwareSnapshot(
            total_ram_mb=int(vm.total / (1024 * 1024)),
            free_ram_mb=int(vm.available / (1024 * 1024)),
            cpu_cores_logical=psutil.cpu_count(logical=True) or 1,
            cpu_cores_physical=psutil.cpu_count(logical=False) or 1,
            cpu_freq_mhz=freq.max if freq and freq.max else (freq.current if freq else 0.0),
            gpu=self._read_gpu(),
            battery=self._read_battery(),
        )
        self._snapshot = snap
        self._tier = self._classify(snap)
        return snap

    def _classify(self, snap: HardwareSnapshot) -> PerformanceTier:
        base_tier: PerformanceTier
        if (
            snap.free_ram_mb >= self.PERFORMANCE_RAM_THRESHOLD_MB
            and snap.cpu_cores_logical >= self.PERFORMANCE_MIN_CORES
            and snap.gpu.present
            and snap.gpu.vram_mb >= 4_096
        ):
            base_tier = PerformanceTier.PERFORMANCE
        elif snap.free_ram_mb <= self.LITE_RAM_THRESHOLD_MB or snap.cpu_cores_logical < 4:
            base_tier = PerformanceTier.LITE
        else:
            base_tier = PerformanceTier.BALANCED

        on_low_battery = (
            snap.battery.present
            and not snap.battery.plugged_in
            and snap.battery.percent < self.LOW_BATTERY_THRESHOLD_PERCENT
        )
        if not on_low_battery:
            return base_tier
        if base_tier == PerformanceTier.PERFORMANCE:
            return PerformanceTier.BALANCED
        return PerformanceTier.LITE

    # ---- Public accessors -----------------------------------------------------------
    @property
    def snapshot(self) -> HardwareSnapshot:
        if self._snapshot is None:
            self.detect()
        return self._snapshot  # type: ignore[return-value]

    @property
    def tier(self) -> PerformanceTier:
        if self._manual_override is not None:
            return self._manual_override
        if self._tier is None:
            self.detect()
        return self._tier  # type: ignore[return-value]

    @property
    def config(self) -> TierConfig:
        return TIER_DEFAULTS[self.tier]

    def set_manual_tier(self, tier: Optional[PerformanceTier]) -> None:
        """User override from settings UI. Pass None to revert to auto-detect."""
        self._manual_override = tier

    def refresh_power_state(self) -> tuple[PerformanceTier, PerformanceTier]:
        """Refresh battery state and reclassify the automatic tier.

        The hardware shape remains the startup snapshot, while power can change
        at any moment as a laptop is unplugged or charged.
        """
        snap = self.snapshot
        previous = self.tier
        snap.battery = self._read_battery()
        if self._manual_override is None:
            self._tier = self._classify(snap)
        return previous, self.tier

    # ---- Runtime "good citizen" monitoring -------------------------------------------
    def start_monitoring(
        self,
        on_throttle: Callable[[bool], None],
        cpu_high_watermark: float = 85.0,
        ram_high_watermark: float = 90.0,
        check_interval_sec: float = 5.0,
        on_tier_change: Optional[Callable[[PerformanceTier, PerformanceTier], None]] = None,
    ) -> None:
        """Runs a daemon thread that samples *system-wide* CPU/RAM usage
        (not just Aether's own usage). If other processes push the system
        above the watermark, `on_throttle(True)` fires so callers can pause
        background tasks. When load drops back down, `on_throttle(False)`
        fires so normal operation can resume."""
        if self._monitor_thread and self._monitor_thread.is_alive():
            return

        self._stop_event.clear()

        def _loop():
            throttled = False
            while not self._stop_event.is_set():
                cpu = psutil.cpu_percent(interval=1.0)
                ram = psutil.virtual_memory().percent
                should_throttle = cpu >= cpu_high_watermark or ram >= ram_high_watermark
                if should_throttle != throttled:
                    throttled = should_throttle
                    on_throttle(throttled)
                previous_tier, current_tier = self.refresh_power_state()
                if on_tier_change and previous_tier != current_tier:
                    on_tier_change(previous_tier, current_tier)
                self._stop_event.wait(check_interval_sec)

        self._monitor_thread = threading.Thread(target=_loop, daemon=True, name="AetherHWMonitor")
        self._monitor_thread.start()

    def stop_monitoring(self) -> None:
        self._stop_event.set()
        if self._monitor_thread:
            self._monitor_thread.join(timeout=2)


# --------------------------------------------------------------------------- #
# Manual smoke test: `python -m aether.hardware_probe`
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    probe = HardwareProbe()
    snap = probe.detect()
    print("Hardware snapshot:", snap)
    print("Detected tier:", probe.tier)
    print("Tier config:", probe.config)

    def _on_throttle(active: bool):
        print(f"[Aether] Throttle {'ENGAGED' if active else 'released'}")

    probe.start_monitoring(_on_throttle)
    try:
        time.sleep(15)
    finally:
        probe.stop_monitoring()
