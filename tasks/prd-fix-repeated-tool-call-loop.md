# PRD: Fix Repeated Tool-Call Loop + Stale Tool-Result Persistence

## Overview

The agent intermittently invokes the same tool (`time_get`) 3–8 times per round, and the conversation store accumulates stale tool-result rows across runs (a run at 13:27 stored 28 tool rows, 27 of them from earlier runs). Investigation identified two independent root causes:

**Bug 1 — repeated calls (agent loop).** After the tool result returns, the model produces an answer and *re-issues the same tool call* in the next turn because it believes its own previous output "seems to be cut off". This is a model-behavior regression tied to the switch to `ollama-cloud:deepseek-v4-flash:0731` (the previous minimax model did not exhibit it). The grader/verification loop is not the cause — it merely re-runs whatever the agent loop produced.

**Bug 2 — stale rows (storage layer).** The audit persistence (`cc3c35e`) writes tool rows with `include_in_model_context: False`, but `get_messages_with_summary` **never reads that flag**, so (a) previous runs' tool results re-enter the LLM context, and (b) `_tool_audit_records` reads `state.messages` — which includes those history-loaded rows — so each run **re-persists earlier runs' stale tool rows**, growing the transcript on every round (also why those rows lack `tool_name`: they are not loop-produced messages).

## Quality Gates

These commands must pass for every user story:
- `uv run pytest` (full backend suite)
- `uv run ruff check src/`
- `uv run mypy src/`
- `uv run native test` (Zig, 87 tests)
- `bash tests/frontend_suite.sh --all` (37 tests)

For the loop story, verify live with `native automate` (send a time query; count `tool_input_start` events — expect exactly 1; confirm the nudge system message appears when the model re-proposes).

## User Stories

### US-001: Exclude audit records from model-context loading
**Description:** As a backend developer, I want `get_messages_with_summary` to filter rows whose metadata has `include_in_model_context = false`, so previous tool results never leak into the LLM context.

**Acceptance Criteria:**
- [ ] `get_messages_with_summary` excludes rows with `metadata["include_in_model_context"] == False`
- [ ] The filter applies to both the non-summary and the summary-provenance branches
- [ ] New storage test: persisted audit rows do not appear in `get_messages_with_summary` output, while user/reasoning/assistant rows do
- [ ] Live check: after a tool round, a follow-up run's Langfuse prompt contains no `tool`-role rows from previous runs

### US-002: Audit only the current run's tool executions
**Description:** As a developer, I want `_tool_audit_records` to collect only tool messages produced by the current run (not rows loaded from history), so stored transcripts stop accumulating stale rows.

**Acceptance Criteria:**
- [ ] `_tool_audit_records` distinguishes current-run messages from history-loaded ones (e.g. via `storage_id`/`storage_ts` provenance on SDK messages)
- [ ] Two consecutive tool-using runs produce exactly 1 + 1 tool rows in the store (not 1 + 25)
- [ ] All audit rows carry `tool_name` + `tool_call_id` metadata
- [ ] New unit test: run twice with a fake store; assert the second run's stored rows contain none from the first

### US-003: Soft duplicate-call guard in the agent loop (all tools)
**Description:** As a user, I want the agent to call a tool at most once per distinct request, so the transcript doesn't show the model re-calling `time_get` because it "thinks the output was cut off".

**Acceptance Criteria:**
- [ ] Live test: a "what time is it" query produces exactly 1 `tool_input_start` event (was 3–8)
- [ ] **Soft, repeatable:** when the model re-proposes a `(tool, args)` pair already executed this run, the loop does NOT execute it — it injects a **system message** with the existing result ("this tool already returned: <result> — answer directly") and continues; no error framing, no blocking of other tools
- [ ] Nudge limit configurable via **`RunConfig.max_duplicate_tool_nudges`**, default **3**
- [ ] After K nudges, the loop makes one final model call requesting a **brief final text response**, capped at ~**200 tokens** (via `provider_options`); any tool call in that final response is suppressed and the text is used as the answer
- [ ] The guard applies to **all tools** (no read-only restriction)
- [ ] The guard is stateless apart from the nudge counter in loop state (`state.extra`)
- [ ] Text-with-tool-call hold (FR-4): text bundled with a tool call is withheld until the model stops calling tools
- [ ] Stress script: 50 rounds contain no round with >1 `time_get`

## Functional Requirements

- FR-1: `get_messages_with_summary` must exclude messages whose metadata contains `include_in_model_context: False`.
- FR-2: `_tool_audit_records` must persist only the current run's tool messages.
- FR-3: The agent loop must not re-execute a `(tool, args)` pair that already has a stored result in the current run; it must instead inject a **system message** with the previous result and a directive to answer directly — for all tools.
- FR-4: When the model emits text together with a tool call in one response, the text must be withheld until the model stops requesting tools (no partial answer committed mid-loop).
- FR-5: A run with no tools requested must store zero tool rows.
- FR-6: The verification/grader loop must not change the tool-execution count of the underlying agent run.
- FR-7: **No numeric tool-call budgets** are added by this change — no run-level or thread-level count caps, now or as a future backstop (explicit non-goal).
- FR-8: The nudge limit **K** is configurable via `RunConfig.max_duplicate_tool_nudges` (default `3`). The nudge is a system message.
- FR-9: After K nudges, one final model call requests a brief final text response; the call caps output at ~200 tokens via `provider_options`; any tool call in that final response is suppressed and the text is used as the answer.

## Non-Goals

- Changing the model/provider (deepseek-v4-flash stays)
- Numeric tool-call budgets or rate limits (LangChain `ToolCallLimitMiddleware`-style counters — excluded, including as a future backstop)
- Reworking the REACT loop architecture (no tool-executor refactor)
- Changing the rubric/grader verification flow
- Fixing the `time_get` tool itself (it returns fresh results correctly)

## Technical Considerations

- **Context filter is the single-source fix** for both stale rows and the LLM seeing stale results — small change in both branches of `get_messages_with_summary`.
- **Audit provenance**: SDK messages loaded from storage carry `storage_id`/`storage_ts`; current-run messages don't — use that to exclude history rows in `_tool_audit_records`.
- **The soft guard vs LangChain's `ToolCallLimitMiddleware`**: the LangChain middleware is a *count budget* (thread/run counters; blocks exceeded calls with error ToolMessages or ends). Our guard is an *identity check* — it prevents re-execution of an already-answered call, the observed pathology, which a count budget would not fix (a budget of 10 still allows 8 identical `time_get` calls). Per decision, no count budget is added at all — the repeatable system-message nudge is the whole mechanism.
- **Nudge mechanics**: system message (instruction, not tool outcome); repeats up to `max_duplicate_tool_nudges` (default 3); counter lives in `state.extra`.
- **Final-response escalation**: one final call with a strict text-only instruction + ~200-token cap; tool calls from that response suppressed before streaming.
- **Watch out**: FR-4 changes streaming order; existing tests assert text/tool event ordering — update deliberately.
- The guard must not interfere with the parallel-tool-call batch path or the subagent flow.

## Success Metrics

- A "what time is it" run yields exactly 1 stored tool row with `tool_name=time_get`.
- Two consecutive tool runs yield exactly 2 stored tool rows combined.
- No tool row appears in the LLM context of subsequent runs (verified via Langfuse prompt).
- No run in the 50-round stress shows >1 `time_get` execution.
- Frontend suite and Zig tests remain green (37 + 87).

## Open Questions

- None — all decisions resolved.
