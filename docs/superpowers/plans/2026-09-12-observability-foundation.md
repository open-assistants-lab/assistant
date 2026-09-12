# Observability Foundation (OB-0) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish a single, fail-closed OpenTelemetry and Langfuse lifecycle before adding ClickStack instrumentation.

**Architecture:** A new `src/sdk/observability.py` owns an idempotent process-global SDK `TracerProvider`, optional filtered OTLP export, and deterministic shutdown. Langfuse becomes a consumer of that provider through one initialization helper; logging no longer creates a separate unused Langfuse client. Only the admin channel is represented; vendor consent/export is excluded.

**Tech Stack:** Python 3.11+, Langfuse v4, OpenTelemetry SDK/OTLP HTTP exporter, FastAPI lifespan, pytest.

**Spec:** `docs/superpowers/specs/2026-09-12-observability-foundation-design.md`

## Global Constraints

- Never run pytest without a `timeout` prefix.
- `LANGFUSE_BASE_URL` remains primary; `LANGFUSE_HOST` remains a legacy fallback.
- Disabled/unconfigured observability performs zero outbound requests.
- ClickStack must not receive Langfuse semantic spans, prompts, completions, tool arguments, tool results, or unknown attributes.
- No vendor endpoint, consent tier, local ring buffer, auto-instrumentation, dashboard, or frontend protocol work in this plan.
- Do not stage `.env`, `config.yaml`, Docker configuration, or scratch artifacts.

---

### Task 1: Add explicit settings and fail-closed validation

**Files:**
- Modify: `src/config/settings.py`
- Modify: `src/config/__init__.py`
- Modify: `tests/unit/test_config.py`

**Interfaces:**
- Produces `OtelConfig(endpoint: str = "", headers: dict[str, str] = {})` nested under `ObservabilityConfig`.
- Produces `validate_observability_settings(settings) -> None`, raising `ValueError` when Langfuse is enabled with credentials but no explicit host.

- [ ] Write failing configuration tests for: disabled/no-host succeeds; enabled/keys/no-host fails; enabled/keys/`LANGFUSE_BASE_URL` succeeds; legacy `LANGFUSE_HOST` succeeds; empty OTLP endpoint remains disabled.
- [ ] Run: `timeout 120 uv run pytest tests/unit/test_config.py -q` and confirm red cases fail for validation, not fixture setup.
- [ ] Implement the smallest settings model + startup validation hook. Do not make cloud.langfuse.com a fallback when enabled.
- [ ] Re-run the focused configuration tests.
- [ ] Commit: `feat(observability): add fail-closed tracing configuration`

### Task 2: Centralize provider and Langfuse lifecycle

**Files:**
- Create: `src/sdk/observability.py`
- Modify: `src/http/main.py`
- Modify: `src/app_logging.py`
- Modify: `src/sdk/langfuse_tracer.py`
- Modify: `src/sdk/runner.py`
- Modify: `src/sdk/providers/factory.py`
- Create: `tests/sdk/test_observability_lifecycle.py`

**Interfaces:**
- `configure_observability(settings) -> TracerProvider | None`
- `shutdown_observability() -> None`
- `ensure_langfuse_initialized(settings) -> bool`

- [ ] Write failing tests using fake SDK/Langfuse objects: configuration is idempotent; logger construction does not construct Langfuse; Langfuse receives the configured provider once; lifespan invokes configure before logger access; shutdown flushes owned processors once.
- [ ] Run: `timeout 180 uv run pytest tests/sdk/test_observability_lifecycle.py -q` and confirm red failures.
- [ ] Implement provider ownership. Handle a pre-existing non-SDK provider by logging and leaving export disabled; never call `add_span_processor` blindly. Remove the unused `app_logging` Langfuse construction. Replace duplicate runner/factory init branches with the shared helper.
- [ ] Re-run focused lifecycle and existing Langfuse stream tests.
- [ ] Commit: `refactor(observability): centralize provider and Langfuse lifecycle`

### Task 3: Add filtered admin OTLP export

**Files:**
- Modify: `src/sdk/observability.py`
- Create: `tests/sdk/test_observability_export.py`
- Modify: `pyproject.toml` and lockfile only for explicit OTel packages required at runtime.

**Interfaces:**
- `FilteringSpanExporter(delegate, allowed_attributes, dropped_scopes)` exports only allowed physical spans.

- [ ] Write failing exporter tests using in-memory spans/exporter: physical span preserves trace ID and allowed attributes; a `langfuse` scope span is dropped; marker values in prompt/tool-args/output/unknown attributes never reach delegate; delegate failure is swallowed and recorded locally.
- [ ] Run: `timeout 180 uv run pytest tests/sdk/test_observability_export.py -q` and confirm the expected red failures.
- [ ] Implement exporter-side filtering without mutating `ReadableSpan`. Build it only when the explicit admin endpoint is set; use bounded batch processing and a five-second exporter timeout.
- [ ] Re-run focused export/lifecycle tests and `timeout 200 uv run pytest tests/api/test_langfuse_stream.py -q`.
- [ ] Commit: `feat(observability): add filtered admin OTLP export`

### Task 4: Integration gates and design evidence

**Files:**
- Modify: `DEPLOYMENT.md` with explicit admin tracing configuration, fail-closed host requirement, and zero vendor egress in OB-0.
- Modify: `docs/superpowers/plans/2026-08-26-observability-otel-clickstack.md` status/OB-1 prerequisite note.

- [ ] Add a FastAPI lifespan test proving disabled observability triggers no exporter construction and enabled config initializes/shuts down exactly once.
- [ ] Run:

```bash
uv run ruff check src/sdk/observability.py src/sdk/langfuse_tracer.py src/app_logging.py src/http/main.py tests/sdk/test_observability_lifecycle.py tests/sdk/test_observability_export.py
timeout 200 uv run mypy src/sdk/observability.py src/sdk/langfuse_tracer.py
timeout 300 uv run pytest tests/sdk/test_observability_lifecycle.py tests/sdk/test_observability_export.py tests/api/test_langfuse_stream.py -q
timeout 900 uv run pytest tests/ -q
```

- [ ] Record before/after startup/RSS/request baseline evidence; do not claim R-PERF-3 until 20-run measurement data exists.
- [ ] Commit: `docs(observability): document OB-0 lifecycle and safety boundary`
