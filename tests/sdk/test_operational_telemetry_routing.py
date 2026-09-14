"""OB-1 routing repair: semantic and operational spans use isolated processors."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest


class RecordingProcessor:
    def __init__(self) -> None:
        self.ended: list[object] = []

    def on_start(self, span, parent_context=None):  # noqa: ANN001, ANN202
        return None

    def on_end(self, span) -> None:  # noqa: ANN001
        self.ended.append(span)

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis=None):  # noqa: ANN001, ANN202
        return True


@pytest.fixture()
def isolated_providers() -> Iterator[tuple[object, RecordingProcessor, RecordingProcessor]]:
    from opentelemetry.sdk.trace import TracerProvider

    import src.sdk.observability as obs

    obs._reset_for_tests()
    semantic = TracerProvider()
    operational = TracerProvider()
    semantic_recorder = RecordingProcessor()
    operational_recorder = RecordingProcessor()
    semantic.add_span_processor(semantic_recorder)
    operational.add_span_processor(operational_recorder)
    obs._state["semantic_telemetry_provider"] = semantic
    obs._state["operational_telemetry_provider"] = operational
    yield semantic, semantic_recorder, operational_recorder
    obs._reset_for_tests()


def test_operational_span_isolated_from_semantic_processor_and_keeps_trace_id(
    isolated_providers,
):
    import src.sdk.observability as obs

    semantic, semantic_recorder, operational_recorder = isolated_providers
    with semantic.get_tracer("langfuse-sdk").start_as_current_span("semantic") as semantic_span:
        with obs.operational_telemetry_span("http.client", **{"http.request.method": "POST"}):
            pass

    assert [span.name for span in semantic_recorder.ended] == ["semantic"]
    assert [span.name for span in operational_recorder.ended] == ["http.client"]
    assert operational_recorder.ended[0].context.trace_id == semantic_span.get_span_context().trace_id


def test_configured_operational_pipeline_excludes_langfuse_and_keeps_trace_id(monkeypatch):
    """The endpoint-owning pipeline is isolated from Langfuse by construction."""
    from types import SimpleNamespace

    from opentelemetry import trace as otel_trace

    import src.sdk.observability as obs

    obs._reset_for_tests()
    delegate_spans: list[object] = []

    class Delegate:
        def export(self, spans):
            delegate_spans.extend(spans)
            return obs.SpanExportResult.SUCCESS

        def shutdown(self):
            return None

    monkeypatch.setattr(obs, "OTLPSpanExporter", lambda **kwargs: Delegate())
    monkeypatch.setattr(otel_trace, "get_tracer_provider", lambda: otel_trace.ProxyTracerProvider())
    monkeypatch.setattr(otel_trace, "set_tracer_provider", lambda provider: None)
    settings = SimpleNamespace(
        observability=SimpleNamespace(
            otel=SimpleNamespace(endpoint="http://collector.test/v1/traces", headers={})
        )
    )
    semantic = obs.configure_observability(settings)
    assert semantic is not None
    langfuse_recorder = RecordingProcessor()
    semantic.add_span_processor(langfuse_recorder)

    with semantic.get_tracer("langfuse-sdk").start_as_current_span("semantic") as semantic_span:
        with obs.operational_telemetry_span("http.client", **{"http.request.method": "POST"}):
            pass

    operational = obs._state["operational_telemetry_provider"]
    assert operational is not None
    operational.force_flush()
    assert [span.name for span in langfuse_recorder.ended] == ["semantic"]
    assert [span.name for span in delegate_spans] == ["http.client"]
    assert delegate_spans[0].context.trace_id == semantic_span.get_span_context().trace_id
    obs.shutdown_observability()
    obs._reset_for_tests()


def test_empty_endpoint_disables_http_sandbox_provider_and_audit_instrumentation(monkeypatch, tmp_path):
    """A semantic provider alone never activates operational instrumentation."""
    from opentelemetry import trace as otel_trace

    import src.sdk.observability as obs
    from src.sdk.audit import AuditEvent, AuditStore
    from src.sdk.sandbox import NullSandboxBackend, SandboxLimits

    obs._reset_for_tests()
    monkeypatch.setattr(otel_trace, "get_tracer_provider", lambda: otel_trace.ProxyTracerProvider())
    monkeypatch.setattr(otel_trace, "set_tracer_provider", lambda provider: None)
    settings = SimpleNamespace(observability=SimpleNamespace(otel=SimpleNamespace(endpoint="", headers={})))
    assert obs.configure_observability(settings) is not None
    assert obs.operational_telemetry_active() is False
    assert obs._state["operational_telemetry_provider"] is None

    class Client:
        is_closed = False

    provider = SimpleNamespace(_http_client=Client())
    original_client = provider._http_client
    obs.instrument_provider_http(provider)
    assert provider._http_client is original_client
    conn = sqlite3.connect(tmp_path / "raw.db")
    assert obs.instrument_sqlite_connection(conn) is conn

    result = NullSandboxBackend().run(
        ["python3", "-c", "print('safe')"], Path("."), SandboxLimits()
    )
    assert result.exit_code == 0
    store = AuditStore(str(tmp_path / "audit.db"))
    store.record(AuditEvent(kind="error", detail="safe"))
    assert obs._state["operational_telemetry_provider"] is None
    obs.shutdown_observability()
    obs._reset_for_tests()


def test_operational_span_is_noop_without_explicit_operational_provider():
    import src.sdk.observability as obs

    obs._reset_for_tests()
    try:
        assert obs.operational_telemetry_active() is False
        with obs.operational_telemetry_span("http.client") as span:
            assert span is None
    finally:
        obs._reset_for_tests()
