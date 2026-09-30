# E2E Bug Audit Addendum — Cross-Layer Seam Findings

**Date:** 2026-08-23 · **Companion to:** `2026-08-23-performance-bug-audit.md` (ids E*, new)
**Method:** 4 read-only lanes tracing full request flows across layers (lifecycle/persistence, streaming wire contract incl. Zig client, tool-capabilities flow, config/auth/deployment). Known S*/B*/P* items and parked subagent files excluded.

---

## Blockers

### E22. Docker/Solo-WAN deployment is broken — port hardcoded
`[blocker|high]` `src/http/main.py:258` ↔ `docker/docker-compose.yaml`, `docker/Caddyfile`
`run()` binds `uvicorn.run(app, host="0.0.0.0", port=8080)` ignoring `ApiConfig.host/port`; compose maps `8000:8000`, healthchecks `localhost:8000/health`, Caddy proxies `app:8000`. Container listens on 8080 → host port dead, healthcheck permanently failing.
**Fix:** read `settings.api.host/port` in `run()`; verify with `docker compose up --build`.

### E23. config.yaml/.env loaded CWD-dependently — silently ignored outside repo root
`[high|high]` `src/config/settings.py:390` (also `:329`)
`get_settings()` passes relative `"config.yaml"` to `from_yaml()`, bypassing the repo-root resolution branch its own docstring promises. Launched from any other directory (systemd/launchd/`uv run --project`) ALL yaml settings silently fall back to code defaults — incl. shell allowlist (`agent-browser`) and summarization trigger.
**Fix:** call `from_yaml()` with no arg (repo-root resolution), same for `.env`.

### E24. Capabilities bypass: disabled tools remain executable via lazy-load + stale index
`[blocker|high]` `loop.py:539–583` ↔ `runner.py:422` ↔ `tool_index.py:240–247` ↔ `tools.py:160–184`
`PATCH /tools/{name}` scope=none resets loops but never purges the persisted tool index; indexing is skipped whenever `idx.count() > 0`; `_try_lazy_load` consults the *unfiltered* global registry and applies no caps check. Net effect: disable `files_delete` → system prompt even advertises it (stale index count) → `tool_search` finds it → it **executes despite scope=none**.
**Fix:** check `resource_enabled(caps, "tools", tc.name)` in `_try_lazy_load` before register/execute; purge/disable index rows on PATCH.

### E25. Follow-up steers can never fire — loop unregistered before consumer runs
`[high|high]` `run_service.py:781` ↔ `ws.py:936–941`
`execute_stream`'s finally unregisters the loop from `_user_loops` when the stream ends; the WS handler checks pending follow-up steers only *after* stream completion → `get_user_loop(...)` is always None. Users see "steer accepted" followed by silence; the documented Pi-style follow-up feature is unreachable.
**Fix:** return pending-steer state in `DoneEvent`, or invoke a pre-unregister callback from `execute_stream`.

### E26. Two SessionWorkerRegistry instances — cross-transport busy guard doesn't exist
`[high|high]` `conversation.py:49` ↔ `ws.py:59`
REST/SSE and WS each build their own registry + RunService; a WS run and an SSE run on the same `(user_id, session_id)` run concurrently on the *same cached* AgentLoop whose `state` is rebuilt per run — the second run overwrites the first's state mid-flight (duplicate-guard counters, cost tracker, audit records). Also the REST approve endpoint bypasses locking entirely.
**Fix:** module-singleton registry shared by both routers; acquire the same lock in the approve path.

---

## Streaming wire contract (backend ↔ native Zig client)

- `[high|high]` `ws.py:171–262` ↔ `ws_protocol.py:88–95,401` — WS emits RunEvent **envelopes** but `parse_server_message()` ("the contract") parses **flat** shapes; every streamed delta is unparseable by protocol-conforming clients. Pick one shape and align tests.
- `[medium|high]` `conversation.py:884–891` ↔ `main.zig:784–795` — failed runs reach SSE clients as a silent `done(status=failed)`; the Zig done handler never checks `status`. WS converts to ErrorMessage. Transports disagree; primary one hides failures.
- `[medium|high]` `conversation.py:1236` ↔ `main.zig:796–820` — approve-stream catch-all yields `{"content": ...}` (no message/code); native error branch early-returns → HITL failures vanish silently.
- `[medium|high]` `conversation.py:65–81 vs :1128–1215` — `/message/approve` uses a legacy-flat serializer (no envelope fields, no starts/ends/usage/rubric events) while `/message/stream` uses canonical envelopes; same Zig parser consumes both.
- `[medium|high]` `conversation.py:1073` — approve/resume stream has NO heartbeat wrapper; long post-approval tools starve the native liveness clock while the main stream stays fed.
- `[low|high]` `run_service.py:277–290` — text/reasoning start blocks mint random UUIDs as block_id while deltas/ends use literal `"text"`/`"reasoning"` — violates the canonical correlation contract.
- `[low|high]` `run_service.py:316–325` — `ToolResultEvent.status` hardcoded `"completed"`; StreamChunk has no failure channel, so failed tools are wire-indistinguishable.
- `[low|medium]` ws.py drops `usage` events entirely; `DoneMessage.cost_usd`/`total_llm_calls` never populated.

## Lifecycle & persistence seams

- `[medium-high|high]` `conversation.py:1304–1320` ↔ coremem ↔ `messages.py` — `/conversation/import` stores everything under `session_id=""` while queries run in `"default"`: **imported eval history is invisible to the agent**, defeating LongMemEval preloading. Accept `session_id` in the import model and pass through.
- `[medium|high]` cancel is a **no-op for non-streaming runs**: `cancel_message` never calls `SessionWorkerRegistry.stop()` (zero callers repo-wide); POST /message returns "cancelled" while the run continues and the session stays locked until completion.
- `[medium|medium-high]` `messages.py:1212` — `delete_session` deletes SQL rows only; ChromaDB vectors + `_journal` rows survive → deleted chats still surface via memory/hybrid search (privacy gap).
- `[medium|high]` cancelled/interrupted SSE turns persist without `run_id` → fragment into up to three unrelated "turns" in `get_turns`.
- `[low-medium|high]` reasoning fidelity differs by transport: REST `_run` never persists or returns reasoning; streaming does.
- `[low|high]` `workspaces.py:96–104` — workspace deletion hardcodes `messages_deleted: 0`; cleanup helpers are dead code; legacy sessions linger forever.
- `[low|medium]` deleting a session during an active run lets `persist_run` resurrect zombie rows (answer without user row).

## Prompt/config drift (capabilities vs injected prompt)

- `[medium|high]` `runner.py:225` ↔ `shell.py:16–24` — conditional guideline tells the model to use `ls, rg, find` via shell_execute, none of which are in the allowlist → guaranteed-fail loop exactly when dedicated tools are disabled. Derive guideline from actual allowlist.
- `[medium|medium]` `runner.py:577–586` — unconditional "## Tool Preferences" block advertises capability-disabled tools (the memory section was made conditional; this one was missed).

## Smaller verified items

- `[low|high]` `email_sync.py:17` — module-level `SETTINGS = get_settings()` freezes config at import; reload never applies.
- `[low|high]` `runner.py:1484–1494` — reset/create race can resurrect a stale-caps loop (`reset_user_sdk_loops` mutates cache without `_loop_lock`).
- `[low|high]` `conversation.py:45` area — invalid `user_id` on some routers → raw ValueError → 500 instead of 400 (workspace/todos/email/memories lack pre-validation).
- Note: WS hardcodes `user_id="default_user"` regardless of auth; `workspace_id` param accepted everywhere but has zero isolation effect outside research_dir; `ToolResult.structured_content/audience` are consumed by nothing (dead API surface today); fresh MCP tools aren't added to `tool_index` until config hash changes; SSE `"cancelled"` event has no native handler (benign).

## Verified sound (no action)

WS first-message auth handshake covers the HTTP-middleware gap for upgrades; `_validate_path_id` blocks id traversal on all DataPaths consumers; per-user storage scoping has no cross-user leak; summarization checkpoint round-trip reproduces in-memory framing on reload; `persist_run` idempotency holds on success paths; `mcp_reload` genuinely affects mid-run payloads.

## Not fully verified (follow-up during fixes)

Native watchdog timeout constant; real-world population of `parse_server_message` clients; interrupt re-injection after reject on REST; steer_ack consumers beyond server code.
