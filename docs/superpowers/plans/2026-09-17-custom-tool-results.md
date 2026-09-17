# Custom Tool Results Implementation Plan

**Goal:** Resolve #22 without rerunning commands or requiring files_read.

**Architecture:** Both TOOL.md execution paths bind invoking user/workspace context and use one bounded-output formatter. A file-backed result store under each user's `.tool_results/` keys records by hashed workspace and opaque result ID. A core, read-only `tool_result_read` tool returns bounded character pages; normal capability/deployment policy still applies.

**Approved design:** User approved a dedicated read-only paged-result reader in this conversation. Outputs up to 5000 characters remain unchanged. Larger successful outputs return a JSON preview with explicit truncation, total characters, result ID, next offset, and recovery instructions. Failed commands retain their existing 2000-character diagnostic and timeout behavior (not expanded in this slice). Save failures explicitly report that full output was not saved. Storage evicts oldest entries on save after 100 results per workspace and expires reads after seven days; recovery can expire, never reruns commands. Concurrent saves may briefly exceed the count target; no byte quota or change to subprocess capture memory is included.

**Constraints:** Separate worktree; trusted context not TOOL.md metadata or model arguments; workspace isolation despite user-scoped Files paths; no dependency additions; all pytest commands use timeout.

## Checklist
- [x] Add boundary/recovery/scope regressions for parsed and reconstructed tools; observe red (4 failed, 8 passed).
- [x] Implement `src/sdk/tool_results.py`: format_output(output, user_id, workspace_id), read_result(result_id, offset, limit, user_id, workspace_id). File-backed fixed-width UTF-32LE storage for bounded character reads, including embedded NULs.
- [x] Wire parse/scan/get_custom_tools and lazy reconstruction to trusted runtime context, never persist scope into reconstruct metadata.
- [x] Add and register `src/sdk/tools_core/tool_results.py:tool_result_read` as core/read-only; retain capability/deployment controls.
- [x] Test malformed IDs, invalid pages, Unicode/NUL recovery, expired/missing results, save failure, retention, discovery, real lazy execution/runtime identity and disabled files_read.
- [x] Run focused tests and related SDK suites with timeout; scoped Ruff; diff check; inspect diff before commit. Final evidence: 121 passed in 14.40s; scoped Ruff clean; git diff --check clean. Logs: /tmp/custom-results-final.log. Independent review remains pending.

## Verification commands
`timeout 180 .venv/bin/python -m pytest tests/sdk/test_custom_tool_results.py tests/sdk/test_custom_tools.py tests/sdk/test_custom_tools_merge.py tests/sdk/test_tool_lazy_load.py tests/sdk/test_tool_search.py -q`

Use the main checkout's absolute .venv path when executing in the worktree. Do not merge or release #22 without reporting the completed validation and review status.
