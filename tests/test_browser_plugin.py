from __future__ import annotations

from aether.plugins.browser_automation_plugin import BrowserAutomationPlugin


def test_read_only_browser_rejects_local_and_credential_urls(monkeypatch) -> None:
    monkeypatch.setattr(
        "aether.plugins.browser_automation_plugin.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(None, None, None, None, ("127.0.0.1", 0))],
    )
    assert BrowserAutomationPlugin._public_url_error("http://localhost/admin") is not None
    assert BrowserAutomationPlugin._public_url_error("https://user:pass@example.com") is not None


def test_read_only_browser_accepts_global_http_address(monkeypatch) -> None:
    monkeypatch.setattr(
        "aether.plugins.browser_automation_plugin.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(None, None, None, None, ("93.184.216.34", 0))],
    )
    assert BrowserAutomationPlugin._public_url_error("https://example.com/article") is None
