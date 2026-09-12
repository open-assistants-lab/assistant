# OB-1 Physical Instrumentation Design

## Goal
Add admin-only physical runtime telemetry to the OB-0 provider without exporting semantic Langfuse content, PII, database statements, command strings, or unfiltered exception data.

## Order
1. Harden `FilteringSpanExporter` to filter span events, links, status descriptions, and resource attributes.
2. Instrument FastAPI/httpx with explicit noise filters; treat SSE/WS as connection lifecycle, excluded from request-latency baselines.
3. Add manual `sandbox.exec` and SQLite operation spans with allowlisted attributes only.
4. Repeat the established deterministic 20-run/cold-start/RSS measurements and enforce OB-1 budgets.

## Scope
Admin endpoint only; no vendor exporter, consent, ring buffer, dashboard/alerts, or frontend protocol work. Auto-instrumentation must never create exporter activity when `OTEL_ENDPOINT` is empty.
