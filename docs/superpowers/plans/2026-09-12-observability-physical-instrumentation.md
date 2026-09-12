# OB-1 Physical Instrumentation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Emit safe admin-only physical spans while meeting the documented privacy and performance bounds.

**Architecture:** OB-0 remains the lifecycle/export owner. Instrumentation creates only physical spans; `FilteringSpanExporter` removes all non-allowlisted span/resource/event/status fields before OTLP serialization.

**Tech Stack:** OpenTelemetry SDK, FastAPI, httpx, subprocess, sqlite3, pytest.

**Spec:** `docs/superpowers/specs/2026-09-12-observability-physical-instrumentation-design.md`

## Constraints
- Never run pytest without `timeout`.
- No vendor egress, consent, ring buffer, dashboard, alert, prompt, tool args/results, DB statements, command strings, stable user IDs, or content.
- Empty `OTEL_ENDPOINT` creates no exporter activity.

### Task 1: Harden the exporter privacy boundary

**Files:** `src/sdk/observability.py`, `tests/sdk/test_observability_export.py`

- [ ] Write red tests proving physical copies drop event attributes, links, resource attributes outside the allowlist, and `status.description`/exception messages while retaining allowed resource version attributes and error type.
- [ ] Run `timeout 180 uv run pytest tests/sdk/test_observability_export.py -q`; confirm red.
- [ ] Implement filtered copies without mutating `ReadableSpan`.
- [ ] Re-run focused export/lifecycle tests and commit `fix(observability): filter physical span metadata`.

### Task 2: Add HTTP and sandbox physical spans

**Files:** `src/http/main.py`, `src/sdk/sandbox.py`, `src/sdk/observability.py`, focused tests.

- [ ] Add red tests: health routes excluded; normal request emits route/status only; sandbox emits backend/command class/duration/exit only.
- [ ] Add startup instrumentation only when an explicit endpoint exists; add manual sandbox span. Never capture command text.
- [ ] Run focused tests and commit.

### Task 3: Add outbound HTTP and SQLite operation spans

**Files:** provider/client boundary, SQLite operation wrapper(s), tests.

- [ ] Add red tests for host/status/duration only and SQLite operation/duration only; assert URLs, statements, params, and content markers are absent.
- [ ] Implement narrow wrappers; no blanket sqlite monkeypatching.
- [ ] Run focused tests and commit.

### Task 4: Baseline comparison and release gates

- [ ] Repeat 20-run deterministic loop, 10 cold imports/RSS, and full `timeout 900 uv run pytest tests/ -q`.
- [ ] Record before/after values; stop if p50/p95 >5%, startup +1s, or RSS +70MB.
- [ ] Review, merge, tag only after gates pass.
