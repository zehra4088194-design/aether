from __future__ import annotations

from dataclasses import dataclass

import requests

from aether.license_manager import LicenseManager


@dataclass
class StoredCredential:
    service: str
    username: str
    secret: str


class FakeSystem:
    def __init__(self, device_id: str = "a" * 64) -> None:
        self.device_id = device_id
        self.credentials: dict[tuple[str, str], str] = {}

    def device_fingerprint(self) -> str:
        return self.device_id

    def store_credential(self, service: str, username: str, secret: str) -> None:
        self.credentials[(service, username)] = secret

    def get_credential(self, service: str, username: str):
        secret = self.credentials.get((service, username))
        return StoredCredential(service, username, secret) if secret is not None else None

    def delete_credential(self, service: str, username: str) -> bool:
        return self.credentials.pop((service, username), None) is not None


def test_first_activation_is_saved_and_allows_offline_start(monkeypatch) -> None:
    system = FakeSystem()
    manager = LicenseManager(sysiface=system, endpoint="https://licenses.example", required=True)
    monkeypatch.setattr(
        manager,
        "_post",
        lambda path, payload: {"valid": True, "activation_id": "activation-1"},
    )

    activated = manager.activate("AETH-1111-2222-3333-4444")
    assert activated.valid is True
    assert manager.local_status().valid is True

    def offline(*_args, **_kwargs):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(manager, "_post", offline)
    heartbeat = manager.heartbeat()
    assert heartbeat.valid is True
    assert heartbeat.online_check is False


def test_saved_activation_cannot_move_to_another_device(monkeypatch) -> None:
    system = FakeSystem()
    manager = LicenseManager(sysiface=system, endpoint="https://licenses.example", required=True)
    monkeypatch.setattr(
        manager,
        "_post",
        lambda path, payload: {"valid": True, "activation_id": "activation-1"},
    )
    manager.activate("AETH-1111-2222-3333-4444")

    system.device_id = "b" * 64
    assert manager.local_status().valid is False


def test_authoritative_rejection_revokes_local_activation(monkeypatch) -> None:
    system = FakeSystem()
    manager = LicenseManager(sysiface=system, endpoint="https://licenses.example", required=True)
    responses = iter(
        [
            {"valid": True, "activation_id": "activation-1"},
            {"valid": False, "message": "License disabled."},
        ]
    )
    monkeypatch.setattr(manager, "_post", lambda path, payload: next(responses))
    manager.activate("AETH-1111-2222-3333-4444")

    rejected = manager.heartbeat()
    assert rejected.valid is False
    assert manager.local_status().valid is False


def test_online_deactivation_releases_seat_before_removing_local_record(monkeypatch) -> None:
    system = FakeSystem()
    manager = LicenseManager(sysiface=system, endpoint="https://licenses.example", required=True)
    responses = iter(
        [
            {"valid": True, "activation_id": "activation-1"},
            {"deactivated": True},
        ]
    )
    monkeypatch.setattr(manager, "_post", lambda path, payload: next(responses))
    manager.activate("AETH-1111-2222-3333-4444")

    removed = manager.deactivate()
    assert removed.valid is False
    assert system.credentials == {}
