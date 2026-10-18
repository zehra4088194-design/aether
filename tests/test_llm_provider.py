from __future__ import annotations

from aether.hardware_probe import PerformanceTier, TIER_DEFAULTS
from aether.llm_provider import LocalProvider, LLMProvider, LLMProviderError, LLMResponse, ManagedLLM


class FailingProvider(LLMProvider):
    model = "failure-test"

    def __init__(self) -> None:
        self.calls = 0

    def is_available(self) -> bool:
        return True

    def chat(self, *args, **kwargs) -> LLMResponse:
        self.calls += 1
        raise LLMProviderError("planned failure")


class WorkingProvider(LLMProvider):
    model = "working-test"

    def is_available(self) -> bool:
        return True

    def chat(self, *args, **kwargs) -> LLMResponse:
        return LLMResponse(text="fallback", input_tokens=100, output_tokens=20)


def test_circuit_breaker_cools_provider_after_three_failures() -> None:
    preferred = FailingProvider()
    fallback = WorkingProvider()
    states: list[dict] = []
    llm = ManagedLLM(
        preferred,
        fallback,
        failure_threshold=3,
        cooldown_sec=60,
        on_provider_state=states.append,
    )

    assert [llm.chat(messages=[]).text for _ in range(4)] == ["fallback"] * 4
    assert preferred.calls == 3
    assert states[-1]["state"] == "cooling_down"
    assert llm.provider_states()[0]["cooling_down"] is True


def test_session_usage_tracks_tokens_and_cloud_estimate() -> None:
    llm = ManagedLLM(WorkingProvider())
    llm.chat(messages=[])
    usage = llm.usage
    assert usage.input_tokens == 100
    assert usage.output_tokens == 20
    assert usage.estimated_cost_usd > 0


def test_local_provider_checks_the_configured_ollama_host(monkeypatch) -> None:
    requested: list[str] = []

    class Response:
        status_code = 200

    monkeypatch.setattr(
        "aether.llm_provider.requests.get",
        lambda url, **_kwargs: requested.append(url) or Response(),
    )
    provider = LocalProvider(TIER_DEFAULTS[PerformanceTier.BALANCED], "qwen3:4b")
    provider.OLLAMA_URL = "http://ollama.lan:11434/api/chat"

    assert provider.is_available() is True
    assert requested == ["http://ollama.lan:11434/api/tags"]
