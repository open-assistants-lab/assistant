# Observability Foundation (OB-0) Design

## Goal
Create one safe OpenTelemetry lifecycle shared by Langfuse and an optional admin-owned ClickStack export path, without changing the existing agent-semantic trace contract or enabling vendor telemetry.

## Scope boundary
This implements only the foundation required before OB-1: provider ownership, startup/shutdown lifecycle, explicit configuration, destination filtering, and privacy/failure tests. It does not add auto-instrumentation, physical spans, local ring buffer, vendor consent tiers, telemetry CLI/API, support bundles, dashboards, alerts, or frontend protocol changes.

## Lifecycle
`src/sdk/observability.py` owns the process-global provider. `configure_observability(settings)` is invoked in FastAPI lifespan before `get_logger()` or any Langfuse constructor. It creates the SDK `TracerProvider` only if OTel is still a `ProxyTracerProvider`; otherwise it accepts only an SDK-compatible existing provider and logs/degrades safely rather than assuming `add_span_processor` exists. It is idempotent. Lifespan shutdown calls `shutdown_observability()` to flush and shut down only processors owned by this module.

`app_logging.Logger` stops constructing a Langfuse client: it does not use that client for logging. `LangfuseTracer.init()` receives the configured provider, becoming the sole Langfuse initialization path. Runner and provider factory call a narrow `ensure_langfuse_initialized()` helper rather than constructing clients themselves; lazy calls remain harmless but use the provider established at startup.

## Configuration and fail-closed behavior
Admin observability is optional and off unless explicitly configured. Extend settings with an `observability.otel` section containing an empty-default OTLP HTTP endpoint and optional headers. `LANGFUSE_BASE_URL` is the primary environment alias; `LANGFUSE_HOST` is legacy. If Langfuse is enabled with credentials but no explicit non-empty host, startup fails with a configuration error instead of using `cloud.langfuse.com`. If no admin OTLP endpoint exists, no OTLP exporter is created. Vendor endpoint constants and T1/T2 consent are explicitly out of scope.

## Destination routing and privacy
A `FilteringSpanExporter` wraps the admin OTLP exporter. It exports only physical/OTel spans and drops Langfuse instrumentation-scope spans, preserving shared trace IDs without duplicating prompts, completions, tool args, or tool results into ClickStack. The exporter is allowlist-based for attributes: resource/version identifiers plus documented physical attributes. Unknown attributes are dropped. This is exporter-side serialization/routing, not mutation of immutable `ReadableSpan` objects.

No vendor exporter is created in OB-0. Therefore identity stripping for the vendor destination is specified and tested as a future OB-9 requirement, not implemented prematurely. Admin-owned Langfuse retains its established user/session semantics.

## Acceptance
1. One SDK tracer provider and one Langfuse initialization lifecycle per process.
2. Langfuse enabled with credentials and no explicit host fails startup; disabled Langfuse makes zero outbound calls.
3. Admin OTLP disabled creates no exporter; exporter errors never enter request handling.
4. Filtered admin OTLP export preserves trace ID for a synthetic physical span and excludes a Langfuse-scope span plus content-marker attributes.
5. FastAPI lifespan starts configuration before logger/Langfuse initialization and shuts it down deterministically.
6. Existing Langfuse streaming-context regression remains green.
