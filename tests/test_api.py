from __future__ import annotations

import os

import uvicorn
from fastapi.testclient import TestClient

import aether.api as api


class FakeRuntime:
    def state(self) -> dict:
        return {"app": {"name": "Aether"}, "privacy": {"consent_completed": False}}


def test_health_is_available_for_sidecar_readiness() -> None:
    with TestClient(api.app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["service"] == "aether"


def test_state_requires_per_launch_token(monkeypatch) -> None:
    monkeypatch.setattr(api, "_runtime", FakeRuntime())
    with TestClient(api.app) as client:
        assert client.get("/v1/state").status_code == 401
        authorized = client.get("/v1/state", headers={"X-Aether-Token": api.API_TOKEN})
    assert authorized.status_code == 200
    assert authorized.json()["app"]["name"] == "Aether"


def test_windowed_sidecar_disables_console_logging(monkeypatch) -> None:
    captured: dict = {}

    def fake_run(app, **kwargs) -> None:
        captured["app"] = app
        captured.update(kwargs)

    monkeypatch.setattr(api, "_start_parent_watchdog", lambda: captured.update(watchdog=True))
    monkeypatch.setattr(uvicorn, "run", fake_run)
    api.main()

    assert captured["app"] is api.app
    assert captured["watchdog"] is True
    assert captured["log_config"] is None
    assert captured["access_log"] is False


def test_parent_process_probe_distinguishes_live_and_missing_processes() -> None:
    assert api._parent_process_alive(os.getpid()) is True
    assert api._parent_process_alive(2_147_483_647) is False
