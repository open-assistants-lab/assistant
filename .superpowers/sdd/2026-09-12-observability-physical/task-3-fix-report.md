# OB-1 Task 3 Fix Round 2 — Streaming HTTP Span

## Scope
Reviewer P1: `instrument_provider_http()` previously passed `client.stream()` through without emitting an outbound `http.client` physical span.

## Implementation
- Replaced passthrough with `_InstrumentedStream`, an async-context-manager adapter.
- It preserves `async with client.stream(method, url, ...)` semantics and emits exactly one span on success, response-body failure, context-exit failure, or connection-entry failure.
- Attributes are limited to `http.request.method`, `server.address` (parsed hostname only), `http.response.status_code` when a response exists, and `duration_ms`.
- It never records URL path/query/userinfo, request/response content, exception text, or `error.type`.
- Ollama, Anthropic, and Gemini `chat_stream()` already retrieve clients through `_get_client()`, which installs the shared wrapper; no provider-specific duplication is required.

## TDD / validation
- Added success and failed-entry async-context-manager tests that plant credential/userinfo, query, path, message-body, and exception-content sentinels, asserting all are absent from emitted attributes.
- `timeout 180 uv run pytest tests/sdk/test_ob1_task3_spans.py -q` → `8 passed in 0.25s`.
- `timeout 180 uv run ruff check src/sdk/observability.py tests/sdk/test_ob1_task3_spans.py` → `All checks passed!`.
