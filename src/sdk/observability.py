"""OB-0 observability foundation: one OpenTelemetry lifecycle owner.

This module owns the process-global ``TracerProvider`` and is the single
Langfuse initialization path. Design goals (spec:
``docs/superpowers/specs/2026-09-12-observability-foundation-design.md``):

- ``configure_observability`` is idempotent, called from the FastAPI lifespan
  BEFORE any logger/Langfuse construction.
- If OTel still holds its write-once slot (``ProxyTracerProvider``), we create
  an SDK provider. If an SDK provider already exists we adopt it. Any foreign
  pre-existing provider degrades to "no export" — we never blindly call
  ``add_span_processor`` on it (spec: fail-safe, ordering-independent).
- ``ensure_langfuse_initialized`` is the ONLY place a Langfuse client is
  constructed; it passes the shared provider in so trace IDs are shared.
- ``shutdown_observability`` flushes/shuts down only processors registered
  with THIS module (Task 3 adds the filtered operational exporter), plus the
  Langfuse client flush, exactly once.
- Disabled/unconfigured observability performs zero outbound requests: no
  exporter exists until Task 3 wires an explicit operational endpoint.

Vendor telemetry (consent tiers, vendor endpoints) is explicitly out of scope.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

# opentelemetry-sdk and the OTLP HTTP exporter are direct runtime
# dependencies (declared in pyproject.toml alongside langfuse, which itself
# requires the OTel SDK) — no guarded fallback: an import failure here should
# fail loudly at startup, not degrade into a broken half-observability state.
from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
    OTLPSpanExporter,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import Event, ReadableSpan
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.trace import Status

from src.config import AppConfig

logger = logging.getLogger("src.sdk.observability")

# Operational-layer attribute allowlist (spec §Destination routing). Only these
# keys survive to the operational OTLP destination; everything else — prompts,
# tool arguments/results, message content, unknown instrumentation attrs —
# is dropped at the exporter. Extend deliberately, never wholesale.
#
# Deferred to OB-1 (deliberately NOT here): filtering span *events*,
# *links*, and per-destination *resource* attributes. OB-0 ships the
# attribute-level boundary only; those surfaces need their own red/green
# coverage when the operational spans that populate them exist.
ALLOWED_OPERATIONAL_ATTRIBUTES = frozenset(
    {
        # release identity (version baselines)
        "service.version",
        "deployment.commit",
        "deployment.environment",
        "instance.id",
        # HTTP server lifecycle
        "http.request.method",
        "http.route",
        "http.response.status_code",
        "server.address",
        "duration_ms",
        "url.path",
        "url.scheme",
        # database operation class (never db.statement)
        "db.system",
        "db.operation",
        # sandbox / background work
        "sandbox.backend",
        "sandbox.command_class",
        "sandbox.exit_code",
        "scheduler.cycle_type",
        "background.job_type",
        # model identity (provider+model are not content)
        "gen_ai.provider.name",
        "gen_ai.request.model",
        # errors: type only, never the message
        "error.type",
    }
)

# Instrumentation scopes whose spans never belong in the operational telemetry.
#
# The REAL Langfuse v4 tracer scope is ``langfuse-sdk`` — verified against
# ``langfuse._client.constants.LANGFUSE_TRACER_NAME`` (the test imports the
# constant so scope drift cannot silently pass). ``langfuse`` and
# ``langfuse.*`` remain dropped defensively (v3-style scopes, custom
# wrappers). Keep prefix semantics: scope == entry or scope.startswith(
# entry + ".").
DEFAULT_DROPPED_SCOPES = ("langfuse", "langfuse-sdk")

# Resource-attribute allowlist (OB-1 Task 1): only release-identity fields
# survive to the operational OTLP destination. Anything else on the resource —
# host.name, user.id, custom deployment labels — is fingerprinting or
# secret-carrying material and never leaves the process.
ALLOWED_RESOURCE_ATTRIBUTES = frozenset(
    {
        "service.name",
        "service.version",
        "deployment.commit",
        "deployment.environment",
        "instance.id",
    }
)

_state: dict[str, Any] = {
    "semantic_telemetry_provider": None,
    "owns_semantic_telemetry_provider": False,
    "operational_telemetry_provider": None,
    "owned_operational_processors": [],
    "langfuse_initialized": False,
    "shutdown_done": False,
}


def _reset_for_tests() -> None:
    """Restore pristine module state between tests (not for production use)."""
    _state.update(
        semantic_telemetry_provider=None,
        owns_semantic_telemetry_provider=False,
        operational_telemetry_provider=None,
        owned_operational_processors=[],
        langfuse_initialized=False,
        shutdown_done=False,
    )
    try:
        from src.sdk.langfuse_tracer import LangfuseTracer

        LangfuseTracer._client = None
    except Exception:
        pass


def _reset_langfuse_singleton() -> None:  # pragma: no cover - test hook point
    """Hook point for tests that must isolate the Langfuse singleton."""


def operational_telemetry_active() -> bool:
    """True only when an explicit endpoint created the operational pipeline."""
    return _state["operational_telemetry_provider"] is not None


@contextmanager
def operational_telemetry_span(name: str, **attributes: Any) -> Iterator[Any]:
    """Open an operational span without exposing it to semantic processors.

    The dedicated provider retains the current OTel context as its parent, so
    its span shares an active semantic trace ID while its processor pipeline is
    completely independent. With no explicit operational endpoint this is a
    true no-op: callers do no tracer lookup and create no span.
    """
    provider = _state["operational_telemetry_provider"]
    if provider is None:
        yield None
        return
    tracer_override = getattr(provider, "_tracer_override", None)
    tracer = tracer_override or provider.get_tracer("assistant.operational")
    with tracer.start_as_current_span(name, attributes=attributes) as span:
        yield span


def instrument_provider_http(provider: Any) -> None:
    """Wrap a provider's httpx request seam with an ``http.client`` span.

    Carries host/status/method/duration ONLY. The URL path, query string,
    request body, and response content never enter span attributes; the
    provider's own (semantic) spans remain the only place content is
    recorded, and the exporter drops those at the operational destination.
    """
    client = provider._http_client
    if not operational_telemetry_active() or getattr(provider, "_ob1_http_instrumented_client", None) is client:
        return
    original_client = client

    class _InstrumentedClient:
        def __init__(self, client: Any) -> None:
            self._client = client

        def __getattribute__(self, name: str) -> Any:
            client = object.__getattribute__(self, "_client")
            if name == "post":
                return _wrap_post(client)
            if name == "stream":
                return _wrap_stream(client)
            if name == "is_closed":
                return getattr(client, "is_closed", False)
            if name == "aclose":
                return getattr(client, "aclose", None)
            return getattr(client, name)

    def _wrap_post(client: Any) -> Callable[..., Any]:
        async def wrapped(url: str, **kwargs: Any) -> Any:
            from urllib.parse import urlparse

            # ``hostname`` intentionally excludes a credential-bearing userinfo
            # component, port, path, query, and fragment (unlike ``netloc``).
            host = urlparse(str(url)).hostname or ""
            start = time.monotonic()
            try:
                response = await client.post(url, **kwargs)
                with operational_telemetry_span(
                    "http.client",
                    **{
                        "http.request.method": "POST",
                        "server.address": host,
                        "http.response.status_code": getattr(
                            response, "status_code", 0
                        ),
                        "duration_ms": round((time.monotonic() - start) * 1000, 3),
                    },
                ) as span:
                    if span is not None:
                        pass
                return response
            except Exception:
                with operational_telemetry_span(
                    "http.client",
                    **{
                        "http.request.method": "POST",
                        "server.address": host,
                        "duration_ms": round((time.monotonic() - start) * 1000, 3),
                    },
                ):
                    pass
                raise

        return wrapped

    def _wrap_stream(client: Any) -> Callable[..., Any]:
        def wrapped(method: str, url: str, **kwargs: Any) -> Any:
            from urllib.parse import urlparse

            # httpx's ``stream`` returns an async context manager rather than
            # an awaitable. Preserve that API exactly while timing the entire
            # outbound response lifecycle (connect through stream close).
            return _InstrumentedStream(
                client.stream(method, url, **kwargs),
                method=str(method).upper(),
                host=urlparse(str(url)).hostname or "",
            )

        return wrapped

    class _InstrumentedStream:
        """Async-context-manager adapter that emits one safe outbound span."""

        def __init__(self, stream: Any, *, method: str, host: str) -> None:
            self._stream = stream
            self._method = method
            self._host = host
            self._response: Any | None = None
            self._start: float | None = None
            self._emitted = False

        def _emit(self) -> None:
            if self._emitted:
                return
            self._emitted = True
            attributes: dict[str, Any] = {
                "http.request.method": self._method,
                "server.address": self._host,
                "duration_ms": round(
                    (time.monotonic() - (self._start or time.monotonic())) * 1000,
                    3,
                ),
            }
            if self._response is not None:
                attributes["http.response.status_code"] = getattr(
                    self._response, "status_code", 0
                )
            with operational_telemetry_span("http.client", **attributes):
                pass

        async def __aenter__(self) -> Any:
            self._start = time.monotonic()
            try:
                self._response = await self._stream.__aenter__()
                return self._response
            except Exception:
                self._emit()
                raise

        async def __aexit__(self, *args: Any) -> Any:
            try:
                return await self._stream.__aexit__(*args)
            finally:
                self._emit()

    provider._http_client = _InstrumentedClient(original_client)
    provider._ob1_http_instrumented_client = provider._http_client


def instrument_openai_provider_http(provider: Any) -> None:
    """Attach local httpx hooks to an OpenAI SDK client when operational telemetry is active.

    OpenAI-compatible providers own their HTTP client inside ``AsyncOpenAI``.
    This attaches hooks to that one client instance rather than monkeypatching
    httpx globally. Both regular and streamed SDK requests pass through the
    hooks. The response hook records only method, hostname, status, and
    duration; request URL userinfo/path/query and bodies are never read.
    """
    if not operational_telemetry_active():
        return
    client = getattr(getattr(provider, "_client", None), "_client", None)
    if client is None or getattr(provider, "_ob1_openai_http_client", None) is client:
        return
    hooks = getattr(client, "event_hooks", None)
    if not isinstance(hooks, dict):
        return

    async def on_request(request: Any) -> None:
        request.extensions["assistant.operational.start"] = time.monotonic()

    async def on_response(response: Any) -> None:
        request = response.request
        start = request.extensions.get("assistant.operational.start", time.monotonic())
        with operational_telemetry_span(
            "http.client",
            **{
                "http.request.method": str(request.method).upper(),
                "server.address": str(request.url.host or ""),
                "http.response.status_code": getattr(response, "status_code", 0),
                "duration_ms": round((time.monotonic() - start) * 1000, 3),
            },
        ):
            pass

    hooks.setdefault("request", []).append(on_request)
    hooks.setdefault("response", []).append(on_response)
    provider._ob1_openai_http_client = client


def instrument_sqlite_connection(conn: Any) -> Any:
    """Return an execute-wrapping proxy for a sqlite3 connection.

    Carries operation class + duration ONLY. The SQL statement text and
    parameters never enter span attributes (Rule 2: content never enters
    the operational telemetry).

    ``sqlite3.Connection`` rejects attribute assignment, so instrumentation
    is a proxy object rather than an attribute flag. Re-instrumenting the
    proxy is idempotent. When observability is inactive the connection is
    returned unchanged.
    """
    if not operational_telemetry_active() or isinstance(conn, _SQLiteProxy):
        return conn
    original_execute = conn.execute

    def wrapped_execute(sql: str, *args: Any) -> Any:
        statement = str(sql).strip().lower()
        if statement.startswith("select"):
            op = "select"
        elif statement.startswith("insert"):
            op = "insert"
        elif statement.startswith("update"):
            op = "update"
        elif statement.startswith("delete"):
            op = "delete"
        elif statement.startswith("create"):
            op = "create"
        elif statement.startswith("pragma"):
            op = "pragma"
        else:
            op = "other"
        start = time.monotonic()
        try:
            result = original_execute(sql, *args)
            with operational_telemetry_span(
                "db.query",
                **{
                    "db.system": "sqlite",
                    "db.operation": op,
                    "duration_ms": round((time.monotonic() - start) * 1000, 3),
                },
            ) as span:
                if span is not None:
                    pass
            return result
        except Exception:
            with operational_telemetry_span(
                "db.query",
                **{
                    "db.system": "sqlite",
                    "db.operation": op,
                    "duration_ms": round((time.monotonic() - start) * 1000, 3),
                },
            ):
                pass
            raise

    proxy = _SQLiteProxy(conn, wrapped_execute)
    return proxy


class _SQLiteProxy:
    """Delegating proxy so wrapped execute rides on the real connection."""

    def __init__(self, conn: Any, execute: Any) -> None:
        object.__setattr__(self, "_conn", conn)
        object.__setattr__(self, "_wrapped_execute", execute)

    def __getattr__(self, name: str) -> Any:
        return getattr(object.__getattribute__(self, "_conn"), name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(object.__getattribute__(self, "_conn"), name, value)

    def execute(self, sql: str, *args: Any) -> Any:
        return object.__getattribute__(self, "_wrapped_execute")(sql, *args)


def _register_owned_operational_processor(processor: Any) -> None:
    """Register an operational processor owned by this lifecycle module."""
    _state["owned_operational_processors"].append(processor)


class FilteringSpanExporter(SpanExporter):
    """Admin-destination exporter: operational spans only, allowlisted attrs.

    Exporter-side filtering (spec: never mutate the immutable ReadableSpan):
    spans whose instrumentation scope matches ``dropped_scopes`` (e.g.
    ``langfuse``) are dropped entirely; every other span is forwarded as a
    NEW ReadableSpan carrying only ``allowed_attributes``. The shared trace
    ID survives — it is the join key between the semantic and operational
    layers.

    Delegate failures are swallowed and counted: a dead OTLP endpoint must
    never raise into the span path (spec: exporters are off the critical
    path; drops, never backpressure).
    """

    def __init__(
        self,
        delegate: Any,
        allowed_attributes: frozenset[str] | None = None,
        dropped_scopes: tuple[str, ...] = DEFAULT_DROPPED_SCOPES,
    ) -> None:
        super().__init__()
        self._delegate = delegate
        self._allowed = allowed_attributes or ALLOWED_OPERATIONAL_ATTRIBUTES
        self._dropped_scopes = dropped_scopes
        self.failure_count = 0

    def _is_dropped(self, span: Any) -> bool:
        scope_name = (
            getattr(getattr(span, "instrumentation_scope", None), "name", "") or ""
        )
        return any(
            scope_name == scope or scope_name.startswith(scope + ".")
            for scope in self._dropped_scopes
        )

    def _filtered_resource(self, resource: Any) -> Resource:
        attrs = {
            key: value
            for key, value in dict(getattr(resource, "attributes", None) or {}).items()
            if key in ALLOWED_RESOURCE_ATTRIBUTES
        }
        return Resource(attrs)

    def _filtered_events(self, events: Any) -> tuple[Event, ...]:
        filtered: list[Event] = []
        for event in events or ():
            filtered.append(
                Event(
                    name=event.name,
                    timestamp=event.timestamp,
                    attributes={
                        key: value
                        for key, value in dict(
                            getattr(event, "attributes", None) or {}
                        ).items()
                        if key in self._allowed
                    },
                )
            )
        return tuple(filtered)

    def _filtered_status(self, status: Any) -> Status:
        # Status.description routinely carries exception messages (PII) —
        # only the status code is operational-telemetry material.
        return Status(status_code=status.status_code)

    def _filtered_copy(self, span: Any) -> Any:
        attrs = {
            key: value
            for key, value in (getattr(span, "attributes", None) or {}).items()
            if key in self._allowed
        }
        # Build a NEW immutable ReadableSpan — original is untouched.
        # Events keep name/timestamp but lose non-allowlisted attributes
        # (exception.message/stacktrace); links are dropped entirely (no
        # operational-telemetry contract exists for them yet); the resource is
        # filtered to release-identity only; the status loses its
        # human-readable description.
        return ReadableSpan(
            name=span.name,
            context=span.context,
            parent=span.parent,
            resource=self._filtered_resource(span.resource),
            attributes=attrs,
            events=self._filtered_events(span.events),
            links=(),
            kind=span.kind,
            instrumentation_scope=span.instrumentation_scope,
            start_time=span.start_time,
            end_time=span.end_time,
            status=self._filtered_status(span.status),
        )

    def export(self, spans: Any) -> Any:
        forwarded = []
        try:
            for span in spans:
                if self._is_dropped(span):
                    continue
                forwarded.append(self._filtered_copy(span))
        except Exception as exc:  # filtering must never raise
            self.failure_count += 1
            logger.warning(
                "observability.export_filter_failed", {"error": str(exc)}
            )
            return SpanExportResult.FAILURE
        if not forwarded:
            return SpanExportResult.SUCCESS
        try:
            return self._delegate.export(forwarded)
        except Exception as exc:
            self.failure_count += 1
            logger.warning(
                "observability.export_delegate_failed",
                {"error": str(exc), "dropped_spans": len(forwarded)},
            )
            return SpanExportResult.FAILURE

    def shutdown(self) -> None:
        try:
            self._delegate.shutdown()
        except Exception as exc:
            logger.warning(
                "observability.export_delegate_shutdown_failed", {"error": str(exc)}
            )


def configure_observability(settings: AppConfig) -> Any | None:
    """Configure isolated semantic and optional operational telemetry pipelines.

    The process-global semantic provider is created/adopted once for Langfuse.
    A separate operational provider is constructed only when ``OTEL_ENDPOINT``
    is explicit; it alone owns the filtered ClickStack exporter. This prevents
    operational spans from reaching Langfuse by construction.
    """
    try:
        from opentelemetry import trace as otel_trace
        from opentelemetry.sdk.trace import TracerProvider as SDKTracerProvider
    except Exception as exc:  # pragma: no cover - direct runtime dependency
        logger.warning("observability.otel_unavailable", {"error": str(exc)})
        return None

    provider = _state["semantic_telemetry_provider"]
    if provider is None:
        current = otel_trace.get_tracer_provider()
        if isinstance(current, SDKTracerProvider):
            provider = current
            owns = False
        elif isinstance(current, otel_trace.ProxyTracerProvider):
            provider = SDKTracerProvider(resource=Resource.create({"service.name": "assistant"}))
            otel_trace.set_tracer_provider(provider)
            owns = True
        else:
            logger.warning(
                "observability.foreign_provider",
                {"provider_type": type(current).__name__},
            )
            return None
        _state["semantic_telemetry_provider"] = provider
        _state["owns_semantic_telemetry_provider"] = owns

    otel_cfg = getattr(getattr(settings, "observability", None), "otel", None)
    endpoint = str(getattr(otel_cfg, "endpoint", "") or "")
    if endpoint and _state["operational_telemetry_provider"] is None:
        delegate = OTLPSpanExporter(
            endpoint=endpoint,
            headers=dict(getattr(otel_cfg, "headers", None) or {}),
            timeout=5,
        )
        exporter = FilteringSpanExporter(delegate)
        operational_provider = SDKTracerProvider(
            resource=Resource.create({"service.name": "assistant"})
        )
        processor = BatchSpanProcessor(exporter, export_timeout_millis=5000)
        operational_provider.add_span_processor(processor)
        _state["operational_telemetry_provider"] = operational_provider
        _register_owned_operational_processor(processor)

    return provider


def ensure_langfuse_initialized(settings: AppConfig) -> bool:
    """Sole Langfuse client construction path. Idempotent, fail-closed.

    Returns True when Langfuse tracing is active. Degrades (returns False)
    when disabled, key-less, hostless, or when no provider could be owned —
    never constructs a client that would create a second global provider.
    """
    from src.sdk.langfuse_tracer import LangfuseTracer

    # The LangfuseTracer singleton is the authoritative "already live"
    # signal — never a module flag. Tests and init-failure paths reset the
    # singleton directly; a stale flag here would silently disable tracing
    # for the rest of the process.
    if LangfuseTracer.is_enabled():
        return True

    lf = settings.langfuse
    if not (lf.enabled and lf.public_key and lf.secret_key):
        return False

    from src.config.settings import _resolve_langfuse_host

    host = _resolve_langfuse_host(settings)
    if not host:
        # Startup validation (Task 1) already refuses this configuration;
        # a lazy caller degrades instead of defaulting to cloud.langfuse.com.
        logger.warning("observability.langfuse_no_host")
        return False

    provider = configure_observability(settings)
    if provider is None:
        logger.warning("observability.langfuse_degraded_no_provider")
        return False

    LangfuseTracer.init(
        public_key=lf.public_key,
        secret_key=lf.secret_key,
        host=host,
        tracer_provider=provider,
    )
    _state["langfuse_initialized"] = True
    return LangfuseTracer.is_enabled()


def shutdown_observability() -> None:
    """Flush/shut down only resources this module owns. Exactly once."""
    if _state["shutdown_done"]:
        return
    _state["shutdown_done"] = True

    for processor in _state["owned_operational_processors"]:
        try:
            processor.shutdown()
        except Exception as exc:  # never break shutdown
            logger.warning("observability.processor_shutdown_failed", {"error": str(exc)})
    _state["owned_operational_processors"].clear()

    try:
        from src.sdk.langfuse_tracer import LangfuseTracer

        LangfuseTracer.flush()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("observability.langfuse_flush_failed", {"error": str(exc)})
