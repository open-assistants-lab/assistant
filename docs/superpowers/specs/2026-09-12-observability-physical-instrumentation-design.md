# OB-1 Physical Instrumentation Design

## Goal
Add operational runtime telemetry without exporting semantic Langfuse content, PII, database statements, command strings, or unfiltered exception data.

## Telemetry routing
`observability.py` remains the sole lifecycle owner but owns two isolated pipelines:

- **Semantic telemetry** uses the process-global `semantic_telemetry_provider` for Langfuse agent semantics only.
- **Operational telemetry** uses a dedicated `operational_telemetry_provider`, created only with an explicit `OTEL_ENDPOINT`, for filtered ClickStack runtime diagnostics only.

Operational spans inherit the current semantic context when present, retaining trace-ID correlation without sharing processors. `operational_telemetry_span()` is a no-op unless the operational provider exists. Langfuse must never receive an `assistant.operational` span.

## Order
1. Route semantic and operational telemetry through isolated providers; operational tracing is active only with an explicit endpoint.
2. Harden `FilteringSpanExporter` to filter span events, links, status descriptions, and resource attributes.
3. Instrument FastAPI/httpx with explicit noise filters; treat SSE/WS as connection lifecycle, excluded from request-latency baselines.
3. Add manual `sandbox.exec` and SQLite operation spans with allowlisted attributes only.
4. Repeat the established deterministic 20-run/cold-start/RSS measurements and enforce OB-1 budgets.

## Scope
Admin endpoint only; no vendor exporter, consent, ring buffer, dashboard/alerts, or frontend protocol work. Auto-instrumentation must never create exporter activity when `OTEL_ENDPOINT` is empty.
