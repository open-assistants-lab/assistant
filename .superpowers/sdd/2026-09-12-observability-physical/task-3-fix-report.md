# OB-1 Task 3 Fix Report

## Scope
Resolved all four Task 3 review P1 findings from commit `8abd6c11`.

## Changes
- Wired `instrument_provider_http()` at real lazy client seams for Ollama Cloud, Anthropic, and Gemini. The wrapper remains inert without active physical observability and re-wraps a replacement client after `aclose()`.
- Wired the selected real SQLite boundary: `AuditStore` wraps its own connection, rather than applying a global sqlite monkeypatch.
- Added `server.address` and `duration_ms` to the physical exporter allowlist and asserted the exporter preserves them.
- Host extraction uses `urlparse(str(url)).hostname`, excluding username, password, port, path, query, and fragment.
- HTTP and SQLite error spans retain only their documented attributes (no `error.type`).
- Replaced helper-only tests with tests that execute `OllamaCloud.chat()` and `AuditStore.record()` through their production seams. Added credential-bearing URL and strict attribute-set coverage.

## Validation
```
timeout 180 uv run pytest tests/sdk/test_ob1_task3_spans.py tests/sdk/test_observability_export.py tests/sdk/test_observability_lifecycle.py tests/sdk/test_observability_physical.py -q
34 passed, 1 warning in 2.32s

timeout 180 uv run ruff check src/sdk/observability.py src/sdk/providers/ollama.py src/sdk/providers/anthropic.py src/sdk/providers/gemini.py src/sdk/audit.py tests/sdk/test_ob1_task3_spans.py tests/sdk/test_observability_export.py
All checks passed!
```

## Residual scope
This fix deliberately instruments only the selected `AuditStore` SQLite boundary, as required; it does not blanket-monkeypatch SQLite or instrument other storage modules.
