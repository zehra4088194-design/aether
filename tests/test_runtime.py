from __future__ import annotations

import threading
import time

from aether.runtime import ConfirmationBroker, EventBus


def test_pending_confirmation_survives_state_reconnect_and_resolves() -> None:
    broker = ConfirmationBroker(EventBus(), timeout_sec=2)
    result: list[bool] = []
    worker = threading.Thread(
        target=lambda: result.append(broker.request("write_file", "Write a file", {"path": "x"}))
    )
    worker.start()

    deadline = time.monotonic() + 1
    while not broker.pending() and time.monotonic() < deadline:
        time.sleep(0.01)

    snapshot = broker.pending()
    assert snapshot[0]["tool"] == "write_file"
    assert snapshot[0]["arguments"] == {"path": "x"}
    assert broker.resolve(snapshot[0]["id"], True) is True
    worker.join(timeout=1)
    assert result == [True]
    assert broker.pending() == []
