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
    from langfuse._client.constants import LANGFUSE_TRACER_NAME

    from src.sdk.observability import FilteringSpanExporter

    delegate = RecordingExporter()
    exporter = FilteringSpanExporter(delegate)
    # The REAL Langfuse v4 instrumentation scope (verified: 'langfuse-sdk')
    # — a stale hardcoded 'langfuse' filter must not pass this test.
    lf_span = _make_span(LANGFUSE_TRACER_NAME, {"prompt.text": "SECRET"})

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


# ---------------------------------------------------------------------------
# OB-1 Task 1: metadata boundary — events, links, resource attrs, status.


def _make_span_with_exception():
    """Real ended span carrying an exception event + error status.

    The exception message is PII-shaped on purpose: it must never survive
    export. ``error.type`` (span attribute) is allowlisted and must survive.
    """
    from opentelemetry.sdk.trace import TracerProvider as SDKTracerProvider
    from opentelemetry.trace import Status, StatusCode

    provider = SDKTracerProvider()
    tracer = provider.get_tracer("assistant.physical")
    span = tracer.start_span("tool.exec", attributes={"error.type": "TimeoutError"})
    secret_message = "email to john@firm.com not found"
    try:
        raise TimeoutError(secret_message)
    except TimeoutError as exc:
        span.record_exception(exc)
    span.set_status(Status(StatusCode.ERROR, secret_message))
    span.end()
    return span, secret_message


def test_exception_event_message_and_status_description_dropped():
    from src.sdk.observability import FilteringSpanExporter

    delegate = RecordingExporter()
    exporter = FilteringSpanExporter(delegate)
    span, secret_message = _make_span_with_exception()

    exporter.export([span])

    assert len(delegate.batches) == 1
    exported = delegate.batches[0][0]

    # Status: error code kept, human-readable description (PII carrier) gone.
    assert exported.status.status_code == span.status.status_code
    assert not exported.status.description

    # Span attribute: allowlisted error type survives.
    assert exported.attributes == {"error.type": "TimeoutError"}

    # Event attributes: nothing non-allowlisted (exception.message,
    # exception.stacktrace, exception.type) reaches the delegate.
    from src.sdk.observability import ALLOWED_PHYSICAL_ATTRIBUTES

    assert exported.events, "event itself is kept, attributes filtered"
    for event in exported.events:
        assert set(event.attributes or {}) <= set(ALLOWED_PHYSICAL_ATTRIBUTES)
        assert not any(
            "john@firm.com" in str(value)
            for value in (event.attributes or {}).values()
        )
        assert "SECRET-" not in str(event.attributes)

    # Original span untouched: filtering must never mutate the source.
    assert span.status.description == secret_message
    assert any(
        "exception.message" in (e.attributes or {}) for e in span.events
    )


def test_links_are_dropped_entirely():
    from opentelemetry.sdk.trace import TracerProvider as SDKTracerProvider
    from opentelemetry.trace import Link, SpanContext

    from src.sdk.observability import FilteringSpanExporter

    delegate = RecordingExporter()
    exporter = FilteringSpanExporter(delegate)

    provider = SDKTracerProvider()
    tracer = provider.get_tracer("assistant.physical")
    link_context = SpanContext(trace_id=0x111111, span_id=0x222222, is_remote=False)
    span = tracer.start_span(
        "tool.exec", links=[Link(link_context, {"secret.link": "SECRET-LINK"})]
    )
    span.end()

    exporter.export([span])

    exported = delegate.batches[0][0]
    assert exported.links == ()
    # Original untouched.
    assert len(span.links) == 1


def test_resource_attributes_filtered_to_allowlist():
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider as SDKTracerProvider

    from src.sdk.observability import FilteringSpanExporter

    delegate = RecordingExporter()
    exporter = FilteringSpanExporter(delegate)

    provider = SDKTracerProvider(
        resource=Resource.create(
            {
                "service.name": "assistant",
                "service.version": "0.6.5",
                "deployment.commit": "abc1234",
                "deployment.environment": "production",
                "host.name": "SECRET-HOST",
                "user.id": "SECRET-USER",
                "secret.label": "SECRET-RESOURCE",
            }
        )
    )
    tracer = provider.get_tracer("assistant.physical")
    span = tracer.start_span("http.request", attributes={"http.route": "/v1/message"})
    span.end()

    exporter.export([span])

    exported = delegate.batches[0][0]
    resource_attrs = dict(exported.resource.attributes)
    # Allowed version-baseline identity survives.
    assert resource_attrs.get("service.name") == "assistant"
    assert resource_attrs.get("service.version") == "0.6.5"
    assert resource_attrs.get("deployment.commit") == "abc1234"
    assert resource_attrs.get("deployment.environment") == "production"
    # Everything else — fingerprinting or secret-carrying — is gone.
    assert "host.name" not in resource_attrs
    assert "user.id" not in resource_attrs
    assert "secret.label" not in resource_attrs
    assert not any(
        "SECRET" in str(value) for value in resource_attrs.values()
    )
    # Original resource untouched.
    assert "host.name" in dict(span.resource.attributes)


def test_status_description_dropped_without_exception_event():
    from opentelemetry.sdk.trace import TracerProvider as SDKTracerProvider
    from opentelemetry.trace import Status, StatusCode

    from src.sdk.observability import FilteringSpanExporter

    delegate = RecordingExporter()
    exporter = FilteringSpanExporter(delegate)

    provider = SDKTracerProvider()
    tracer = provider.get_tracer("assistant.physical")
    span = tracer.start_span("http.request")
    span.set_status(Status(StatusCode.ERROR, "prompt text leaked SECRET-PROMPT"))
    span.end()

    exporter.export([span])

    exported = delegate.batches[0][0]
    assert exported.status.status_code == StatusCode.ERROR
    assert not exported.status.description
    assert span.status.description == "prompt text leaked SECRET-PROMPT"


def _settings_with_otel(endpoint: str) -> SimpleNamespace:
    return SimpleNamespace(
        langfuse=SimpleNamespace(enabled=False, public_key="", secret_key="", host=""),
        observability=SimpleNamespace(
            otel=SimpleNamespace(endpoint=endpoint, headers={})
        ),
    )


def test_exporter_built_only_with_explicit_endpoint(obs_reset, monkeypatch):
    obs = obs_reset
    from opentelemetry import trace as otel_trace

    # Never consume the real write-once global provider slot from tests:
    # reads always see a fresh proxy, writes are recorded.
    set_calls: list[object] = []
    monkeypatch.setattr(
        otel_trace, "get_tracer_provider", lambda: otel_trace.ProxyTracerProvider()
    )
    monkeypatch.setattr(otel_trace, "set_tracer_provider", set_calls.append)

    constructed: list[dict[str, object]] = []
    batch_kwargs: list[dict[str, object]] = []

    class FakeOTLPExporter:
        def __init__(self, **kwargs):
            constructed.append(kwargs)

        def export(self, spans):  # pragma: no cover - never called here
            return SpanExportResult.SUCCESS

        def shutdown(self) -> None:  # pragma: no cover
            return None

    class FakeBatchProcessor:
        def __init__(self, exporter, **kwargs):
            batch_kwargs.append(kwargs)

        def shutdown(self) -> None:  # pragma: no cover
            return None

    monkeypatch.setattr(obs, "OTLPSpanExporter", FakeOTLPExporter)
    monkeypatch.setattr(obs, "BatchSpanProcessor", FakeBatchProcessor)

    provider_off = obs.configure_observability(_settings_with_otel(""))
    assert provider_off is not None
    assert obs._state["owned_processors"] == []
    assert constructed == [] and batch_kwargs == []

    obs._reset_for_tests()
    provider_on = obs.configure_observability(
        _settings_with_otel("https://otel.admin.example")
    )
    assert provider_on is not None
    assert len(constructed) == 1
    assert len(batch_kwargs) == 1
    assert len(obs._state["owned_processors"]) == 1
    # Spec R-PERF: bounded retries — exporter timeout 5s (seconds), batch
    # processor export timeout 5000ms.
    assert constructed[0]["timeout"] == 5
    assert batch_kwargs[0]["export_timeout_millis"] == 5000
    assert set_calls, "provider must be installed exactly once per configure"
