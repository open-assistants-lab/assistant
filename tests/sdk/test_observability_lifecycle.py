"""OB-0 Task 2: one provider + one Langfuse lifecycle, fail-closed degradation.

Covers:
- configure_observability is idempotent and owns provider creation exactly once
- a foreign (non-SDK) pre-existing provider degrades safely (no blind processors)
- ensure_langfuse_initialized is the single construction path and passes the
  shared provider into Langfuse exactly once
- Logger no longer constructs a Langfuse client
- shutdown flushes owned processors once, even when called repeatedly
- FastAPI lifespan configures before the first logger access and shuts down
  on exit
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest


@pytest.fixture()
def obs_reset(monkeypatch):
    """Fresh observability state + fake Langfuse for each test."""
    import src.sdk.observability as obs

    obs._reset_for_tests()
    monkeypatch.setattr(obs, "_reset_langfuse_singleton", lambda: None)

    calls: dict[str, object] = {"langfuse_init": [], "set_provider": [], "flush": 0}

    class FakeLangfuse:
        def __init__(self, **kwargs):
            calls["langfuse_init"].append(kwargs)

    import langfuse as langfuse_mod

    monkeypatch.setattr(langfuse_mod, "Langfuse", FakeLangfuse)

    import src.sdk.langfuse_tracer as lt

    monkeypatch.setattr(
        lt.LangfuseTracer,
        "flush",
        lambda *a, **k: calls.__setitem__("flush", calls["flush"] + 1),
    )

    # Never permanently consume OTel's write-once global provider slot from
    # tests: creation is recorded, not installed; reads see a fresh proxy.
    from opentelemetry import trace as otel_trace

    monkeypatch.setattr(
        otel_trace, "set_tracer_provider", calls["set_provider"].append
    )
    monkeypatch.setattr(
        otel_trace, "get_tracer_provider", lambda: otel_trace.ProxyTracerProvider()
    )

    yield obs, calls
    obs._reset_for_tests()


def _lf_settings(enabled=True, host="http://langfuse.local"):
    return SimpleNamespace(
        langfuse=SimpleNamespace(
            enabled=enabled,
            public_key="pk-test" if enabled else "",
            secret_key="sk-test" if enabled else "",
            host=host,
        )
    )


def test_configure_creates_provider_once(obs_reset, monkeypatch):
    obs, calls = obs_reset
    from opentelemetry import trace as otel_trace
    from opentelemetry.sdk.trace import TracerProvider as _SDKTracerProvider

    # Force a pristine write-once slot: earlier tests (e.g. real Langfuse
    # init) may have installed an SDK provider, which configure would adopt
    # rather than create — adoption is covered by degrade/foreign tests.
    monkeypatch.setattr(
        otel_trace, "get_tracer_provider", lambda: otel_trace.ProxyTracerProvider()
    )
    monkeypatch.setattr(
        otel_trace,
        "set_tracer_provider",
        lambda p: calls["set_provider"].append(p),
    )

    provider = obs.configure_observability(_lf_settings(enabled=False))
    again = obs.configure_observability(_lf_settings(enabled=False))

    assert provider is not None
    assert again is provider
    assert len(calls["set_provider"]) == 1
    assert isinstance(provider, _SDKTracerProvider)


def test_configure_degrades_on_foreign_provider(obs_reset, monkeypatch):
    obs, calls = obs_reset

    class ForeignProvider:
        pass  # neither ProxyTracerProvider nor SDK TracerProvider

    from opentelemetry import trace as otel_trace

    monkeypatch.setattr(otel_trace, "get_tracer_provider", lambda: ForeignProvider())
    monkeypatch.setattr(
        otel_trace, "set_tracer_provider", lambda p: calls["set_provider"].append(p)
    )

    assert obs.configure_observability(_lf_settings(enabled=False)) is None
    assert calls["set_provider"] == []


def test_ensure_langfuse_initializes_once_with_shared_provider(obs_reset):
    obs, calls = obs_reset

    provider = obs.configure_observability(_lf_settings(enabled=False))
    assert provider is not None

    assert obs.ensure_langfuse_initialized(_lf_settings()) is True
    assert obs.ensure_langfuse_initialized(_lf_settings()) is True

    assert len(calls["langfuse_init"]) == 1
    kwargs = calls["langfuse_init"][0]
    assert kwargs["tracer_provider"] is provider
    assert kwargs["base_url"] == "http://langfuse.local"


def test_ensure_langfuse_disabled_is_noop(obs_reset):
    obs, calls = obs_reset

    assert obs.ensure_langfuse_initialized(_lf_settings(enabled=False)) is False
    assert calls["langfuse_init"] == []


def test_ensure_langfuse_without_host_degrades(obs_reset):
    obs, calls = obs_reset

    assert obs.ensure_langfuse_initialized(_lf_settings(host="")) is False
    assert calls["langfuse_init"] == []


def test_logger_does_not_construct_langfuse(obs_reset, monkeypatch):
    import langfuse as langfuse_mod

    class ExplodingLangfuse:
        def __init__(self, **kwargs):
            raise AssertionError("Logger must not construct a Langfuse client")

    monkeypatch.setattr(langfuse_mod, "Langfuse", ExplodingLangfuse)

    from src.app_logging import Logger

    logger = Logger()
    assert not hasattr(logger, "langfuse")


def test_shutdown_flushes_owned_processors_once(obs_reset):
    obs, calls = obs_reset

    class FakeProcessor:
        def __init__(self):
            self.shutdowns = 0

        def shutdown(self):
            self.shutdowns += 1

    proc = FakeProcessor()
    obs._register_owned_processor(proc)

    obs.shutdown_observability()
    obs.shutdown_observability()

    assert proc.shutdowns == 1
    assert calls["flush"] == 1


@pytest.mark.asyncio
async def test_lifespan_orders_configure_before_logger(obs_reset, monkeypatch):
    obs, _ = obs_reset
    order: list[str] = []

    # Import the app module BEFORE patching so import-time logger calls do
    # not pollute the ordering record; only lifespan-internal calls count.
    import src.http.main as main_mod

    monkeypatch.setattr(
        obs,
        "configure_observability",
        lambda settings: order.append("configure"),
    )
    monkeypatch.setattr(
        obs, "shutdown_observability", lambda: order.append("shutdown")
    )

    import src.app_logging as app_logging_mod

    real_get_logger = app_logging_mod.get_logger
    monkeypatch.setattr(
        app_logging_mod,
        "get_logger",
        lambda *a, **k: (order.append("logger"), real_get_logger())[1],
    )

    async with main_mod.lifespan(app=SimpleNamespace()):
        pass

    assert order[0] == "configure"
    assert "logger" in order[1:]
    assert order[-1] == "shutdown"
