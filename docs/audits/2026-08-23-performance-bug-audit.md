# Deep Bug Hunt & Performance Audit — Findings & Refactor Specs

**Date:** 2026-08-23 · **Scope:** full Python backend (`src/sdk`, `src/http`, `src/storage`, `src/subagent`)
**Method:** 5 parallel read-only review lanes (SDK engine, middleware/context, providers, tools_core/DB, HTTP/storage) + manual hot-path tracing. **No code was changed.**

Each finding: `[severity|confidence]` → file:line, problem, and a concrete fix spec.
Cross-lane duplicates are merged; systemic issues are grouped under "Systemic" first.

---

## 0. Systemic Themes (highest leverage)

### S1. Sync tools run directly on the event loop ⛔ worst latency class in the repo
`[high|high]` `src/sdk/loop.py:524–527`

`AgentLoop._execute_tool` invokes non-coroutine tools via bare `tool_def.invoke(...)` on the loop thread. Since virtually every tool in `tools_core` is synchronous (SQLite, file I/O, `subprocess`, IMAP, SentenceTransformer inference, HybridDB/Chroma search), **every tool call blocks all concurrent sessions, WS streams, SSE streams, steer and ping/pong for the whole process.** Concrete instances:

- `subagent_*` tools block up to **300 s** (`subagent.py:36,55` `future.result(timeout=300)`)
- custom tools run `subprocess.run(..., timeout=120)` via `tool_index.py:43–55`
- `shell_execute` up to its 30 s subprocess timeout (`shell.py:110–116`)
- `tool_search` / `_try_lazy_load` run hybrid FTS+embedding search synchronously (`loop.py:517–531`, `tool_search.py:25`)
- apps.py lazily loads `all-MiniLM-L6-v2` inline (`apps.py:36–52`)

**Spec:** wrap sync tool functions in `await asyncio.to_thread(tool_def.invoke, tc.arguments)` inside `ToolDefinition.ainvoke` (or at registration time). One change fixes every module below.

### S2. Usage/cost accounting broken in four places
Undermines `CostTracker` budgets that the loop treats as safety rails:

1. `[medium|high]` `run_service.py:589–597` — streaming runs never populate `usage.agent`: code waits for usage on the `done` chunk, but providers emit separate `usage` chunks which are never summed. Streaming `RunResult` always reports zero tokens. **Fix:** accumulate from canonical `usage` chunks keyed by attempt, or attach cumulative usage to the final `done` event in `loop.py`.
2. `[medium|high]` `run_service.py:809–820` — non-streaming path counts only the *last* assistant message's usage per attempt → multi-step runs undercount N−1 LLM calls. **Fix:** sum over all assistant messages per attempt.
3. `[high|medium]` Gemini: `gemini.py:320–331` emits a cumulative `usageMetadata` event per chunk and `loop.py:1556–1562` **sums** them → tokens inflated ~50× on long responses. Anthropic has the mirror bug: `message_delta.usage.output_tokens` is cumulative but accumulated with `+=` (`anthropic.py:332–343`, `loop.py:1577–1583`). **Fix:** emit usage only on terminal chunks (or take last-seen instead of summing).
4. `[medium|high]` `loop.py:126–147` — `CostTracker.add_usage` ignores `cache_read_tokens`/`cache_creation_tokens` (Anthropic bills 0.1×/1.25×) and excludes them from token limits. **Fix:** accept full `Usage` with cache multipliers.

### S3. Providers are rebuilt (and never closed) per request
`[high|high]` `factory.py:247–303`; call sites `conversation.py:499`, `runner.py:348`, `research.py:86`, `coordinator.py:490`, `agent_scheduler.py:113`

A fresh provider = fresh `httpx.AsyncClient` / `AsyncOpenAI` with cold TLS pool, on nearly every request. No memoization by `(provider_type, model, api_key)` exists, so httpx connection pooling is theoretical. Additionally no provider exposes `aclose()` — pools leak until GC (also rubric creates a fresh grader provider per run, `middleware_rubric.py:46–48`).

**Spec:** keyed LRU provider cache in the factory with invalidation on settings change (generalize the pattern already used in `middleware_summarization.py:751`); add `async def aclose()` to providers and wire into loop/session teardown.

### S4. Event-loop-blocking storage I/O in async paths
`[medium|high]` across `http/routers/conversation.py`, `ws.py`, `storage/messages.py`, `registry.py`

- Every async handler calls MessageStore's sync SQLite + ChromaDB directly; first construction per user runs migrations synchronously row-by-row (`messages.py:117–330`) stalling all requests.
- `registry.py:195` does `urlopen(timeout=10)` + sync cache reads inside async routers (cold start can stall the server ~10 s).
- `gmail_cache.py:359–373` `subprocess.run(timeout=120)` reachable from async contexts.

**Spec:** offload via `asyncio.to_thread` or make endpoints sync `def`; move migrations to startup; single-flight lock around registry `_ensure_loaded` (currently also an unlocked module-global race).

---

## 1. Critical Bugs

### B1. Duplicate-tool-call guard produces provider-invalid history (breaks strict APIs)
`[high|high]` `loop.py:1302+1335–1359` (non-streaming) and `~1706+1755–1790` (streaming)

When the Ralph-loop duplicate guard fires, the assistant message *with* `tool_calls` is already in state, but the nudge path appends only a `Message.system(...)` — no `tool_result` for those call IDs. OpenAI-compatible APIs and Anthropic reject dangling `tool_calls` with hard 400s, so instead of a soft nudge the run dies.
**Fix:** before nudging, append synthetic `Message.tool_result(tool_call_id=tc.id, content="duplicate — see earlier result")` per duplicate call (+ corresponding `tool_result` stream events).

### B2. Mid-conversation system nudge replaces the whole system prompt on Anthropic
`[high|high]` `anthropic.py:82–85`

`_build_payload` keeps only the **last** system message as payload `system`. A duplicate-guard nudge appended as `Message.system(...)` therefore silently drops the agent's real system prompt whenever a nudge exists.
**Fix:** concatenate all system contents in `_build_payload`, or emit nudges as `user` messages.

### B3. SSE heartbeat wrapper hot-spins on every streamed response
`[high|high]` `conversation.py:718–742`

`_sse_with_heartbeat` never discards completed tasks from `pending`. After the first event, stale done tasks make `asyncio.wait(FIRST_COMPLETED)` return immediately forever → CPU-bound spin emitting `": ping"` as fast as the client drains, for the entire duration of every SSE stream. Also allocates 2 tasks per delta.
**Fix:** rebuild `pending` each iteration, or rewrite as queue + single reusable timer / `asyncio.timeout`.

### B4. HITL approve-loop bug half-fixed — three WS paths still loop forever
`[high|medium]` `ws.py:869, 613, 911` (fixed path: ws.py top-level ApproveMessage → `_execute_approved_tool`)

Deferred-control approval wait and both edit-and-approve paths still retry with an instruction-only user message after `approve_tool_call(...)`. The model re-proposes with a fresh call id/args, approval match never fires, agent re-interrupts — the exact documented approve loop.
**Fix:** route all four paths through `_execute_approved_tool` (execute edits with edited args).

### B5. Shared bridge loop destroyed on any subagent-tool error; thread leak
`[high|high]` `subagent.py:64–79, 39`

`_run_async` catches *every* exception (including benign `ValueError("Subagent disabled")`) and calls `_recreate_loop()`, orphaning all other coroutines already submitted to that loop (callers hang until the 300 s timeout). Recreation leaks a daemon thread each time (old loop never stopped/joined).
**Fix:** recreate only on loop-level failures; propagate domain exceptions; stop/close old loop and join thread.

### B6. Gemini streaming parser is fundamentally fragile
`[high|high]` `gemini.py:225–247`

Hand-rolled JSON-array splitting buffers until the final `]\n` (TTFT == full generation time — no incremental tokens), splits multi-byte UTF-8 across network chunks (mojibake/`UnicodeDecodeError`), and silently drops events on any parse failure. Related `[medium|medium]` `gemini.py:171–188, 270–313`: thought-summary text leaks into the visible answer and `Message.reasoning` gets set to the boolean flag (`True`) instead of text.
**Fix:** switch to `?alt=sse` and parse `data:` lines like `anthropic.py:226–250`; use `codecs.getincrementaldecoder("utf-8")`; check `part.get("thought")` before `"text" in part`.

### B7. Email read/flagged state always wrong
`[medium|high]` `email_db.py:230`, `email_sync.py:127`

imap_tools ≥1.x `msg.flags` is a tuple of strings, but code checks `hasattr(msg.flags, "Seen")` → always False → every email stored as `read=True, flagged=False`; downstream "urgent/unread email" counting (`agent_scheduler.py:157–166`) always reports zero.
**Fix:** `'SEEN' in msg.flags` / case-insensitive `'FLAGGED'` membership test.

### B8. Version retention deletes nothing; `.versions/` grows unboundedly
`[medium|high]` `file_versioning.py:214–245`

Versions iterated ascending, so "newer ts beats stored one" is true for every version → all kept, none unlinked; `files_versions_clean` always reports 0. Dead `pass` branches confirm broken intent.
**Fix:** track newest per bucket, unlink non-newest afterwards.

### B9. Arbitrary-file read/write/delete via `version` parameter (path traversal)
`[medium|high]` `file_versioning.py:150–160, 186–199`

`files_versions_restore` does `ver_dir / version` unvalidated → crafted relative path copies arbitrary file content into the workspace (read primitive); `files_versions_delete` unlinks/rmtree's attacker-chosen paths. `_resolve_path` validates only `path`, not `version`. Same class: `coordinator.py:455–463` uses raw `name` in `base_path / name` + `rmtree`.
**Fix:** validate `version` against `^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}$` and enforce `resolve().is_relative_to(ver_dir)`; validate coordinator `name`/`load_def`/`update` with `_PATH_ID_RE`.

### B10. Personality decay destroys facts immediately
`[medium|high]` `agent_scheduler_db.py:212–218`

`_apply_decay` predicate `updated_at < _now()` is always true and never bumps `updated_at` → every cycle shaves 0.01 confidence off *every* fact; after ~40 cycles all facts drop out (`confidence > 0.1` filter).
**Fix:** real cutoff (`now - 1 day`) and set `updated_at` in the UPDATE.

### B11. Failed runs persisted twice (corrupt transcripts)
`[medium|high]` `run_service.py:700–708` × `conversation.py:893–904` × `ws.py`

`persist_run` runs unconditionally even on FAILED, then the router's failed branch additionally persists collected state without `run_id` → duplicated tool results/reasoning/assistant text in `/conversation` and `/conversation/turns`.
**Fix:** skip `persist_run` on FAILED, or make router persistence conditional on absence of `persisted_id`.

### B12. Concurrent same-session stream request clobbers cancel registration
`[medium|high]` `conversation.py:757–759, 1076–1077` vs `1285`

Cancel flags / active-stream slots are reset *before* the busy-check fires lazily inside `generate()`. Stream B wipes stream A's cancel flag, then fails busy and pops the slot → cancel stops working for the live stream A.
**Fix:** try-acquire the session slot atomically before touching the dicts (or key by run id).

### B13. Registry TTL is fiction; offline fallback collapses to 4 models
`[medium|high]` `registry.py:328–343, 334–341`

`_ensure_loaded` short-circuits forever after first load (the advertised 5-min TTL gates only the initial disk read; `refresh()` is exported but never called). Worse, expired cache + fetch failure falls through to the 4-model builtin, discarding thousands of valid stale models until restart.
**Fix:** honor TTL vs `_last_fetch_time` with background/single-flight refresh; on fetch failure reload stale cache ignoring expiry before builtin.

### B14. Summarization retry policy is dead code
`[high|high]` `middleware_summarization.py:806`

Retries on builtins `(ConnectionError, TimeoutError, OSError)`; providers raise `httpx.*Timeout/ConnectError` or OpenAI `APITimeoutError/APIConnectionError` — none inherit from these. The documented one-retry transient absorption never happens.
**Fix:** use `is_timeout_error(exc)` from `providers.base` (and extend it to cover `WriteTimeout`/`PoolTimeout`, `base.py:12–18`).

### B15. Malformed tool-call JSON silently becomes `{}` on OpenAI path
`[medium|high]` `openai.py:171–177`

On `JSONDecodeError`, args are set `{}` and the tool executes with missing required args. `validation.repair_tool_call` exists and is used elsewhere (`ollama.py:110`, `loop.py:1694`).
**Fix:** fall back to `repair_tool_call(...)`.

### B16. CORS allows any origin with credentials
`[medium|high]` `main.py:67–74`

`allow_origins=["*"]` + `allow_credentials=True` lets any website ride authenticated sessions in Solo-WAN mode.
**Fix:** enumerate trusted origins or drop credentials.

### B17. Misc correctness (each verified)
- `[medium|medium]` `email_sync.py:303–360` — "new" incremental sync ignores `last_timestamp` entirely; >limit new mails between syncs are permanently missed. Fetch by UID/date watermark like "full" mode.
- `[medium|medium]` `work_queue.py:~385–397` — `add_instruction` read-modify-write loses concurrent instructions. Use SQL `json_insert(instructions,'$[#]',…)` or `BEGIN IMMEDIATE`.
- `[medium|medium]` `agent_scheduler_db.py:47–54` — unlocked lazy `aiosqlite.connect` init races (mirror WorkQueueDB's `asyncio.Lock` pattern).
- `[medium|medium]` `messages.py:697–707` — `update_session_title` writes to un-ordered `memories[0]` while lookup reads oldest-first; verify ordering or use explicit `ORDER BY ts ASC LIMIT 1` for both.
- `[medium|medium]` `anthropic.py:292–338` — in-stream `type:"error"` SSE events silently swallowed → empty successful response. Map to `StreamChunk.error`.
- `[medium|high]` `openai.py:257–287`, `anthropic.py:281–291` — per-event-site fallback `call_{uuid}` mints different ids for start/delta/end → clients correlating by call_id see unpaired blocks; retry guard (`emitted`) misses starts → duplicated/orphan starts on retry. Compute id once per call; count starts in guard.
- `[medium|medium]` `loop.py:862–864` — streaming batch lacks the `BaseException` branch its non-streaming twin has (`757–771`) → cancelled child task crashes tuple-unpack mid-stream.
- `[low|high]` `loop.py:1483–1509` — input-guardrail task leaked on early-exit paths (wrap in try/finally).
- `[low|high]` `validation.py:24–28` — `normalize_tool_schema` pops keys from the caller's dict (copy first).
- `[low|high]` `conversation.py:45, 609–624` — `_pending_approvals` dead branch returns fabricated "approved (execution pending)" responses; delete.
- `[low|medium]` `main.py:44–59`, `auth.py:29–34` — silent forever-swallowing refresh loop; localhost bypass misses `::ffff:127.0.0.1`.
- `[low|medium]` `workspace_cache.py:63–79` — entries without `server_modified` permanently report "has update"; FileCache read-modify-write is non-atomic.
- `[low|medium]` `todos_storage.py:44` — 8-char UUID ids collide at scale → uncaught IntegrityError.
- `[low|medium]` `apps.py:268–291` — date-word rewriting hits string literals (`LIKE '%today%'` rewritten); "last month" in January resolves to future December.
- `[low|medium]` `contacts_storage.py:170–260` — claimed INSERT OR IGNORE is actually SELECT+INSERT with non-unique index (duplicate contacts under concurrency); stale `last_name` after rename to single word.
- `[low|high]` `message.py` `message_count` output claims N workspaces searched but only current workspace queried.
- `[low|medium]` `middleware_rubric.py:155–213` — greedy JSON-extraction regex turns malformed verdicts into terminal `grader_error`; empty criteria list makes `needs_revision` vacuous (burns attempts); grader transcript can pick up compression summary as "first user message".
- `[note]` `loop.py:372–395` — HITL `_should_interrupt` hard-disabled (documented intentional); flagged so it isn't mistaken for a working safety control. Also `subagent_delegate` annotated `read_only=True` though delegation executes arbitrary tools (`subagent.py:374–380`).
- `[note]` `registry_update.py:60–124` — hand-rolled TOML parser (use stdlib `tomllib`) and a fetch-all-then-discard loop (dead work).

---

## 2. Performance Refactor Specs

### P1. Email sync: engine-per-call + commit-per-row (10–100× win)
`[high|high]` `email_db.py:23–28`, `email_sync.py:230–249, 331–350`
New SQLAlchemy engine + full DDL per invocation (never disposed); `_sync_folder` opens a connection and commits **per email** (500-message backfill = 500 connects + 500 fsync commits).
**Spec:** cache engines per user (pattern exists in `contacts_storage.py:20–27`); one connection per batch, single commit per batch.

### P2. O(session-length) work on every message send
`[medium|high]` `messages.py:724–800`
`get_messages_with_summary` (called on every REST/SSE/WS turn) batch-scans backwards for summaries and loads entire prefixes to filter in Python.
**Spec:** cache validated summary rowid per (user, session), invalidate on writes; select only needed tail rows.

### P3. Token counting redundancy in summarization/context measurement
`[medium|high]` `middleware_summarization.py:226–238, 365, 494`; `context_measurement.py:85–101`
Binary-search cutoff re-walks + `json.dumps`es suffix slices repeatedly; context measured 2–3× per LLM call (`loop.py:1006–1055` prepare/record/project each tokenize full history); sync `before_model` pays a full count then discards it.
**Spec:** memoize per-message token counts (content-hash key), suffix-sum arithmetic; reuse the prepared snapshot between measure points; no-op the sync hook unless debug logging.

### P4. Unbounded scans/growth
- `[medium|high]` `work_queue.py:337–396` — `check_progress` no LIMIT, terminal rows retained forever → add `ORDER BY created_at DESC LIMIT` defaults + retention prune.
- `[low|high]` `gmail_cache.py:296–299` — `clear()` materializes ≤100k rows then deletes row-by-row → single `DELETE FROM emails`.
- `[low|medium]` `gmail_cache.py:216–243` — SELECT-then-upsert, no UNIQUE on `message_id` → UNIQUE index + `ON CONFLICT DO UPDATE`.
- `[low|medium]` `shell.py:120–133` spilled outputs, `email_sync.py:19` cooldown rows, dismissed notifications — add TTL cleanup hooks.
- `[low|medium]` `runner.py:47,645–651` — loop-cache LRU eviction can evict a loop belonging to an in-flight session (>50 sessions) → refuse evicting keys present in `_user_loops`.

### P5. File search/read efficiency
`[medium|high]` `file_search.py:66–125`, `filesystem.py:96–99`
`files_grep_search` rglobs everything (no ignore/binary/vcs skipping), reads files ≤10 MB fully, collects all matches before truncating to 100; `files_glob_search` stats matches twice, unbounded; `files_read` slurps entire file to slice lines.
**Spec:** cap walk depth/results early, skip dot-dirs/binaries, stop collecting past display cap; stream reads via `itertools.islice` + max-size guard.

### P6. Small but free wins
- `[medium|high]` `coordinator.py:74–82` — capabilities YAML loaded from disk once **per tool** in `disabled_tools` comprehension → hoist one load.
- `[medium|medium]` `paths.py:315–326` — `get_paths(workspace_id=...)` deliberately excluded from cache key → fresh DataPaths (2 mkdirs + settings access) per call → include workspace_id in key or memoize dirs.
- `[medium|medium]` `messages.py:1000–1010`, `962–965` — `persist_run` idempotency probe full-scans `json_extract(metadata,'$.run_id')` → generated column + index (same for `delete_messages_for_workspace`).
- `[medium|medium]` providers emit canonical + alias events (~2× Pydantic objects per token; aliases discarded immediately in `loop.py:1940–1942`) → emit aliases once centrally at the transport boundary.
- `[medium|medium]` `mcp_manager.py:206–216` — config JSON re-parsed per `get_tools()` (cache keyed by mtime); `mcp_bridge.py:69–95` iterates connections without manager lock (snapshot under lock).
- `[low|medium]` `tool_index.py:154–158` — `clear()` is discarded-query + N+1 deletes; index does SELECT-before-write per tool (~82 at startup) → bulk delete/upsert. Also fix hash persistence order (`tool_index.py:246–247`): hashes saved before indexing completes → crash leaves permanently stale partial index.
- `[low|medium]` `run_service.py:340` — unmapped StreamChunk types become empty text deltas on the wire; per-call `import json`; hardcoded `llm_call_index=1` → drop unknown types, hoist import.
- `[low|medium]` `middleware_summarization.py:252–256` — non-atomic prompt seeding → temp file + `os.replace`.
- `[low|medium]` `ws.py:640–700` — per-chunk receive tasks can drop frames at boundaries → single long-lived reader queue.
- `[low|medium]` `messages.py:1097–1180` — `get_turns` splits runs across page boundaries → carry open run across batches.
- `[low|medium]` `ollama.py:186–206` — dedup suppresses starts but still emits orphan ends/deltas.
- `[low|medium]` `factory.py:113` — OllamaCloud default 45 s timeout covers whole non-streaming response → distinct connect/read timeouts.
- `[low|medium]` `gemini.py:110` — API key in URL query → `x-goog-api-key` header.
- `[low|high]` `middleware_summarization.py:573–586` — inner framing line left in `<previous-summary>` payload → loop prefix stripping with `while`.
- `[low|medium]` `middleware_summarization.py:1055–1062` — sink failure discards a paid-for summary and retries spend next iteration → degrade to base_result or cache generated summary.
- `[low|high]` two divergent storage→SDK converters (`run_service._storage_messages_to_sdk` vs `runner._messages_from_conversation`) → consolidate before they diverge further.

---

## 3. Recommended Fix Order

| Wave | Items | Why first |
|---|---|---|
| 1 | S1 (to_thread offload), B3 (SSE spin), B1+B2 (duplicate-guard pair), S2.1–2.3 (usage plumbing) | Process-wide stalls; every-SSE-response CPU burn; guard breaks runs on strict providers; cost limits are safety rails |
| 2 | B4 (HITL loops), B5 (bridge teardown), B6 (Gemini SSE), S3 (provider cache), B13/B14 (registry + retry) | Correctness of core interaction loops; TTFT and connection reuse |
| 3 | B7–B12, B15–B17, P1 (email batching) | Feature-level bugs, biggest targeted perf win |
| 4 | Remaining P-items, low-severity list | Hygiene and scaling headroom |

## 4. Verification Plan (per wave)
- Wave 1: `uv run pytest tests/sdk/test_sdk_loop.py tests/sdk/test_run_service.py -v`; new regression tests for dangling tool_results and nudge-as-system on Anthropic; SSE heartbeat timing test (assert no ping burst).
- Wave 2: WS approve-path integration test (approve → execute, no re-interrupt); Gemini SSE fixture stream; factory cache-hit assertion.
- Wave 3: unit tests per bug (flags parsing fixture, retention algorithm table-test, traversal rejection test, decay cutoff test); email backfill benchmark before/after.
- Full gate: `uv run pytest && uv run ruff check src/ && uv run mypy src/`.
