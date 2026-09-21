# Custom Tool Sandbox Routing Implementation Plan

**Goal:** Resolve #34 — custom `TOOL.md` commands must run through the `SandboxBackend` seam instead of a bare `subprocess.run(..., shell=True)`, so they receive the same caps as every other command path.

**Architecture:** One shared transport helper, `run_custom_command()` in `src/sdk/tools_custom.py`, executes the rendered shell string as a single argv `["sh", "-c", rendered]` through `SandboxBackend.run()`. Both wrappers — `_parse_tool_file` (freshly parsed) and `tool_index._rebuild_custom_function` (rebuilt from the index) — call it. cwd is the invoking user's workspace files directory; `user_id` is passed so the soft backend's uid drop applies. Capture and write budgets come from the operator's `shell_tool.max_output_kb` / `shell_tool.max_write_mb`; the wall-clock cap remains the tool's declared `annotations.timeout_seconds` (#23).

**Approved design (issue option 1):** Wrap the shell string as one `sh -c` invocation rather than duplicating rlimit/uid/env logic beside `subprocess.run`. The seam is transport, not policy — policy checks (`custom_command_tools_allowed`, the `which` availability probe, parameter rendering) stay in front. Signal and timeout semantics are preserved: a cap kill raises `CommandKilledError`, a timeout raises `subprocess.TimeoutExpired`, and the `128+n` pipeline band (#32 part 2) still maps to a signal death instead of a failure string.

**Constraints:** Separate worktree (`custom-tool-sandbox-34`, branched from `64e10a64`); no new dependencies; all pytest runs under `timeout`; scoped Ruff and mypy on changed files; `git diff --check` before commit.

## Checklist
- [x] Red regressions for both wrappers: transport goes through the seam with `["sh", "-c", rendered]`, workspace cwd, `user_id`, and the configured limits; the workspace write budget bounds a custom command (`tests/sdk/test_custom_tool_sandbox.py`). Observed red: 4 failed / 2 passed. The 2 MB write under a 1 MB budget succeeded pre-fix, leaving `big.bin` at 2,097,152 bytes in the worktree root and returning `"(no output)"`.
- [x] Implement `run_custom_command()` and route both wrappers through it.
- [x] Remove the soft backend's fixed `RLIMIT_NPROC=256`. Without a PID namespace it is enforced against the whole real UID's process count, so any forked command (a pipeline, a tool that spawns a helper) fails with `EAGAIN` once the operator's ambient process count is above 256 — custom commands are typically pipelines, which is where the existing pipeline tests caught it (this host: 889 processes). bwrap keeps its cap because `--unshare-pid` makes the count namespace-local. New unit tests pin the policy via the extracted `_apply_soft_rlimits()`.
- [x] Honour `timeout_seconds: none` (#23) on the seam: backends skip the `RLIMIT_CPU` derivation instead of computing `int(None)`; `NullSandboxBackend` and the `SandboxBackend` protocol accept `user_id` like soft/bwrap already did.
- [x] Update tests that asserted the old transport (`subprocess.run(..., shell=True)`): select the execution call by argv instead of call index, since workspace first-touch runs `git init` through the same patched transport.
- [x] Document which limits apply to custom commands in the `cli-toolkit` seed skill (acceptance criterion 3) and add a CHANGELOG entry.
- [x] Verification: full suite on the committed tree **3124 passed, 26 skipped**; scoped Ruff clean; mypy clean on the three changed source files; `git diff --check` clean.

## Decisions and residual risks
- **Hard backends still refuse custom command tools.** `custom_command_tools_allowed()` is unchanged: `bwrap`/`runc` short-circuit with "disabled by the hard sandbox backend" rather than mapping shell strings into a rootfs that may not contain the tool. Documented in the skill; lifting it is a separate, functional decision.
- **Capture ceiling for very large output (filed as a follow-up issue).** The sandbox stores stdout up to `max_output_bytes * 8` (≈800 KB with default `shell_tool.max_output_kb=100`), while #22's `format_output` previously persisted unbounded captured output. Custom tools now inherit `shell_execute`'s ceiling: results larger than it are no longer recoverable through `tool_result_read`. A single file is still capped by the write budget.
- **The `which` availability probe runs outside the seam** (read-only PATH lookup, 10 s timeout). It does not execute the tool; unchanged by this fix.
- **Per-file, not aggregate, write budget** — same distinction as #32 part 3: file count and total workspace size remain bounded only by the command timeout and the host filesystem.

## Verification commands
```
timeout 300 uv run pytest tests/sdk/test_custom_tool_sandbox.py tests/sdk/test_custom_tool_results.py tests/sdk/test_custom_tool_timeout.py tests/sdk/test_custom_tools.py -q
timeout 600 uv run pytest tests/sdk/test_pipeline_signal_death.py tests/sdk/test_sandbox.py tests/sdk/test_sandbox_write_budget.py -q
timeout 2400 uv run pytest -q          # full suite
uv run ruff check src/sdk/tools_custom.py src/sdk/tool_index.py src/sdk/sandbox.py
uv run mypy src/sdk/tools_custom.py src/sdk/tool_index.py src/sdk/sandbox.py
```

Merged as `--no-ff` per repo convention; issue #34 closed with the commit reference.
