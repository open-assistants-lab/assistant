"""OB-0 Task 3: filtered admin OTLP export.

The admin exporter must:
- export physical spans only — Langfuse instrumentation-scope spans are
  dropped (their prompt/completion content belongs to the admin's Langfuse,
  never to the physical layer);
- allowlist span attributes — unknown or content-carrying attributes never
  reach the delegate (exporter-side filtering: the immutable ReadableSpan is
  never mutated; a filtered copy is built instead);
- preserve the shared trace ID on allowed spans (the join key to Langfuse);
- swallow delegate failures (never raise into the span path) and keep working;
- be constructed ONLY when the admin endpoint is explicitly configured.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from opentelemetry.sdk.trace.export import SpanExportResult


class RecordingExporter:
    """In-memory delegate: records exported spans, can be told to fail."""

    def __init__(self) -> None:
        self.batches: list[list[object]] = []
        self.fail = False

    def export(self, spans):  # noqa: ANN001, ANN202 - test double
        if self.fail:
            raise RuntimeError("delegate boom")
        self.batches.append(list(spans))
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:  # noqa: ANN001 - delegate contract
        return None


def _make_span(scope_name: str, attributes: dict[str, object]):
    """Build a real (ended) SDK Span — a ReadableSpan — under a named scope.

    Uses a private SDK TracerProvider; the process-global slot is untouched.
    """
    from opentelemetry.sdk.trace import TracerProvider as SDKTracerProvider

    provider = SDKTracerProvider()
    tracer = provider.get_tracer(scope_name)
    span = tracer.start_span("test.span", attributes=attributes)
    span.end()
    return span


@pytest.fixture()
def obs_reset():
    import src.sdk.observability as obs

    obs._reset_for_tests()
    yield obs
    obs._reset_for_tests()


def test_physical_span_exported_with_trace_id_and_allowed_attrs():
    from src.sdk.observability import FilteringSpanExporter

    delegate = RecordingExporter()
    exporter = FilteringSpanExporter(delegate)
    span = _make_span(
        "assistant.physical",
        {"http.request.method": "GET", "prompt.text": "SECRET-PROMPT"},
    )

    result = exporter.export([span])

    assert result is SpanExportResult.SUCCESS
    assert len(delegate.batches) == 1
    exported = delegate.batches[0][0]
    assert exported.context.trace_id == span.context.trace_id
    assert exported.attributes == {"http.request.method": "GET"}


def test_langfuse_scope_span_is_dropped():
    from src.sdk.observability import FilteringSpanExporter

    delegate = RecordingExporter()
    exporter = FilteringSpanExporter(delegate)
    lf_span = _make_span("langfuse", {"prompt.text": "SECRET"})

    result = exporter.export([lf_span])

    assert result is SpanExportResult.SUCCESS
    assert delegate.batches == []


def test_content_marker_attributes_never_reach_delegate():
    from src.sdk.observability import FilteringSpanExporter

    delegate = RecordingExporter()
    exporter = FilteringSpanExporter(delegate)
    span = _make_span(
        "assistant.physical",
        {
            "prompt.text": "SECRET-PROMPT",
            "tool.args": "SECRET-ARGS",
            "tool.result": "SECRET-RESULT",
            "unknown.attr": "SECRET-UNKNOWN",
            "http.route": "/v1/message",
        },
    )

    exporter.export([span])

    assert len(delegate.batches) == 1
    assert delegate.batches[0][0].attributes == {"http.route": "/v1/message"}


def test_delegate_failure_is_swallowed_and_exporter_keeps_working():
    from src.sdk.observability import FilteringSpanExporter

    delegate = RecordingExporter()
    exporter = FilteringSpanExporter(delegate)
    span = _make_span("assistant.physical", {"http.request.method": "GET"})

    delegate.fail = True
    failed = exporter.export([span])
    assert failed is SpanExportResult.FAILURE  # recorded, never raised

    delegate.fail = False
    ok = exporter.export([span])
    assert ok is SpanExportResult.SUCCESS
    assert len(delegate.batches) == 1


def test_mixed_batch_drops_langfuse_keeps_physical():
    from src.sdk.observability import FilteringSpanExporter

    delegate = RecordingExporter()
    exporter = FilteringSpanExporter(delegate)
    physical = _make_span("assistant.physical", {"http.route": "/health"})
    lf_gen = _make_span("langfuse", {"prompt.text": "SECRET"})

    exporter.export([lf_gen, physical])

    assert len(delegate.batches) == 1
    assert len(delegate.batches[0]) == 1
    assert delegate.batches[0][0].attributes == {"http.route": "/health"}


def _settings_with_otel(endpoint: str) -> SimpleNamespace:
    return SimpleNamespace(
        langfuse=SimpleNamespace(enabled=False, public_key="", secret_key="", host=""),
        observability=SimpleNamespace(
            otel=SimpleNamespace(endpoint=endpoint, headers={})
        ),
    )


def test_exporter_built_only_with_explicit_endpoint(obs_reset, monkeypatch):
    obs = obs_reset
    constructed: list[object] = []

    class FakeOTLPExporter:
        def __init__(self, **kwargs):
            constructed.append(kwargs)

        def export(self, spans):  # pragma: no cover - never called here
            return SpanExportResult.SUCCESS

        def shutdown(self) -> None:  # pragma: no cover
            return None

    monkeypatch.setattr(obs, "OTLPSpanExporter", FakeOTLPExporter)

    provider_off = obs.configure_observability(_settings_with_otel(""))
    assert provider_off is not None
    assert obs._state["owned_processors"] == []

    obs._reset_for_tests()
    provider_on = obs.configure_observability(_settings_with_otel("https://otel.admin.example"))
    assert provider_on is not None
    assert len(constructed) == 1
    assert len(obs._state["owned_processors"]) == 1
