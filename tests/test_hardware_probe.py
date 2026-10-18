from __future__ import annotations

from aether.hardware_probe import (
    BatteryInfo,
    GPUInfo,
    HardwareProbe,
    HardwareSnapshot,
    PerformanceTier,
)


def snapshot(
    *,
    free_ram_mb: int,
    logical_cores: int,
    gpu: GPUInfo | None = None,
    battery: BatteryInfo | None = None,
) -> HardwareSnapshot:
    return HardwareSnapshot(
        total_ram_mb=max(free_ram_mb, 8192),
        free_ram_mb=free_ram_mb,
        cpu_cores_logical=logical_cores,
        cpu_cores_physical=max(1, logical_cores // 2),
        cpu_freq_mhz=3200,
        gpu=gpu or GPUInfo(),
        battery=battery or BatteryInfo(),
    )


def test_low_available_ram_is_lite() -> None:
    probe = HardwareProbe()
    assert probe._classify(snapshot(free_ram_mb=4096, logical_cores=8)) == PerformanceTier.LITE


def test_no_gpu_is_balanced_even_with_fast_cpu_and_ram() -> None:
    probe = HardwareProbe()
    snap = snapshot(free_ram_mb=32768, logical_cores=16, gpu=GPUInfo(present=False))
    assert probe._classify(snap) == PerformanceTier.BALANCED


def test_high_end_specs_are_performance() -> None:
    probe = HardwareProbe()
    snap = snapshot(
        free_ram_mb=32768,
        logical_cores=16,
        gpu=GPUInfo(present=True, name="Test GPU", vram_mb=8192),
    )
    assert probe._classify(snap) == PerformanceTier.PERFORMANCE


def test_low_battery_throttles_performance_to_balanced() -> None:
    probe = HardwareProbe()
    snap = snapshot(
        free_ram_mb=32768,
        logical_cores=16,
        gpu=GPUInfo(present=True, name="Test GPU", vram_mb=8192),
        battery=BatteryInfo(present=True, percent=19, plugged_in=False),
    )
    assert probe._classify(snap) == PerformanceTier.BALANCED


def test_low_battery_never_throttles_below_lite() -> None:
    probe = HardwareProbe()
    snap = snapshot(
        free_ram_mb=2048,
        logical_cores=2,
        battery=BatteryInfo(present=True, percent=10, plugged_in=False),
    )
    assert probe._classify(snap) == PerformanceTier.LITE


def test_low_percentage_does_not_throttle_while_plugged_in() -> None:
    probe = HardwareProbe()
    snap = snapshot(
        free_ram_mb=32768,
        logical_cores=16,
        gpu=GPUInfo(present=True, name="Test GPU", vram_mb=8192),
        battery=BatteryInfo(present=True, percent=9, plugged_in=True),
    )
    assert probe._classify(snap) == PerformanceTier.PERFORMANCE


def test_power_refresh_reclassifies_when_laptop_is_unplugged(monkeypatch) -> None:
    probe = HardwareProbe()
    probe._snapshot = snapshot(
        free_ram_mb=32768,
        logical_cores=16,
        gpu=GPUInfo(present=True, name="Test GPU", vram_mb=8192),
    )
    probe._tier = PerformanceTier.PERFORMANCE
    monkeypatch.setattr(
        probe,
        "_read_battery",
        lambda: BatteryInfo(present=True, percent=15, plugged_in=False),
    )

    assert probe.refresh_power_state() == (
        PerformanceTier.PERFORMANCE,
        PerformanceTier.BALANCED,
    )
