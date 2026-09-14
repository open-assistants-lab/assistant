# Task 1 Report — Deployment Native Tool Trimming

## Implemented

- Added `tools.disabled: list[str]` to `ToolsConfig`.
- Added `filter_deployment_disabled_native_tools()` in `src.sdk.runner`, using case-sensitive `fnmatchcase` matching.
- Applied the filter only to `get_native_tools()` at loop creation and `_current_tool_catalog()` refresh construction, before capability filtering.
- Custom TOOL.md, MCP, and runner meta tools never enter the native filter and therefore remain visible even when their names match a deployment pattern.
- Added `config.yaml` and `DEPLOYMENT.md` configuration guidance.

## Validation

- `timeout 180 uv run pytest tests/sdk/test_deployment_tool_trimming.py -q` → 6 passed.
- `timeout 240 uv run pytest tests/sdk/test_deployment_tool_trimming.py tests/sdk/test_runner.py tests/unit/test_config.py -q` → 57 passed.
- `timeout 120 uv run ruff check src/config/settings.py src/sdk/runner.py tests/sdk/test_deployment_tool_trimming.py tests/sdk/test_runner.py` → passed.
- `timeout 240 uv run mypy src/config/settings.py src/sdk/runner.py` → 4 existing runner errors. Three predate this task (`messages.get_message_store`, `Any` return, missing `profile_timeout_seconds`); the remaining lambda inference is pre-existing code at the refresh seam. No source change was made to suppress unrelated type errors.

## Scope

No native registry global mutation, no per-user config, and no custom/MCP filtering were added.
