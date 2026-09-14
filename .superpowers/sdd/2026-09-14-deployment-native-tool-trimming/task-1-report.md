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

## P2 Follow-up: Live Refresh Regression

- Added direct coverage for `refresh_user_tool_registries()` using an existing live `AgentLoop` registry that already contains a native `files_read` tool plus matching custom and MCP tools.
- The refresh removes the now deployment-disabled native tool and retains `files_custom` and `mcp__crm__files_read`.
- Initial red run caught an invalid test fixture provider identity; after replacing it with the canonical `test:test` identity, the behavioral regression test passed without production changes.
- `timeout 120 uv run pytest tests/sdk/test_deployment_tool_trimming.py::test_refresh_unregisters_deployment_disabled_native_tool_but_keeps_custom_and_mcp -q` → 1 passed.
- `timeout 120 uv run ruff check tests/sdk/test_deployment_tool_trimming.py` → passed.

## Release Verification

- `timeout 300 uv sync --extra dev --extra analytics` → resolved 187 packages and audited 159 packages; analytics dependency is present.
- `timeout 900 uv run pytest tests/ -q` → **2,892 passed, 26 skipped, 53 warnings, 0 failed** in **824.28s**.
- `timeout 180 uv run ruff check src/config/settings.py src/sdk/runner.py tests/sdk/test_deployment_tool_trimming.py tests/sdk/test_runner.py` → **All checks passed**.
- `timeout 180 uv run mypy src/config/settings.py src/sdk/runner.py` → **4 errors**, all in unchanged `src/sdk/runner.py` lines (lambda inference; missing `messages.get_message_store`; `Any` return; missing `AgentLoop.profile_timeout_seconds`). `git diff -U0 5c9e0242..HEAD` confirms no changed code at those error locations; these are pre-existing and unrelated to deployment trimming.
- `git diff --check` → passed. No source changes, commits, or pushes were made during release verification.

## Final-review fix: stale `tool_search` index rows

- Added `ToolIndex.search_with_tool_types()` so `tool_search` can make policy decisions from persisted row provenance.
- `tool_search` now filters only persisted rows whose `tool_type == "native"` and whose name matches deployment `tools.disabled` with case-sensitive `fnmatchcase` semantics.
- Custom TOOL.md and MCP rows are deliberately retained, even if the same pattern matches their names.
- Added a regression fixture with a stale disabled `files_read` native row plus matching `files_custom` custom and `mcp__crm__files_read` MCP rows; the native entry is not advertised and both custom/MCP entries are explicitly asserted.
- `timeout 180 uv run pytest tests/sdk/test_tool_search.py -q` → **12 passed** in 4.87s.
- `timeout 180 uv run ruff check src/sdk/tool_index.py src/sdk/tools_core/tool_search.py tests/sdk/test_tool_search.py` → **All checks passed**.
- `git diff --check` → passed.

## Final-review fix round 2: filter before presentation limit

- `tool_search` now fetches a bounded 50-result candidate set, applies deployment and capability policy filters, then presents at most five results.
- Added a regression where the first five matches are stale deployment-disabled native rows and the sixth is a matching custom tool. The custom tool is returned; the index is read-only.
- `timeout 120 uv run pytest tests/sdk/test_tool_search.py::TestToolSearchTool::test_search_overfetches_before_filtering_deployment_disabled_native_rows -q` → red before implementation, green after.
- `timeout 180 uv run pytest tests/sdk/test_tool_search.py -q` → **13 passed** in 4.93s.
- `timeout 120 uv run ruff check src/sdk/tools_core/tool_search.py tests/sdk/test_tool_search.py` → **All checks passed**.
- `git diff --check` → passed.

## Final verification after index fix round 2

- `timeout 300 uv sync --extra dev --extra analytics` → resolved 187 packages; audited 159 packages; no environment package changes required.
- `timeout 900 uv run pytest tests/ -q` → **2,894 passed, 26 skipped, 52 warnings, 0 failed** in **476.69s**.
- `timeout 180 uv run ruff check src/config/settings.py src/sdk/runner.py src/sdk/tool_index.py src/sdk/tools_core/tool_search.py tests/sdk/test_deployment_tool_trimming.py tests/sdk/test_runner.py tests/sdk/test_tool_search.py` → **All checks passed**.
- `git status --short` → clean. No source changes, commits, pushes, or retries occurred during this final verification.

## Final-review fix round 3: exhaust ranked candidates before filtering

- Replaced the fixed 50-row overfetch with `_eligible_results()`, which expands HybridDB's ranked `limit` prefix in 50-row steps until five allowed results are found or `ToolIndex.count()` proves the finite index is exhausted.
- HybridDB provides no offset/cursor search API, so expanding its ranked prefix is the bounded pagination mechanism; the maximum is the actual persisted index count, not an arbitrary truncation.
- Added the red boundary regression with 50 stale disabled native rows before matching custom `crm_lookup` and MCP `mcp__crm__lookup` rows. Both survive the native-only provenance filter before the public five-result cap.
- `timeout 180 uv run pytest tests/sdk/test_tool_search.py::TestToolSearchTool::test_search_iterates_past_a_full_disabled_candidate_page -q` → red before implementation, green after.
- `timeout 180 uv run pytest tests/sdk/test_tool_search.py tests/sdk/test_deployment_tool_trimming.py -q` → **20 passed** in **5.10s**.
- `timeout 180 uv run ruff check src/sdk/tools_core/tool_search.py tests/sdk/test_tool_search.py` → **All checks passed**.
- `git diff --check` → passed.

## Final-review fix round 4: `TOOLS_DISABLED` environment precedence

- Added an integration regression through `AppConfig.from_yaml()` proving the documented JSON `TOOLS_DISABLED` list overrides YAML `tools.disabled`, while an unset environment variable preserves the YAML patterns.
- `AppConfig.from_yaml()` now applies only this documented flat deployment override after YAML construction, matching existing explicit flat environment precedence without changing unrelated nested configuration behavior.
- Exact/case-sensitive glob handling remains in the existing runner matcher; this fix changes only configuration source precedence.
- `timeout 120 uv run pytest tests/sdk/test_deployment_tool_trimming.py -q` → red: 1 failed before the fix; green: 8 passed after.
- `timeout 180 uv run pytest tests/sdk/test_deployment_tool_trimming.py tests/config/test_settings_resolution.py tests/unit/test_config.py -q` → **26 passed** in **1.08s**.
- `timeout 120 uv run ruff check src/config/settings.py tests/sdk/test_deployment_tool_trimming.py` → **All checks passed**.

## Scope

No native registry global mutation, no per-user config, and no custom/MCP filtering were added.
