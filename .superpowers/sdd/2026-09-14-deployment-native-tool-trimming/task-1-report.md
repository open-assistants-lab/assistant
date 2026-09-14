# #16 Deployment Native Tool Trimming — Final Verification

## Environment

```bash
timeout 300 uv sync --extra dev --extra analytics
```

Result: resolved 187 packages; audited 159 packages; successful.

## Final verification after `TOOLS_DISABLED` precedence fix

```bash
timeout 900 uv run pytest tests/ -q
```

Result: **2895 passed, 26 skipped, 52 warnings, 0 failed** in **420.08s**.

```bash
timeout 180 uv run ruff check \
  src/config/settings.py src/sdk/runner.py src/sdk/tool_index.py \
  src/sdk/tools_core/tool_search.py tests/sdk/test_deployment_tool_trimming.py \
  tests/sdk/test_runner.py tests/sdk/test_tool_search.py
```

Result: **All checks passed.**

No source edits, commits, pushes, retries, or staged files were made by this verification task.

## Critical final-review hard-ceiling repair

Added centralized native-only deployment matching and applied it at persisted-index lazy loading and prompt-guidance boundaries. A stale `tool_type="native"` row matching `tools.disabled` is rejected before the global native registry is consulted or the tool is registered. Custom and MCP rows with a matching name remain eligible because the deployment matcher runs only after persisted provenance identifies a row as native. `tool_reload` documentation now accurately describes deployment-managed native tools.

Compatibility repairs preserved the existing runner helper `(tools, disabled_patterns)` signature via the centralized matcher and restored the module-level `tool_search.get_settings` patch seam.

Focused validation:

```bash
timeout 180 uv run pytest tests/sdk/test_deployment_tool_trimming.py tests/sdk/test_deployment_tool_hard_ceiling.py tests/sdk/test_tool_search.py -q
```

Result: **24 passed** in **4.55s**.

```bash
timeout 180 uv run ruff check src/sdk/deployment_tools.py src/sdk/runner.py src/sdk/loop.py src/sdk/tools_core/tool_search.py src/sdk/tools_core/tool_reload.py tests/sdk/test_deployment_tool_hard_ceiling.py
```

Result: **All checks passed.**

## Final lazy-load provenance repair

Lazy-load success now uses `_register_tool_definition(td)` instead of direct registry registration. The helper records built-in provenance, so a native tool loaded while allowed is rejected when a deployment `tools.disabled` pattern subsequently takes effect; custom/MCP same-name definitions remain eligible.

```bash
timeout 180 uv run pytest tests/sdk/test_deployment_tool_hard_ceiling.py tests/sdk/test_tool_lazy_load.py -q
# 15 passed in 2.53s

timeout 180 uv run ruff check src/sdk/loop.py tests/sdk/test_deployment_tool_hard_ceiling.py
# All checks passed
```
