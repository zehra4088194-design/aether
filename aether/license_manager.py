"""Device-bound first-run licensing with offline starts and online heartbeats."""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Optional

import requests

from aether.system_interface import SystemInterface, get_system_interface


LICENSE_SERVICE = "desktop-license"
LICENSE_USERNAME = "activation"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _masked(key: str) -> str:
    clean = key.strip()
    if len(clean) < 9:
        return "••••"
    return f"{clean[:4]}-••••-{clean[-4:]}"


@dataclass
class LicenseStatus:
    required: bool
    configured: bool
    activated: bool
    valid: bool
    device_code: str
    key_masked: str = ""
    last_verified_at: Optional[str] = None
    online_check: Optional[bool] = None
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class LicenseManager:
    """Owns the local activation receipt and Cloudflare verification calls.

    A successful first activation is stored in Windows Credential Manager,
    whose payload is protected by Windows DPAPI. Subsequent starts trust that
    local receipt only when it still matches the current machine fingerprint.
    Network failures never remove a valid local activation; an authoritative
    invalid response does.
    """

    def __init__(
        self,
        *,
        sysiface: Optional[SystemInterface] = None,
        endpoint: Optional[str] = None,
        required: Optional[bool] = None,
        timeout_sec: float = 6.0,
    ) -> None:
        self.sysiface = sysiface or get_system_interface()
        self.endpoint = (endpoint if endpoint is not None else os.environ.get("AETHER_LICENSE_URL", "")).rstrip("/")
        if required is None:
            required = os.environ.get("AETHER_LICENSE_REQUIRED", "0").strip().lower() in {
                "1", "true", "yes", "on"
            }
        self.required = bool(required)
        self.timeout_sec = timeout_sec
        self._lock = threading.RLock()

    @property
    def device_id(self) -> str:
        return self.sysiface.device_fingerprint()

    def _load(self) -> Optional[dict[str, Any]]:
        credential = self.sysiface.get_credential(LICENSE_SERVICE, LICENSE_USERNAME)
        if credential is None:
            return None
        try:
            record = json.loads(credential.secret)
        except (TypeError, json.JSONDecodeError):
            return None
        return record if isinstance(record, dict) else None

    def _store(self, record: dict[str, Any]) -> None:
        self.sysiface.store_credential(
            LICENSE_SERVICE,
            LICENSE_USERNAME,
            json.dumps(record, separators=(",", ":")),
        )

    def local_status(self) -> LicenseStatus:
        device_id = self.device_id
        configured = bool(self.endpoint)
        if not self.required:
            return LicenseStatus(
                required=False,
                configured=configured,
                activated=True,
                valid=True,
                device_code=device_id[:12].upper(),
                message="Licensing is disabled for this build.",
            )
        record = self._load()
        valid = bool(
            record
            and record.get("key")
            and record.get("device_id") == device_id
            and not record.get("revoked")
        )
        return LicenseStatus(
            required=True,
            configured=configured,
            activated=valid,
            valid=valid,
            device_code=device_id[:12].upper(),
            key_masked=_masked(str(record.get("key", ""))) if record else "",
            last_verified_at=str(record.get("last_verified_at")) if record and record.get("last_verified_at") else None,
            message=(
                "Activated on this PC. Offline startup is available."
                if valid
                else "Enter a valid Aether key while connected to the internet."
            ),
        )

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.endpoint:
            raise RuntimeError("The license server is not configured in this build.")
        response = requests.post(
            f"{self.endpoint}{path}",
            json=payload,
            headers={"Accept": "application/json", "User-Agent": "Aether-Desktop/0.1"},
            timeout=self.timeout_sec,
        )
        try:
            body = response.json()
        except ValueError as exc:
            raise RuntimeError("The license server returned an unreadable response.") from exc
        if not response.ok:
            raise RuntimeError(str(body.get("message") or "License verification failed."))
        return body

    def activate(self, key: str) -> LicenseStatus:
        key = key.strip().upper()
        if len(key) < 16 or len(key) > 120:
            raise ValueError("Enter the complete Aether license key.")
        with self._lock:
            body = self._post("/v1/activate", {"license_key": key, "device_id": self.device_id})
            if not body.get("valid"):
                raise ValueError(str(body.get("message") or "This license key is not valid."))
            now = _utc_now()
            self._store(
                {
                    "key": key,
                    "device_id": self.device_id,
                    "activation_id": body.get("activation_id"),
                    "activated_at": now,
                    "last_verified_at": now,
                    "revoked": False,
                }
            )
        status = self.local_status()
        status.online_check = True
        status.message = "Aether is activated on this PC."
        return status

    def heartbeat(self) -> LicenseStatus:
        """Verify opportunistically; retain the local receipt when offline."""
        with self._lock:
            status = self.local_status()
            record = self._load()
            if not self.required or not status.valid or record is None:
                return status
            try:
                body = self._post(
                    "/v1/verify",
                    {"license_key": record["key"], "device_id": self.device_id},
                )
            except (requests.RequestException, RuntimeError):
                status.online_check = False
                status.message = "Offline: the saved activation remains attached to this PC."
                return status
            if not body.get("valid"):
                record["revoked"] = True
                self._store(record)
                invalid = self.local_status()
                invalid.online_check = True
                invalid.message = str(body.get("message") or "This activation is no longer valid.")
                return invalid
            record["last_verified_at"] = _utc_now()
            self._store(record)
            verified = self.local_status()
            verified.online_check = True
            verified.message = "Activation confirmed silently."
            return verified

    def deactivate(self) -> LicenseStatus:
        """Release the server-side device seat, then remove the local receipt."""
        with self._lock:
            record = self._load()
            if record is None:
                return self.local_status()
            body = self._post(
                "/v1/deactivate",
                {"license_key": record.get("key"), "device_id": self.device_id},
            )
            if not body.get("deactivated"):
                raise RuntimeError(str(body.get("message") or "Could not remove this activation."))
            self.sysiface.delete_credential(LICENSE_SERVICE, LICENSE_USERNAME)
        status = self.local_status()
        status.online_check = True
        status.message = "License removed. This key can now activate another PC."
        return status
