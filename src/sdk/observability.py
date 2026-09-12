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
  with THIS module (Task 3 adds the filtered admin exporter), plus the
  Langfuse client flush, exactly once.
- Disabled/unconfigured observability performs zero outbound requests: no
  exporter exists until Task 3 wires an explicit admin endpoint.

Vendor telemetry (consent tiers, vendor endpoints) is explicitly out of scope.
"""

from __future__ import annotations

import logging
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

# Physical-layer attribute allowlist (spec §Destination routing). Only these
# keys survive to the admin OTLP destination; everything else — prompts,
# tool arguments/results, message content, unknown instrumentation attrs —
# is dropped at the exporter. Extend deliberately, never wholesale.
#
# Deferred to OB-1 (deliberately NOT here): filtering span *events*,
# *links*, and per-destination *resource* attributes. OB-0 ships the
# attribute-level boundary only; those surfaces need their own red/green
# coverage when the physical spans that populate them exist.
ALLOWED_PHYSICAL_ATTRIBUTES = frozenset(
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
        "url.path",
        "url.scheme",
        # database operation class (never db.statement)
        "db.system",
        "db.operation",
        # sandbox / background work
        "sandbox.backend",
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

# Instrumentation scopes whose spans never belong in the physical layer.
#
# The REAL Langfuse v4 tracer scope is ``langfuse-sdk`` — verified against
# ``langfuse._client.constants.LANGFUSE_TRACER_NAME`` (the test imports the
# constant so scope drift cannot silently pass). ``langfuse`` and
# ``langfuse.*`` remain dropped defensively (v3-style scopes, custom
# wrappers). Keep prefix semantics: scope == entry or scope.startswith(
# entry + ".").
DEFAULT_DROPPED_SCOPES = ("langfuse", "langfuse-sdk")

# Resource-attribute allowlist (OB-1 Task 1): only release-identity fields
# survive to the admin OTLP destination. Anything else on the resource —
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
    "provider": None,
    "owns_provider": False,
    "langfuse_initialized": False,
    "shutdown_done": False,
    "owned_processors": [],
}


def _reset_for_tests() -> None:
    """Restore pristine module state between tests (not for production use)."""
    _state.update(
        provider=None,
        owns_provider=False,
        langfuse_initialized=False,
        shutdown_done=False,
        owned_processors=[],
    )
    try:
        from src.sdk.langfuse_tracer import LangfuseTracer

        LangfuseTracer._client = None
    except Exception:
        pass


def _reset_langfuse_singleton() -> None:  # pragma: no cover - test hook point
    """Hook point for tests that must isolate the Langfuse singleton."""


def _register_owned_processor(processor: Any) -> None:
    """Register a processor this module owns and must shut down (Task 3+)."""
    _state["owned_processors"].append(processor)


class FilteringSpanExporter(SpanExporter):
    """Admin-destination exporter: physical spans only, allowlisted attrs.

    Exporter-side filtering (spec: never mutate the immutable ReadableSpan):
    spans whose instrumentation scope matches ``dropped_scopes`` (e.g.
    ``langfuse``) are dropped entirely; every other span is forwarded as a
    NEW ReadableSpan carrying only ``allowed_attributes``. The shared trace
    ID survives — it is the join key between the semantic and physical
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
        self._allowed = allowed_attributes or ALLOWED_PHYSICAL_ATTRIBUTES
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
        # only the status code is physical-layer material.
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
        # physical-layer contract exists for them yet); the resource is
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
    """Create (or adopt) the process-global SDK TracerProvider. Idempotent.

    Returns the provider, or ``None`` when observability must degrade (OTel
    unavailable, or a foreign provider already owns the write-once slot).
    Never raises into startup.
    """
    if _state["provider"] is not None:
        return _state["provider"]
    try:
        from opentelemetry import trace as otel_trace
        from opentelemetry.sdk.trace import TracerProvider as SDKTracerProvider
    except Exception as exc:  # pragma: no cover - otel ships with langfuse
        logger.warning("observability.otel_unavailable", {"error": str(exc)})
        return None

    current = otel_trace.get_tracer_provider()
    if isinstance(current, SDKTracerProvider):
        # Another SDK-aware component created it first; adopt, don't duplicate.
        provider = current
        owns = False
    elif isinstance(current, otel_trace.ProxyTracerProvider):
        from opentelemetry.sdk.resources import Resource

        provider = SDKTracerProvider(
            resource=Resource.create({"service.name": "assistant"})
        )
        otel_trace.set_tracer_provider(provider)
        owns = True
    else:
        logger.warning(
            "observability.foreign_provider",
            {"provider_type": type(current).__name__},
        )
        return None

    _state["provider"] = provider
    _state["owns_provider"] = owns

    # Admin OTLP export (Task 3): built ONLY for an explicit non-empty
    # endpoint. Empty endpoint = no exporter exists = zero outbound requests.
    otel_cfg = getattr(getattr(settings, "observability", None), "otel", None)
    endpoint = str(getattr(otel_cfg, "endpoint", "") or "")
    if endpoint and OTLPSpanExporter is not None and BatchSpanProcessor is not None:
        delegate = OTLPSpanExporter(
            endpoint=endpoint,
            headers=dict(getattr(otel_cfg, "headers", None) or {}),
            timeout=5,  # spec R-PERF: retries must not accumulate
        )
        exporter = FilteringSpanExporter(delegate)
        processor = BatchSpanProcessor(exporter, export_timeout_millis=5000)
        provider.add_span_processor(processor)
        _register_owned_processor(processor)
    elif endpoint:
        logger.warning(
            "observability.otel_exporter_unavailable", {"endpoint_set": True}
        )

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

    for processor in _state["owned_processors"]:
        try:
            processor.shutdown()
        except Exception as exc:  # never break shutdown
            logger.warning("observability.processor_shutdown_failed", {"error": str(exc)})
    _state["owned_processors"].clear()

    try:
        from src.sdk.langfuse_tracer import LangfuseTracer

        LangfuseTracer.flush()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("observability.langfuse_flush_failed", {"error": str(exc)})
