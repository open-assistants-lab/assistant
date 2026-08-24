"""Tests for the keyed provider cache and aclose lifecycle (audit S3).

The provider factory previously rebuilt a fresh provider — and thus a fresh
httpx/AsyncOpenAI client with a cold TLS pool — on nearly every request. The
cache reuses one provider instance per (effective constructor inputs, event
loop), scoped per loop because httpx clients are bound to their creating loop.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from src.sdk.providers import factory


class FakeProvider:
    """Minimal stand-in for cache tests: counts creations via the factory."""

    def __init__(self, label: str = "default") -> None:
        self.label = label
        self.closed = False

    async def chat(self, *args: object, **kwargs: object) -> None:
        return None

    async def aclose(self) -> None:
        self.closed = True


class FakeSettings:
    """Minimal settings object for langfuse-enabled resolution tests."""

    langfuse = SimpleNamespace(
        enabled=True, public_key="pk-test", secret_key="sk-test", host="http://localhost"
    )


@pytest.fixture(autouse=True)
def _clear_caches():
    """Sync tests share one thread, so the loop-less fallback cache would
    otherwise leak cached providers across tests. The evicted-providers set
    is module-scoped and must not leak across tests either."""
    factory._thread_provider_caches.clear()
    factory._evicted_providers.clear()
    yield
    factory._thread_provider_caches.clear()
    factory._evicted_providers.clear()


@pytest.fixture
def counting_factory(monkeypatch):
    """Patch factory.create_provider to return fresh FakeProviders, counting."""
    created: list[FakeProvider] = []

    def fake_create(*args: object, **kwargs: object) -> FakeProvider:
        provider = FakeProvider()
        created.append(provider)
        return provider

    monkeypatch.setattr(factory, "create_provider", fake_create)
    return created


def test_returns_same_provider_for_same_inputs(counting_factory) -> None:
    """Identical (provider, model, key, base_url, kwargs) share one instance."""
    p1 = factory.get_cached_provider("openai", model="gpt-4o", api_key="sk-test")
    p2 = factory.get_cached_provider("openai", model="gpt-4o", api_key="sk-test")
    assert p1 is p2
    assert len(counting_factory) == 1


def test_distinguishes_api_keys(counting_factory) -> None:
    factory.get_cached_provider("openai", model="gpt-4o", api_key="sk-a")
    factory.get_cached_provider("openai", model="gpt-4o", api_key="sk-b")
    assert len(counting_factory) == 2


def test_distinguishes_models(counting_factory) -> None:
    factory.get_cached_provider("openai", model="gpt-4o", api_key="sk-test")
    factory.get_cached_provider("openai", model="gpt-4o-mini", api_key="sk-test")
    assert len(counting_factory) == 2


def test_distinguishes_base_url(counting_factory) -> None:
    factory.get_cached_provider("openai", model="gpt-4o", api_key="sk-test")
    factory.get_cached_provider(
        "openai", model="gpt-4o", api_key="sk-test", base_url="https://other.example/v1"
    )
    assert len(counting_factory) == 2


def test_distinguishes_extra_kwargs(counting_factory) -> None:
    factory.get_cached_provider("openai", model="gpt-4o", api_key="sk-test", timeout=120.0)
    factory.get_cached_provider("openai", model="gpt-4o", api_key="sk-test", timeout=300.0)
    assert len(counting_factory) == 2


def test_close_all_providers_closes_and_invalidates(counting_factory) -> None:
    p1 = factory.get_cached_provider("openai", model="gpt-4o", api_key="sk-test")
    asyncio.run(factory.close_all_providers())
    assert p1.closed is True
    p2 = factory.get_cached_provider("openai", model="gpt-4o", api_key="sk-test")
    assert p2 is not p1
    assert len(counting_factory) == 2


@pytest.mark.asyncio
async def test_lru_eviction_drops_without_closing(counting_factory) -> None:
    """Past the cap the LRU-oldest is evicted but its client is NOT closed.

    Evicted providers may still be referenced by live AgentLoops and
    concurrent streams — closing here would abort their in-flight requests.
    They are tracked for a deterministic shutdown close instead.
    """
    for i in range(factory._PROVIDER_CACHE_LIMIT + 5):
        factory.get_cached_provider("openai", model=f"model-{i}", api_key="sk-test")
    await asyncio.sleep(0)
    # Evicted but NOT closed — reusable by in-flight references.
    for p in counting_factory[:5]:
        assert p.closed is False
    # ...and tracked for the shutdown close.
    assert len(factory._evicted_providers) == 5
    for p in counting_factory[:5]:
        assert p in factory._evicted_providers
    # A surviving key still resolves to its cached instance without re-creating.
    before = len(counting_factory)
    p = factory.get_cached_provider("openai", model="model-64", api_key="sk-test")
    assert p is counting_factory[64]
    assert len(counting_factory) == before


@pytest.mark.asyncio
async def test_shutdown_closes_evicted_providers(counting_factory) -> None:
    """Evicted providers are closed exactly once, at shutdown."""
    for i in range(factory._PROVIDER_CACHE_LIMIT + 5):
        factory.get_cached_provider("openai", model=f"model-{i}", api_key="sk-test")
    await asyncio.sleep(0)
    assert len(factory._evicted_providers) == 5

    await factory.close_all_providers()
    assert all(p.closed for p in counting_factory[:5])
    assert len(factory._evicted_providers) == 0


@pytest.mark.asyncio
async def test_langfuse_wrap_is_idempotent_across_cached_calls(monkeypatch) -> None:
    """A cached provider is langfuse-wrapped exactly once.

    LangfuseTracer.wrap_provider mutates provider.chat in place (no proxy),
    so wrapping the same cached instance on every call would stack nested
    traced_chat closures — one generation span per layer per request.
    """
    wraps: list[object] = []

    def fake_wrap(provider: object) -> object:
        # Mirrors the real wrap_provider: in-place mutation, no guard.
        wraps.append(provider)
        original_chat = provider.chat

        async def traced(*a: object, **k: object) -> object:
            return await original_chat(*a, **k)

        provider.chat = traced  # type: ignore[attr-defined]
        return provider

    def fake_create(*args: object, **kwargs: object) -> FakeProvider:
        return FakeProvider()

    monkeypatch.setattr("src.config.get_settings", lambda: FakeSettings())
    monkeypatch.setattr(
        "src.sdk.langfuse_tracer.LangfuseTracer.is_enabled", classmethod(lambda cls: True)
    )
    monkeypatch.setattr(
        "src.sdk.langfuse_tracer.LangfuseTracer.wrap_provider", staticmethod(fake_wrap)
    )
    monkeypatch.setattr(factory, "create_provider", fake_create)
    monkeypatch.setattr(factory, "_resolve_registry_provider", lambda *a, **k: None)
    monkeypatch.setattr(
        factory,
        "_load_user_settings",
        lambda user_id: type("S", (), {"default_model": None, "provider_keys": {}})(),
    )

    p1 = factory.get_cached_model_provider("openai:gpt-4o")
    chat_after_first_wrap = p1.chat
    p2 = factory.get_cached_model_provider("openai:gpt-4o")

    assert p1 is p2
    assert len(wraps) == 1  # wrapped exactly once, not once per call
    assert p2.chat is chat_after_first_wrap  # no stacking
    assert p1._langfuse_wrapped is True



@pytest.mark.asyncio
async def test_get_cached_model_provider_caches_across_calls(monkeypatch) -> None:
    """Model-level resolver shares instances for identical effective inputs."""
    created: list[object] = []

    def fake_create(*args: object, **kwargs: object) -> FakeProvider:
        provider = FakeProvider()
        created.append(provider)
        return provider

    monkeypatch.setattr(factory, "create_provider", fake_create)
    # Deterministic: no registry resolution, no stored settings.
    monkeypatch.setattr(factory, "_resolve_registry_provider", lambda *a, **k: None)
    monkeypatch.setattr(
        factory,
        "_load_user_settings",
        lambda user_id: type("S", (), {"default_model": None, "provider_keys": {}})(),
    )

    p1 = factory.get_cached_model_provider("openai:gpt-4o")
    p2 = factory.get_cached_model_provider("openai:gpt-4o")
    assert p1 is p2
    assert len(created) == 1


@pytest.mark.asyncio
async def test_openai_aclose_closes_client() -> None:
    from unittest.mock import AsyncMock
    from unittest.mock import patch as _patch

    from src.sdk.providers.openai import OpenAIProvider

    provider = OpenAIProvider(api_key="sk-test", model="gpt-4o")
    with _patch.object(provider._client, "close", new=AsyncMock()) as close:
        await provider.aclose()
    close.assert_awaited_once()


@pytest.mark.asyncio
async def test_lazy_client_providers_aclose_created_client() -> None:
    from src.sdk.providers.anthropic import AnthropicProvider
    from src.sdk.providers.gemini import GeminiProvider
    from src.sdk.providers.ollama import OllamaCloud

    providers = [
        AnthropicProvider(api_key="sk-test"),
        GeminiProvider(api_key="sk-test"),
        OllamaCloud(api_key="sk-test"),
    ]
    for provider in providers:
        client = provider._get_client()
        assert not client.is_closed
        await provider.aclose()
        assert client.is_closed
        assert provider._http_client is None


@pytest.mark.asyncio
async def test_lazy_client_aclose_noop_when_never_created() -> None:
    from src.sdk.providers.anthropic import AnthropicProvider

    provider = AnthropicProvider(api_key="sk-test")
    await provider.aclose()  # must not raise with no client
    assert provider._http_client is None
