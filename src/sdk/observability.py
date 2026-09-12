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

from src.config import AppConfig

logger = logging.getLogger("src.sdk.observability")

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
