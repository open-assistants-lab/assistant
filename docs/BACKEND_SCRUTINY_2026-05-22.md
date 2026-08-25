# Backend Scrutiny Report - 2026-05-22

## Scope

This report now includes a broad backend-wide review. The review covered:

- HTTP REST/SSE/WebSocket routers and protocol models
- SDK core loop, streaming, middleware, guardrails, handoffs, runner, and native tool registration
- Provider implementations and provider factory behavior
- MCP manager/bridge/reload tools
- SDK-native tools: filesystem, file search, file versioning, shell, todos, contacts, email, memory, workspace, apps, skills, web, browser, companion, observation
- Storage-adjacent backend code: message store, memory store usage, HybridDB/DuckDB/journal paths
- Memory/observation/companion/memcore backend code
- Existing backend test suites under `tests/sdk`, `tests/api`, `tests/unit`, and `tests/storage`

Clarification after follow-up review:

- The core agent memory retrieval path is `memory_search`, registered in `src/sdk/native_tools.py:77` and `src/sdk/native_tools.py:134`.
- Subagents also force-include `memory_search` through `MANDATORY_SUBAGENT_TOOLS = {"memory_search"}` in `src/sdk/coordinator.py:38` and `src/sdk/coordinator.py:108`.
- The `/memories` HTTP router is included in `src/http/main.py:92`, but it is not used by the agent loop for memory retrieval.
- `/memories` is still referenced by the Flutter UI in `flutter_app/lib/services/api_client.dart:100-112` and `flutter_app/lib/features/memory/memory_panel.dart:45-76`, so it is UI/API-facing code, not confirmed dead code.

Not deeply covered:

- Flutter frontend behavior, except that there are unrelated local modified Flutter files in the worktree
- Live provider/API behavior against real OpenAI/Anthropic/Gemini/Ollama endpoints
- Live MCP servers beyond static bridge/reload behavior
- Deployment/Docker/Caddy/runtime network configuration beyond code-level auth/CORS review
- Performance benchmarking beyond reading perf-sensitive paths and running normal tests

## Repository State During Review

- `main...origin/main` was clean for committed backend code.
- Existing unrelated local changes were present in Flutter files:
  - `flutter_app/lib/core/layout/desktop_layout.dart`
  - `flutter_app/lib/features/chat/chat_screen.dart`
- This report file was untracked/modified during the review.

## Verification Commands Run

- `uv run pytest tests/sdk/test_summarization_overhaul.py tests/sdk/test_subagent_tools_async.py tests/sdk/test_subagent_v1.py tests/sdk/test_sdk_loop.py -v`
- Result from earlier targeted pass: `178 passed`
- `uv run pytest tests/sdk tests/api tests/unit tests/storage -q`
- Result: timed out after showing multiple failures; split runs below were used for actionable results
- `uv run pytest tests/sdk -q`
- Result: `16 failed, 797 passed`
- `uv run pytest tests/unit -q`
- Result: `81 failed, 122 passed`
- `uv run pytest tests/storage -q`
- Result: `20 passed`
- `uv run pytest tests/api/test_conversation.py -q`
- Result: `7 passed, 1 warning`
- `uv run pytest tests/api/test_workspace.py -q`
- Result: `7 passed, 1 warning`
- `uv run pytest tests/api/test_ws_protocol.py tests/sdk/test_agent_loop.py::TestAgentLoopWSProtocol::test_done_message_can_include_tool_calls -q`
- Result: `3 failed, 30 passed`
- `uv run pytest tests/api/test_memories.py -q -x`
- Result: first failure is `TestAddMemory.test_add_memory_minimal`, expected `200` but got `422`
- `uv run pytest tests/sdk/test_subagent_tools_async.py --collect-only -q`
- Result: `16 tests collected`; nested `_run_async` timeout tests were not collected
- `uv run pytest tests/sdk/test_summarization_overhaul.py --collect-only -q`
- Result: `12 tests collected`
- `uv run ruff check src tests/sdk tests/api tests/unit tests/storage`
- Result: failed with 110 findings; runtime-relevant failures include missing `asyncio` import in email router, undefined names in memories router, `non_system_old`, `DoneMessage.tool_calls` tests, and many test/import hygiene issues
- `uv run mypy src`
- Result: failed early with `eval-viewer is not a valid Python package name`; targeted mypy from earlier found `AgentLoop has no attribute "state"` and `non_system_old` undefined

## Fix Pass - 2026-05-22

Fixed in the follow-up critical pass:

- HTTP auth now applies consistently through a global HTTP middleware, with `/health`, `/health/ready`, `/docs`, `/redoc`, and `/openapi.json` left public.
- WebSocket `DoneMessage` now carries `tool_calls` while preserving `tools_called`.
- `summarize_session` can access active loop state during an agent run.
- Summarization failure handling no longer references undefined `non_system_old`.
- Context-overflow retry no longer duplicates the latest user message after successful summarization.
- Workspace file routes now pass `workspace_id` through list/read/write/delete operations.
- `DELETE /conversation?workspace_id=...` now deletes only messages for the requested workspace.
- REST/SSE persisted tool messages now include `workspace_id` metadata.
- `files_edit()` now captures file versions in the active workspace instead of always using `personal`.
- Providers now normalize 413/context-too-large errors through `ProviderContextOverflowError`.

Verification after fixes:

- `uv run pytest tests/api/test_auth.py tests/api/test_ws_protocol.py tests/sdk/test_agent_loop.py::TestAgentLoopWSProtocol::test_done_message_can_include_tool_calls tests/api/test_workspace.py tests/sdk/test_summarization_overhaul.py tests/sdk/test_providers.py::test_context_overflow_mapper_raises_for_413_status tests/sdk/test_providers.py::test_context_overflow_mapper_raises_for_context_length_text -q`
- Result: `62 passed, 1 warning`
- `uv run ruff check src/http/main.py src/http/ws_protocol.py src/http/routers/conversation.py src/http/routers/workspace.py src/sdk/loop.py src/sdk/middleware_summarization.py src/sdk/tools_core/summarize.py src/sdk/tools_core/filesystem.py src/sdk/providers/base.py src/sdk/providers/openai.py src/sdk/providers/anthropic.py src/sdk/providers/gemini.py src/sdk/providers/ollama.py tests/api/test_auth.py tests/api/test_workspace.py tests/sdk/test_summarization_overhaul.py tests/sdk/test_providers.py`
- Result: `All checks passed!`

## High Severity Findings

### 1. Fixed: HTTP auth was only applied to a few routes

`require_auth` is applied to `/message`, `/message/stream`, and `/conversation/import` in `src/http/routers/conversation.py:81-82`, `258-259`, and `345-346`.

There is no global dependency in `src/http/main.py:73-136`, and other routers are included without auth wrappers. Representative unauthenticated routes include:

- `src/http/routers/conversation.py:39`, `73`, `365`
- `src/http/routers/workspace.py:13`, `33`, `50`, `59`, `77`, `86`, `94`, `103`, `112`, `121`
- `src/http/routers/memories.py:13`, `68`, `87`, `107`, `117`, `128`, `163`, `172`, `196`, `207`, `239`, `261`, `270`
- `src/http/routers/email.py` routes also lack auth dependency

Runtime impact:

- When `API_KEY` is configured, most REST endpoints still allow unauthenticated reads/writes/deletes.
- This includes conversation history, workspace files, memories, emails, connector credentials, skills, and subagents depending on router.

Test evidence:

- No backend auth tests currently assert 401 behavior for these routers.

### 2. Fixed: `summarize_session` was broken at runtime

`src/sdk/tools_core/summarize.py:38-42` uses `loop.state.messages`, but `AgentLoop` never defines `self.state`. Run state is local in `src/sdk/loop.py:627-629` and `src/sdk/loop.py:782-789`.

Evidence:

- Targeted mypy reports: `"AgentLoop" has no attribute "state"`
- Minimal reproduction raised: `AttributeError: 'AgentLoop' object has no attribute 'state'`

Runtime impact:

- If the model calls `summarize_session`, the tool fails instead of compacting the conversation.

Coverage gap:

- `tests/sdk/test_summarization_overhaul.py` checks registration and annotations, but does not invoke `summarize_session` inside an active loop.

### 3. Fixed: automatic summarization could crash on summary-generation failure

`src/sdk/middleware_summarization.py:327-333` references `non_system_old`, which is not defined in that scope.

Evidence:

- `ruff` reports: `F821 Undefined name non_system_old`
- Minimal reproduction raised: `NameError name 'non_system_old' is not defined`

Runtime impact:

- If `_generate_summary()` returns `None`, the intended clean failure path crashes.

Coverage gap:

- Existing tests do not exercise `_generate_summary() -> None` after the summarization threshold is exceeded.

### 4. Multiple `/memories` REST endpoints are invalid, but they are not on the agent retrieval path

`src/http/routers/memories.py` contains request/handler mismatches and undefined variables:

- `add_memory()` uses `MemorySearchRequest` as the body at `src/http/routers/memories.py:68-73`, but then references undefined `trigger`, `action`, `domain`, and `memory_type` at `src/http/routers/memories.py:78-83`.
- `update_memory()` declares route `/{memory_id}` at `src/http/routers/memories.py:87`, but the function does not accept `memory_id`; it references undefined `memory_id`, `trigger`, and `action` at `src/http/routers/memories.py:97-100`.
- Several handlers reference undefined `workspace_id`: `src/http/routers/memories.py:201`, `212`, `244`, `266`, and `275`.

Evidence:

- Broad `ruff` reports `F821` for these undefined names.
- `uv run pytest tests/api/test_memories.py -q -x` fails at `TestAddMemory.test_add_memory_minimal`, returning `422` instead of expected `200`.
- Follow-up source search found no agent-loop dependency on `/memories`; agent retrieval uses `memory_search` from the SDK native tool registry.
- Flutter still references `/memories` list/search/delete endpoints, so this remains a UI/API issue.

Runtime impact:

- Several memory endpoints return 422 or 500 instead of performing memory operations when called directly or by the Flutter memory panel.
- The core agent's memory retrieval should not be broken by these REST endpoint bugs as long as it uses `memory_search`.

### 5. Fixed: WebSocket `DoneMessage` could not carry `tool_calls`

`src/http/ws_protocol.py:230-238` defines `DoneMessage` with `tools_called`, but no `tool_calls` field. Runtime code constructs `DoneMessage(..., tool_calls=[...])` in `src/http/routers/ws.py:167-175`.

Evidence:

- `uv run pytest tests/api/test_ws_protocol.py tests/sdk/test_agent_loop.py::TestAgentLoopWSProtocol::test_done_message_can_include_tool_calls -q` fails 3 tests with `AttributeError: 'DoneMessage' object has no attribute 'tool_calls'`.

Runtime impact:

- WebSocket clients do not receive detailed tool call metadata in completion frames even when the server computes it.

## Medium Severity Findings

### 6. Fixed: workspace file routes ignored `workspace_id` for file operations

In `src/http/routers/workspace.py`:

- `read_workspace_file()` accepts `workspace_id`, but calls `files_read.invoke({"path": path, "user_id": user_id})` at `src/http/routers/workspace.py:33-38`.
- `list_workspace_files()` does not accept `workspace_id` and calls `files_list` without it at `src/http/routers/workspace.py:50-55`.
- `write_workspace_file()` and `delete_workspace_file()` call `files_write` and `files_delete` without `workspace_id` at `src/http/routers/workspace.py:59-73` and `77-82`.

Runtime impact:

- Reads/writes/deletes for non-personal workspaces hit the default personal workspace.

Coverage gap:

- `tests/api/test_workspace.py` passes but does not verify non-personal workspace file operations.

### 7. Fixed: `DELETE /conversation?workspace_id=...` cleared all user conversations

`src/http/routers/conversation.py:73-78` accepts `workspace_id`, but calls `conversation.clear()`. `MessageStore.clear()` deletes every row in `messages` at `src/storage/messages.py:326-329`.

Additional evidence:

- `get_message_store()` caches only by user at `src/storage/messages.py:341-344`, ignoring workspace in the cache key.

Runtime impact:

- Clearing one workspace can delete conversation history for all workspaces belonging to that user.

Coverage gap:

- `tests/api/test_conversation.py` passes but does not seed multiple workspaces and assert isolation.

### 8. Fixed: REST/SSE tool messages were persisted without workspace metadata

`src/http/routers/conversation.py:24-36` persists verbose tool messages with only tool fields. Non-verbose tool messages are added at `src/http/routers/conversation.py:191-193` with only `tool_name`. SSE tool messages are added at `src/http/routers/conversation.py:324-326` with only tool metadata.

The workspace filter treats missing `workspace_id` as personal at `src/http/routers/conversation.py:58-70`.

Runtime impact:

- Tool outputs from non-personal workspaces can disappear from that workspace history and appear in personal workspace context.

### 9. Fixed: context-overflow recovery was unreachable for real provider errors

`ProviderContextOverflowError` is defined in `src/sdk/providers/base.py:54`, and `AgentLoop` catches only that exact exception at `src/sdk/loop.py:690` and `src/sdk/loop.py:926`.

Provider implementations do not translate 413/context-length errors into that exception:

- Anthropic uses `response.raise_for_status()` at `src/sdk/providers/anthropic.py:132` and `190`.
- Gemini uses `response.raise_for_status()` at `src/sdk/providers/gemini.py:125` and `202`.
- Ollama uses `response.raise_for_status()` at `src/sdk/providers/ollama.py:259` and `278`.
- OpenAI provider lets OpenAI SDK errors escape without mapping them.

Runtime impact:

- Context overflow is handled as a generic LLM error instead of triggering summarization/retry.

Coverage gap:

- No provider test simulates 413/context-length responses.

### 10. Fixed: context-overflow recovery duplicated the latest user message

`src/sdk/loop.py:698-703` and `src/sdk/loop.py:937-943` call `force_summarize(state)` and then append `_last_user_message(state.messages)`.

`force_summarize()` already preserves recent messages at `src/sdk/middleware_summarization.py:253-257`, including the final user message.

Runtime impact:

- Overflow retry can send the same user prompt twice to the provider.

### 11. `provider_keys` are ignored for registry model references

`create_model_from_config()` calls `create_provider_from_registry_model(model_str)` first at `src/sdk/providers/factory.py:167-171`. That helper reads API keys only from environment variables at `src/sdk/providers/factory.py:123-130`.

The later patch attempts `registry_provider._api_key = resolved_key`, but current providers use fields like `api_key` or internal clients, not `_api_key`.

Runtime impact:

- Per-request/user-supplied provider credentials can be silently ignored for registry-style model refs like `openrouter/...`.

### 12. MCP reload can re-register stale tools from removed servers

`mcp_reload` unregisters old names and clears `bridge._tool_to_server` at `src/sdk/tools_core/mcp.py:71-75`, but it does not clear `bridge._registry`.

`MCPToolBridge.discover()` only removes/replaces tools that are rediscovered, at `src/sdk/tools_core/mcp_bridge.py:90-101`. Removed-server tools can remain in the bridge registry and are re-registered by `bridge.get_tool_definitions()` at `src/sdk/tools_core/mcp.py:76-78`.

Runtime impact:

- After removing an MCP server and running `mcp_reload`, stale tools can remain callable in the active loop.

### 13. Gemini streaming parser does not handle the documented SSE framing

`src/sdk/providers/gemini.py:201-218` reads bytes and only parses array-like chunks containing `[` or `]`, splitting on `,\n`. It does not handle `data: {...}` SSE lines, even though the provider describes streaming as SSE.

Runtime impact:

- If Gemini returns SSE frames, streamed text/tool calls/usage are dropped or delayed until request end.

Coverage gap:

- Tests cover `_parse_stream_chunk()` with decoded dicts, not `chat_stream()` against SSE bytes.

### 14. Streaming tool calls lose arguments supplied in `tool_input_start`

`src/sdk/loop.py:1097-1110` reads `chunk.args` and computes runtime-context args, but stores `"arguments": ""` in `tool_calls_map`. Later reconstruction reads only stored arguments at `src/sdk/loop.py:957-970`.

Runtime impact:

- Providers that emit full tool args in `tool_input_start` and no `tool_input_delta` execute tools with `{}` even though UI events showed the correct args.

### 15. `AgentLoop.run_stream()` consumes usage chunks but never emits usage events

Usage chunks are accumulated and skipped at `src/sdk/loop.py:853-861` and `src/sdk/loop.py:895-901`. The loop updates cost tracking at `src/sdk/loop.py:880-884` and `src/sdk/loop.py:920-924`, but it does not yield `StreamChunk.usage_event(...)`.

Runtime impact:

- Streaming clients do not receive token/cost telemetry even when providers emit usage.

### 16. SDK handoffs are not wired into `AgentLoop`

`AgentLoop` stores handoffs and `_handoff_tool_names` at `src/sdk/loop.py:175` and `src/sdk/loop.py:186-188`, but handoffs are never registered as tools, detected as tool calls, or executed.

`src/sdk/handoffs.py:50-92` defines schemas and callbacks that are not used by the loop.

Runtime impact:

- Configured handoffs are invisible to the model and cannot execute.

### 17. Sync tools in parallel batches still block the event loop

`_execute_tool_batch()` uses `asyncio.gather()` at `src/sdk/loop.py:388`, but sync tools run directly with `tool_def.invoke()` at `src/sdk/loop.py:263-268`.

Runtime impact:

- Read-only sync tools do not actually run concurrently and can stall streaming/other async work.

### 18. `subagent_delegate()` records delegated jobs as `pending` while running

`src/sdk/coordinator.py:329-335` inserts the task then runs `_run_loop()` without `set_running()` or `claim_task()`.

Comparison points:

- `invoke()` calls `set_running()` at `src/sdk/coordinator.py:273-275`.
- Background jobs claim tasks at `src/sdk/coordinator.py:397-400`.

Runtime impact:

- `subagent_tasks` can show delegated tasks as pending while they execute.
- Cancellation and stale recovery behavior are wrong for those rows.

### 19. `_run_async()` timeout leaves the coroutine running

`src/sdk/tools_core/subagent.py:54-72` waits on `future.result(timeout=...)`, but the timeout path never calls `future.cancel()`.

Runtime impact:

- After timeout is reported, the operation may continue mutating state in the background.

### 20. Intended `_run_async()` timeout tests are not collected

`tests/sdk/test_subagent_tools_async.py:373-403` defines tests inside `test_subagent_delegate_is_parallel_safe()`, so pytest does not collect them.

Evidence:

- `uv run pytest tests/sdk/test_subagent_tools_async.py --collect-only -q` collected 16 tests and did not include `test_run_async_respects_timeout`.

### 21. Fixed: workspace file edits versioned into the wrong workspace

`files_edit()` calls `capture_version(user_id, path, new_content)` at `src/sdk/tools_core/filesystem.py:219-221`, omitting `workspace_id`.

`capture_version()` defaults to `workspace_id="personal"` at `src/sdk/tools_core/file_versioning.py:39`.

Runtime impact:

- Editing a file in a non-personal workspace writes version history under the personal workspace.

### 22. Todo update/delete report success for nonexistent IDs

`src/sdk/tools_core/todos_storage.py:158-163` and `src/sdk/tools_core/todos_storage.py:170-175` execute `UPDATE`/`DELETE`, commit, and return `{"success": True}` without checking row count.

Runtime impact:

- Users are told a todo was updated/deleted even when the ID does not exist.

### 23. Synced email read/unread state is inverted

`src/sdk/tools_core/email_db.py:230` and `src/sdk/tools_core/email_sync.py:127` set `"read": not msg.flags.Seen`.

IMAP `Seen` means the message has been read.

Runtime impact:

- Read emails appear unread and unread emails appear read.

### 24. Incremental email sync can permanently miss messages beyond the first fetch page

Quick sync reads `last_timestamp` at `src/sdk/tools_core/email_sync.py:303`, but does not use it to constrain or page IMAP fetches. It fetches only `mailbox.fetch(limit=limit, reverse=True)` at `src/sdk/tools_core/email_sync.py:317`.

Runtime impact:

- If more than `limit` new messages arrive between syncs, older new messages beyond the first page can remain permanently unsynced.

### 25. Deleting a nonexistent app returns success

`_get_app_path()` creates the app directory at `src/sdk/tools_core/apps.py:86-92`. `_delete_app()` calls `_get_app_path()` at `src/sdk/tools_core/apps.py:134-140`, then sees the newly-created path and deletes it.

Runtime impact:

- `app_delete("missing")` can report success instead of not found.

### 26. Memory search ignores workspace isolation

`_get_memory_core(user_id, workspace_id)` accepts `workspace_id`, but uses cache key `f"{user_id}:memcore"` and calls `get_paths(user_id)` without workspace at `src/sdk/tools_core/memory.py:21-37`.

Runtime impact:

- Memory search/injection can reuse the first workspace's MemoryCore for every workspace for the same user, causing cross-workspace leakage or missed retrievals.

### 27. Companion workspace activity reads from `default_user`

`_summarize_workspace_activity(workspace_id)` calls `get_message_store(workspace_id=workspace_id)` at `src/sdk/companion_scheduler.py:259-265`, omitting `user_id`.

`get_message_store()` defaults to `user_id="default_user"`.

Runtime impact:

- Companion check-in context can omit the real user's activity or show `default_user` activity.

### 28. Observation reflector never starts from accumulated observations

`ObservationMiddleware.after_agent()` checks the reflector threshold using only `get_latest_reflection()` token count at `src/sdk/middleware_observation.py:86-93`. When no reflection exists, the count is zero.

Runtime impact:

- The reflection/condensation phase does not start for a user who has observations but no prior reflection.

### 29. Observer repeatedly processes the same 30-day message window

`_unobserved_since` is initialized at `src/sdk/middleware_observation.py:48` but not used. `_fire_observer()` always reads the last 30 days / 500 messages at `src/sdk/middleware_observation.py:175-190`.

Runtime impact:

- Once the threshold is reached, observer runs repeatedly over already-observed messages, increasing LLM cost and creating duplicate/redundant observations.

### 30. Chroma memcore backend drops timestamps on retrieval

`ChromaBackend.ingest_batch()` stores `ts` metadata at `src/memcore/src/memcore/backends/chroma.py:40-48` and `51-66`, but `search()` reconstructs `Memory` without `ts` at `src/memcore/src/memcore/backends/chroma.py:93-99`.

Runtime impact:

- Temporal scoring cannot use stored timestamps for Chroma-backed search results.

### 31. Hybrid memcore backend `get_recent()` drops session IDs

`HybridBackend.ingest_batch()` stores `session_id` in metadata at `src/memcore/src/memcore/backends/hybrid.py:72-83`, but `get_recent()` reconstructs `Memory` with only `id`, `content`, and `role` at `src/memcore/src/memcore/backends/hybrid.py:165-183`.

Runtime impact:

- Session-scoped wake-up context cannot include recent memories when using HybridBackend.

### 32. DuckDB journal sync likely breaks for custom/text primary keys

`src/sdk/hybrid_db.py:520-541` syncs using `_journal.row_id`, which stores SQLite internal `rowid`, but DuckDB deletes/inserts using user-facing `id`.

Runtime impact:

- Tables with non-autoincrement IDs can silently miss inserts/updates/deletes in DuckDB analytics.

### 33. DuckDB sync failure drops journal entries

`src/sdk/hybrid_db.py:2299-2308` logs `duckdb.sync_failed` but still deletes journal entries.

Runtime impact:

- Transient DuckDB failures can permanently lose pending analytics sync operations.

### 34. `insert_batch()` journal metadata omits DB defaults

`src/sdk/hybrid_db.py:1183-1194` builds journal metadata from filtered input instead of re-reading inserted rows, unlike single insert at `src/sdk/hybrid_db.py:1087-1099`.

Runtime impact:

- Default/generated values are missing from Chroma metadata for batch inserts, causing filtered vector searches to miss rows.

## Test/Static Check Failures Worth Tracking

These are not all independently root-caused, but they are concrete backend health signals from the broad run:

- `tests/sdk`: `16 failed, 797 passed`
- Notable failures: `DoneMessage.tool_calls`, `MemoryMiddleware.before_agent` conformance mismatch, OllamaCloud provider type expectations, missing `sqlalchemy`, missing `memcore` import path.
- `tests/unit`: `81 failed, 122 passed`
- Notable failures: contacts/email/todos imports and mock paths, filesystem workspace path expectations, memory search import path, skill registry constructor drift, `time_get` rejecting injected `user_id`.
- `tests/storage`: `20 passed`
- `tests/api/test_ws_protocol.py` plus one SDK protocol test: `3 failed, 30 passed`
- `tests/api/test_memories.py -x`: first failure `422` on add-memory minimal request.
- `ruff`: 110 findings; runtime-relevant findings are captured above, while many are import ordering/unused-variable hygiene.
- `mypy src`: blocked early by `eval-viewer is not a valid Python package name`; targeted mypy showed real issues in summarization.

## Recommended Fix Order

1. Apply auth consistently or explicitly document unauthenticated local-only routes.
2. Fix `DoneMessage.tool_calls` vs `tools_called` protocol mismatch.
3. Fix `summarize_session` state access and `non_system_old` crash.
4. Add provider-specific context overflow mappings before relying on overflow recovery.
5. Fix workspace isolation defects: workspace file routes, conversation clear, tool-message metadata, memory core cache, file versioning.
6. Fix memory REST request models/undefined variables if the Flutter memory panel remains supported. This is not on the agent `memory_search` path, but it is still UI/API-facing.
7. Fix subagent delegate state transitions and `_run_async()` cancellation; move nested tests to collected scope.
8. Fix tool/storage correctness issues: todo rowcount, email read flag, incremental email pagination, app delete semantics.
9. Fix core SDK streaming correctness: start-chunk args, usage events, handoffs wiring, sync tool concurrency expectations.
10. Add HybridDB regression tests for custom IDs, DuckDB failure retention, and batch metadata defaults before changing storage behavior.
11. Make `memcore` importable in normal test/runtime environment or update imports to package-relative project paths.
12. Re-run split backend suites and then the combined backend suite after fixes.
