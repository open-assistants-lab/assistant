# Assistant - Agent Guidelines

This document provides guidelines for agents working on this codebase.

## Product direction and documentation authority

- **One shared engine, two products:** a builder platform and a future finished native product. Current non-bug priority is builder/deployer adoption; macOS product work is parked, not a prerequisite.
- Builders own profiles, custom tools, skills, authored ontology and deterministic domain safeguards. Deployment teams apply conventions in their own repositories/environments and own identity provisioning, credentials, permissions, source access and operations. Do not modify customer deployments without separate authorisation.
- Start with [builder/deployer conventions](docs/builder-deployment-guide.md), [runtime deployment](DEPLOYMENT.md) and the [synthetic Jen reference](examples/jen_reference/README.md). The [builder contract](docs/superpowers/specs/2026-10-06-assistant-builder-deployment-contract.md) separates the wider target from completed proof.
- The synthetic reference is not live Jen parity, eddyave/admi adoption or an independent-builder trial. Docker smoke was skipped this round; Compose validation is not deployment proof. eddyave reuse is the recommended next candidate, not an authorised migration.
- Runtime/storage, model inference, external tools and tracing are distinct data flows. Never promise that everything stays local merely because the runtime is local.
- Existing native-tool policy is `all|selected|none`. The simpler exclude-shaped/globs-only policy and full email/contacts/todos deletion remain separate unimplemented work; do not invent a manifest compiler, per-tool taxonomy or fleet control plane. ConnectKit and coding remain; CoreMem remains and missing unit-of-work functionality should go upstream.
- Park graph builders, verification graphs, forks and new messaging/Rust transports. Keep Jen's existing Telegram interface. Design documents do not authorise implementation or production access.
- Public claim guidance: [website handoff](docs/website-handoff.md). Check current source and evidence before upgrading a roadmap item to a shipped claim.

---

## 1. Build, Lint, and Test Commands

### Installation
```bash
# Install with all dependencies
uv sync --extra dev

# Install specific extras
uv sync              # runtime only
uv sync --extra dev  # runtime + development tools
```

### Running the Application
```bash
uv run assistant http      # Start HTTP server (with SSE streaming)
```

### Linting and Type Checking
```bash
# Run ruff linter
uv run ruff check src/

# Auto-fix linting issues
uv run ruff check src/ --fix

# Run mypy type checker
uv run mypy src/
```

### Testing (TDD - Test Driven Development)
```bash
# Run all tests
uv run pytest

# Run SDK tests only
uv run pytest tests/sdk/ -v

# Run a single test file
uv run pytest tests/sdk/test_tools.py

# Run a single test function
uv run pytest tests/sdk/test_tools.py::TestToolDecorator::test_basic_decoration

# Run tests with coverage
uv run pytest --cov=src --cov-report=html

# Run persona evaluation (25 personas)
uv run python tests/evaluation/evaluate.py
```

### Native App (Zig) Tests
```bash
# Zig unit tests (native-sdk-experiment, 90 tests)
uv run native test            # run from native-sdk-experiment/

# Frontend automation suite (51 tests, drives the app via native automate)
bash tests/frontend_suite.sh --all          # full suite
bash tests/frontend_suite.sh --tools        # Settings → Tools section only
bash tests/frontend_suite.sh --settings     # Settings panel only
bash tests/frontend_suite.sh --connectform  # 4-field credential-form regression
```

### Docker
```bash
# Start PostgreSQL
cd docker && docker compose up -d

# Stop PostgreSQL
cd docker && docker compose down

# Build and run app in Docker
cd docker && docker compose up --build
```

---

## 2. Code Style Guidelines

### Python Version
- Minimum: Python 3.11, Maximum: Python <3.14 (capped in pyproject.toml)
- Use modern Python features (type hints, structural pattern matching)

### Imports (PEP 8 + Ruff)
```python
# Standard library first
import os
import json
from pathlib import Path
from typing import Any, Optional

# Third-party libraries
from pydantic import Field
from fastapi import FastAPI

# Local imports (absolute)
from src.config import get_settings
from src.sdk.messages import Message, StreamChunk

# Sort imports with: uv run ruff check src/ --fix
```

### Formatting
- Line length: 100 characters
- Use Black-compatible formatting via Ruff
- 4 spaces for indentation (no tabs)

### Type Hints (Required)
```python
# Use type hints for all function signatures
def process_message(message: str, user_id: str = "default") -> dict[str, Any]:
    ...

# Use | for unions (Python 3.10+)
def get_value(key: str | None) -> str:
    ...

# Use Optional for nullable
def find_item(name: Optional[str]) -> Item | None:
    ...
```

### Naming Conventions
- **Variables/functions**: `snake_case` (e.g., `get_logger`, `user_id`)
- **Classes**: `PascalCase` (e.g., `ExecutiveAssistantCLI`, `Logger`)
- **Constants**: `UPPER_SNAKE_CASE` (e.g., `MAX_RETRIES`, `DEFAULT_TIMEOUT`)
- **Private members**: `_leading_underscore` (e.g., `_internal_state`)

### Tool Naming Pattern
All tools must follow `category_{verb}` pattern:
```python
# Email tools
email_connect, email_disconnect, email_accounts
email_list, email_get, email_search
email_send, email_sync

# Contacts tools
contacts_list, contacts_get, contacts_add, contacts_update, contacts_delete, contacts_search

# Todos tools
todos_list, todos_add, todos_update, todos_delete, todos_extract

# File tools
files_glob_search, files_grep_search, files_list, files_read, files_write, files_edit, files_delete

# Other tools
shell_execute, time_get, memory_get_history, memory_search, skills_load, skills_reload
```

### Pydantic Models
```python
from pydantic import BaseModel, Field

class AgentConfig(BaseModel):
    """Agent configuration."""
    name: str = Field(default="Assistant")
    model: str = Field(default="ollama:minimax-m2.5")
    
    class Config:
        env_prefix = "AGENT_"
        extra = "ignore"  # Allow extra fields from env
```

### Error Handling
```python
# Use specific exceptions
try:
    result = await agent.ainvoke({"messages": messages})
except ValueError as e:
    logger.error("validation_error", {"error": str(e)}, user_id=user_id)
    raise
except Exception as e:
    logger.error("unexpected_error", {"error": str(e), "error_type": type(e).__name__}, user_id=user_id)
    raise
```

---

## 3. Current Architecture & Discoveries

### SDK Architecture (Custom)

The codebase has a **custom agent SDK** (`src/sdk/`) as its core runtime.

**SDK Core (38,445 lines across 116 Python files; 2,367 tests under `tests/sdk/`):**

| Module | Lines | Purpose |
|--------|-------|---------|
| `messages.py` | 413 | `Message`, `ToolCall`, `StreamChunk` — unified message types with block-structured streaming |
| `tools.py` | 297 | `@tool`, `ToolDefinition`, `ToolAnnotations`, `ToolResult`, `ToolRegistry` |
| `loop.py` | 1,178 | `AgentLoop` (ReAct), `RunConfig`, `CostTracker`, `Interrupt`, guardrails, handoffs, tracing |
| `providers/` | ~1,365 | `OllamaLocal`, `OllamaCloud`, `OpenAIProvider`, `AnthropicProvider`, `GeminiProvider` |
| `registry.py` | 388 | models.dev integration — 4172+ models, 110+ providers, auto-updated |
| `registry_update.py` | 159 | Auto-update registry from models.dev API |
| `validation.py` | 158 | `normalize_tool_schema()`, `repair_tool_call()` |
| `guardrails.py` | 60 | `InputGuardrail`, `OutputGuardrail`, `ToolGuardrail`, `GuardrailTripwire` |
| `handoffs.py` | 92 | `Handoff`, `HandoffInput` — model-driven agent transfer |
| `tracing.py` | 204 | `TraceProvider`, `Span`, `SpanContext`, `ConsoleTraceProcessor`, `JsonTraceProcessor` |
| `native_tools.py` | 270 | ToolRegistry with get_native_tools() / get_native_tool_names() + category mapping |
| `capabilities.py` | 71 | `load_capabilities`, `merge_capabilities`, `tool_enabled` — per-scope enable state |
| `agent_validation.py` | 65 | `validate_agent_def` — extracted from coordinator (no circular imports) |
| `agent_profile.py` | 51 | EA-specific AgentProfile validation (models.dev + tools + skills) |
| `subagent_models.py` | 94 | `AgentDef`, `SubagentResult`, `TaskStatus`, `TaskCancelledError`. Drops `disallowed_tools`. |
| `work_queue.py` | 441 | `SubagentWorkQueueDB` — aiosqlite per-user SQLite work queue |
| `coordinator.py` | 633 | `SubagentCoordinator` — PROFILE.md support, capabilities filtering |
| `middleware_rubric.py` | ~300 | `RubricMiddleware` — verification loop (grader LLM, rubric, retry) |
| `runner.py` | 537 | `create_sdk_loop`, `run_sdk_agent` — capabilities-filtered tool registration |
| `workspace_models.py` | 128 | `Workspace`, workspace-level path models |
| `agent_scheduler.py` | 308 | Background agent scheduling (proactive check-ins) |
| `research.py` | 293 | Deep research orchestration |
| `state.py` | 78 | `AgentState` — simplified agent state |
| `tools_core/` (41 files) | 10,440 | ★ SDK-native tool implementations (72 registered tools) |

**Key Design Decisions:**
1. **models.dev integration**: Registry fetches from `https://models.dev/api.json`, caches locally at `data/cache/models.json` with 5-min TTL, falls back to built-in subset. 4172+ models vs. old 20 hardcoded.
2. **Capabilities and deployment ceilings**: Current user-level capabilities use booleans; `src/sdk/capabilities.py` migrates legacy workspace capabilities and `item_scopes.db` (selected migration fails closed). Native deployment policy is separately `all|selected|none`. Unconfigured availability is not action authorisation; do not restore excluded tools via user settings.
3. **Skills directory**: Injected into system prompt as `Skills directory: {paths.user_skills_dir()}`. Agent uses `files_write` with absolute path to create SKILL.md files.
4. **Subagents directory**: Injected as `Subagents directory: {paths.user_subagents_dir()}`.
5. **files_write absolute paths**: Uses the caller's own store plus granted operator roots; resolved-path ownership checks reject another user's store even beneath `data_root`. See the filesystem boundary pitfall below.
6. **Skill catalog injection**: Skill names + descriptions injected into system prompt as `<available_skills>` block. `skills_load` / `skills_reload` are the only skill tools. `skill_create` removed.
7. **Tool availability**: Unconfigured tools default to available (`scope=all`). Destructive annotation no longer enforces disabled-by-default.
8. **Block-structured streaming**: `text_start/delta/end`, `tool_input_start/delta/end`, `reasoning_start/delta/end`, `tool_result`, `interrupt`, `done`, `error`. Backward-compat aliases: `ai_token→text_delta`, `tool_start→tool_input_start`, `reasoning→reasoning_delta`.
9. **ToolAnnotations** (MCP-style): `readOnly`, `destructive`, `idempotent`, `openWorld`, `title`. Auto-approves read-only tools, interrupts on destructive ones.
10. **ToolResult** dual format: `content` (human-readable) + `structured_content` (machine-parseable) + `audience` (user/assistant).
11. **Provider escape hatches**: `provider_options` on inputs (keyed by provider name), `provider_metadata` on outputs. Enables Anthropic `thinking`, Gemini `thinkingConfig`, OpenAI `logprobs` etc.
12. **Reasoning as first-class content**: `Message.reasoning` field persists thinking tokens across turns. Anthropic `thinking` blocks handled in `to_anthropic()`/`from_anthropic_block()`.
13. **Tool execution safety**: AgentLoop classifies registered calls before concurrent execution; unresolved tools are not parallel-safe. Preserve interrupt, cancellation and steering ordering.
8. **No checkpoints**: LangGraph checkpoint system was permanently disabled. Conversation history is managed by `SummarizationMiddleware`.
9. **Parallel tool execution**: `_classify_tool_calls()` splits into `parallel_safe` (read-only or non-destructive), `sequential` (destructive but not needing HITL), and `interrupts` (destructive + not read-only). Concurrent batch via `asyncio.gather()`.
10. **Usage tracking**: `Message.usage` (type `Usage`) carries token counts from provider responses. Providers populate `Usage` with `input_tokens`, `output_tokens`, `reasoning_tokens`, `cache_read_tokens`, `cache_creation_tokens`. `AgentLoop` extracts usage and passes to `CostTracker.add_usage()`. Streaming uses `StreamChunk.usage_event(Usage)` before `done` event.
11. **provider_options on RunConfig**: `RunConfig.provider_options` (dict keyed by provider_id) is now wired through `AgentLoop.run()`, `run_stream()`, and `run_single()` to all provider calls. Previously hardcoded `None`.
12. **MCP Tool Bridge**: `MCPToolBridge` converts MCP `mcp` SDK tool objects → SDK `ToolDefinition` with namespaced names `mcp__{server}__{tool}`. Tool invocations route through `session.call_tool()`. Supports degraded-mode (partial server failures). `mcp_reload` dynamically registers/unregisters tools in the active `AgentLoop` via `register_tool()`/`unregister_tool()`.
13. **Subagent V1 work_queue coordination**: `SubagentWorkQueueDB` (aiosqlite) per-user at `data/private/subagents/work_queue.db`. 11 columns, 2 indexes. Config frozen at invocation into `work_queue.config`. `SubagentContext` provides progress updates, doom loop detection (3x same tool+args), cancel signal, and course-correction injection. `SubagentCoordinator.invoke()` wraps `AgentLoop.run()` in `asyncio.wait_for(timeout)`. All failure modes (cancel, timeout, cost exceeded, provider error) result in terminal work_queue status.
14. **Soft duplicate-tool-call guard** (Ralph loop, `84ca8c4`): the loop never re-executes a `(tool, args)` pair already answered this run — it injects a system-message nudge with the previous result and continues. Configurable via `RunConfig.max_duplicate_tool_nudges` (default 3); after K nudges one final call requests a brief text-only answer (~200-token cap, tool calls suppressed). Stateless apart from a counter in `state.extra`. `get_messages_with_summary` filters rows with `metadata["include_in_model_context"] == False`, and `_tool_audit_records` persists only current-run tool rows (provenance via `storage_id`/`storage_ts`). No numeric tool-call budgets — identity check only (per FR-7 non-goal).
15. **Incremental structured summarization** (Pi-style, `2026-08-20`): `SummarizationMiddleware` writes structured checkpoints (`## Goal / Constraints & Preferences / Progress (Done/In Progress/Blocked) / Key Decisions / Next Steps / Critical Context`). On subsequent compressions it finds its own previous summary message (`source="summarization_middleware"`, both in-memory and `[SUMMARY OF PREVIOUS CONVERSATION]` storage framings) and issues an UPDATE prompt over only the new messages (`<conversation>`/`<previous-summary>` tags) — never a full re-summarize. Cut-points never split AI/Tool pairs; cutting mid-turn generates a separate turn-prefix summary (`## Original Request / ## Context for Suffix`). File ops (`files_*` tool calls) are extracted programmatically and appended as `## Files` (Read/Modified). Summary calls reject tool-call responses and retry once on transient errors (`ConnectionError`/`TimeoutError`/`OSError`); deterministic errors propagate.
16. **Steering** (Pi-style, `2026-08-20`): `AgentLoop.steer(message)` queues a mid-turn nudge delivered after the current tool completes; remaining tool calls in the batch get `{"cancelled": true, "reason": "steer"}` results. Drained at tool boundaries in both `run()` and `run_stream()` (streaming loop advances its `iteration` counter explicitly). Steers arriving during text generation stay queued and are delivered as the next turn (follow-up). WS protocol: client `steer` message, server `steer_ack`; the WS layer persists steers at injection time via `loop.set_steer_sink()` (correct transcript position, no double-persist). `RunService` unregisters loops per session.
17. **Browser CLI pattern** (`2026-08-20`): browser tool family reduced 20 → 6 core tools (`browser_open/snapshot/click/fill/screenshot/eval`, ~302 tokens vs ~934) — the long-tail (get text/HTML/URL, tabs, back/forward, scroll, type, press, hover, wait) lives in the `web-automation` seed skill as a stub pointing at the `agent-browser` CLI (`agent-browser skills get core`, version-matched). `agent-browser` added to `shell_tool.allowed_commands` (the shell sandbox: allowlist + metacharacter ban, no chaining). Schema budget guarded by `tests/sdk/test_tool_schema_budget.py`. Seed skills now refresh via hash sidecars (`.seed-hash`) when the seed changes and the user's copy is untouched — user-modified skills are never overwritten.
18. **Skills validation** (Pi-style, `2026-08-20`): skill loading is warnings-not-errors — invalid frontmatter names fall back to the directory name (with a diagnostic), over-long descriptions still load, only a missing description skips. Discovery respects `.gitignore`/`.ignore`/`.fdignore` (with `!` negation), dedupes symlinks via resolved paths, and reports name collisions (`registry.get_diagnostics()`). `disable_model_invocation: true` excludes a skill from the catalog. `load_skill()` is name-aware (finds skills whose frontmatter name differs from their dir).
19. **Conditional system-prompt guidelines** (`2026-08-20`): the memory-recall strategy section lists only tools actually enabled via capabilities (section vanishes if all memory tools are disabled); 19. **Conditional system-prompt guidelines** (`2026-08-20`, T4 refinement `2026-08-25`): the memory-recall strategy section lists only tools actually enabled via capabilities (section vanishes if all memory tools are disabled); a shell-based file-ops guideline appears only when `shell_execute` is enabled — **suppressed whenever any dedicated file tool (`files_read`/`files_glob_search`/`files_grep_search`/`files_write`) is enabled**, plus a per-tool preference block routing file inspection to `files_*` over shell (`_build_tool_preferences` in `src/sdk/runner.py`). Shell output spill: truncated output is saved to `.shell_output/output-*.txt` under the workspace files dir with a `Full output: <path>` hint so the agent can `files_read` it back.

**Known Provider Behaviors:**
- OpenAI/Anthropic no longer emit duplicate `tool_end` — fixed by Phase 5 block-structured refactor
- Gemini streaming accumulates tool calls across chunks properly
- `provider_options` are now wired through `RunConfig.provider_options` to all provider calls (previously hardcoded `None`)

### HTTP Layer

Three endpoints, all SDK-powered:
- **REST**: `POST /message` — returns `MessageResponse`
- **SSE**: `POST /message/stream` — Server-Sent Events
- **WebSocket**: `/ws/conversation` — bidirectional with HITL interrupt/approve/reject, cancel, ping, and mid-turn steering (`steer` message → delivered after the current tool, remaining tools cancelled; follow-up steers run as the next turn)

Both SSE and WS routers now handle block-structured events (`text_start/delta/end`, `tool_input_start/delta/end`, `reasoning_start/delta/end`, `tool_result`) alongside backward-compat types (`ai_token`, `tool_start`, `tool_end`, `reasoning`).

### Database/Storage

Per-user storage is split across **two trees** (both must be backed up):

1. **`data_root`** (default `~/Assistant/`, env `DEPLOYMENT_DATA_ROOT`) — bulk user data via `DataPaths`: `Messages/messages.db`, `Memory/` (ChromaDB), `Files/`, `Email/emails.db`, `Contacts/contacts.db`, `Todos/todos.db`, `Skills/`, `Subagents/`. Non-default users under `data_root/Users/{user_id}/`; `default_user` uses the root.
2. **`data_path`** (default `data/`, env `DEPLOYMENT_DATA_PATH`) — includes user capability roots (`users/{user_id}/`), legacy settings/scopes and component-specific state. Current `UserSettingsStore` writes via `DataPaths.user_settings_path()` under the user's `data_root`, with migration from the legacy settings path. Inventory actual component paths, including vaults, rather than assuming everything under `data_path` is regenerable.
3. **`data/` root** — project-level: `cache/`, `logs/`, `jobs.db`, `templates/`, `traces/`.

Decision: **SQLite + ChromaDB per-user even for team/enterprise** (not shared DB).

### Deployment

Start with dedicated runtime/storage/credential boundaries for unrelated customers (see `DEPLOYMENT.md`). Trusted users may share a process, but shared-UID namespaces are not hostile-tenant isolation. Identity strength does not create filesystem/process isolation.
**Watch out: per-tenant worker/OS-user isolation in one shared server is NOT built.** API ownership checks and filesystem-tool boundaries exist, but arbitrary code with service credentials is a separate authority path. A per-request `setuid` inside a concurrent async server is not a substitute for worker isolation. See `DEPLOYMENT.md#status`.

Key facts:
- Entry point is `uv run assistant http` (console script `assistant`, not `assistant-sdk` or `ea`). Defaults to `0.0.0.0:8080` (`API_HOST`/`API_PORT`); explicitly bind loopback for unauthenticated development.
- Auth: shared `API_KEY` is trusted-deployment access, not an individual identity; `SOLO_BYPASS` defaults true. Opt-in `PER_USER_AUTH=true` keys and browser OIDC (`OIDC_*`) exist through `IdentityResolver`. Scoped identity must win over payload IDs via `resolve_user_id`/`enforce_user_id`. For remote edges, configure auth, disable bypass and prevent proxy/backend bypass; no native SSO or general hostile-tenant guarantee is implied.
- **One process per user store**: in-memory per-user caches (MessageStore, AgentLoop, session registry) + single-writer SQLite/ChromaDB mean replicas serving the same user are unsupported. Multi-tenant = N isolated containers, each one user.
- Docker (see `docker/`): image must `COPY seeds/ seeds/`; `DEPLOYMENT_DATA_ROOT`/`DEPLOYMENT_DATA_PATH` must point into the mounted volume or user data silently lands in `/root/Assistant` and is lost on recreation.
- Client file caching (partial): `FileCache` in `http/workspace_cache.py` models `cloud_only` / `downloaded` / `pinned` statuses per path.
- Known gaps: no completed hostile-tenant shared-worker isolation, no demonstrated general organisation-sharing contract, no complete offline/bidirectional sync, no horizontal scaling per user. Browser OIDC and per-user keys are implemented, not missing features; validate each deployment's configuration and boundaries.
- Deferred follow-ups from the 2026-08-23 audit live in `docs/audits/2026-08-24-deferred-followups.md` — check it before touching `GmailCache` (batched upsert is trigger-gated P1), the WS approval tests (known hang), or summary-cache invalidation.

---

## 4. Coding Concerns & Pitfalls to Avoid

### CRITICAL: StreamChunk event types
The `StreamChunk.type` field is a `Literal` with **17 values**. When adding new event handling, always use `chunk.canonical_type` for comparison, not `chunk.type` directly, because backward-compat aliases map:
- `ai_token` → canonical `text_delta`
- `tool_start` → canonical `tool_input_start`
- `reasoning` → canonical `reasoning_delta`
- `usage` → canonical `usage`

### CRITICAL: user_id must be passed as separate parameter
```python
# CORRECT
logger.info("event_name", {"key": "value"}, user_id=user_id)

# WRONG — user_id inside data dict shows "default" in logs
logger.info("event_name", {"key": "value", "user_id": user_id})
```

### CRITICAL: Provider options are keyed by provider_id
When passing provider-specific options:
```python
# CORRECT — only Anthropic sees its options
provider_options={"anthropic": {"thinking": {"type": "enabled", "budget_tokens": 10000}}}

# WRONG — all providers see this
kwargs={"thinking": {"type": "enabled"}}  # leaks to OpenAI/Gemini
```

### CRITICAL: models.dev registry uses lazy loading
The registry (`src/sdk/registry.py`) fetches from `https://models.dev/api.json` on first access, caches to `data/cache/models.json`. If the API is unreachable, it falls back to a built-in subset. **Never hardcode model info — always use `get_model_info()` or `list_models()`.**

### CRITICAL: Preserve tool classification before parallel execution
The AgentLoop already executes classified parallel-safe calls concurrently. Unknown tools remain sequential; approval-required calls follow the interrupt path. Do not bypass `_classify_tool_calls` or add concurrency that breaks cancellation, steering or tool-result ordering.

### Watch out: ToolAnnotations.auto_approval only works for non-destructive tools
The `_should_interrupt()` method checks: if `destructive=True AND read_only=False` → interrupt. A tool that is both `destructive` AND `read_only` won't interrupt (read-only wins). This is intentional — a read-only destructive tool is a contradiction that defaults to safe.

### Watch out: TraceProvider spans are async context managers
```python
# CORRECT — async context manager
async with provider.start_span(SpanType.LLM_CALL, "call_0") as span:
    span.set_meta("tokens", 100)

# For sync-only tests, use start_span_sync/end_span
span = provider.start_span_sync(SpanType.AGENT, "test_run")
span.finish()
provider.end_span(span)
```

### Watch out: Ollama has two provider paths
- `ollama:<model>` — OpenAI-compatible local path at `/v1/chat/completions`; uses `OLLAMA_LOCAL_BASE_URL` (default `http://localhost:11434/v1`).
- `ollama-cloud:<model>` — native `/api/chat` cloud path; uses `OLLAMA_CLOUD_BASE_URL` and `OLLAMA_API_KEY` (default host `https://ollama.com`, per Ollama's documented base-URL table). `OLLAMA_BASE_URL` is the deprecated alias for this path — honoured with a warning; if both are set the new name wins.

The model prefix selects the path. `create_model_from_config()` does not switch an
`ollama:` model to cloud based on `OLLAMA_BASE_URL` or `OLLAMA_API_KEY`.

### Watch out: Use module-level imports for patchable functions
Tests that use `unittest.mock.patch` need a module-level attribute to target. `from X import Y` creates a local binding that patches can't reach. Use `import src.module as _m` and call `_m.func()` instead. Example: coordinator.py's `_paths.get_paths()` allows `patch("src.storage.paths.get_paths")` to work.

### Watch out: DataPaths.data_root vs data_path
Workspace-scoped methods (`workspace_subagents_dir`, `user_subagents_dir`) use `self.root` (= `_data_root`), NOT `self.base` (= `data_path`). Tests that need filesystem isolation MUST pass `data_root=tmp_path`, not just `data_path=tmp_path`. `data_path` alone only affects template/legacy paths.

### Watch out: AgentLoop constructor changed
The `AgentLoop` now takes `run_config: RunConfig | None = None` instead of just `max_iterations`. If creating loops manually, use:
```python
loop = AgentLoop(
    provider=provider,
    tools=[...],
    system_prompt="...",
    middlewares=[...],
    run_config=RunConfig(max_llm_calls=50, cost_limit_usd=10.0),
)
```

### CRITICAL: user data in containers — DEPLOYMENT_DATA_ROOT
Without `DEPLOYMENT_DATA_ROOT` pointing into the mounted volume, all bulk user data (conversation, files, memory, email) goes to `/root/Assistant` inside the container and is **silently lost on recreation**. `DEPLOYMENT_DATA_PATH` alone does NOT cover `data_root`. Same split matters in tests: `data_root=tmp_path` for user-data isolation, `data_path=tmp_path` for settings/templates.

### CRITICAL: Identity is not a deployment key or a payload field
`API_KEY` alone grants trusted-deployment access; it is not individual identity. Opt-in per-user keys and browser OIDC resolve scoped identities. Use the existing identity seam to reject mismatched request IDs, and do not allow proxy loopback/bypass to replace authentication at a remote edge. Authentication does not supply hostile-user process isolation or complete business-action authorisation.

### Watch out: one process per user store
MessageStore/AgentLoop caches and SQLite/ChromaDB writes are per-user and single-writer. Do not run multiple replicas serving the same user_id (sticky sessions do not fix Chroma or in-memory state). Horizontal scaling = a new user's own process, not another replica of an existing user.

### CRITICAL: authorization guards fail CLOSED
The loop's guard hook (`Middleware.guard_tool_call` → `AgentLoop._run_guards` in `src/sdk/loop.py`) is the ENFORCEMENT point for `ask`/`deny`. If it raises, the loop must return a non-executed error result — swallowing the exception authorizes the tool. Emit-only, best-effort telemetry belongs in `_emit_audit()`, never in the guard hook. Regression: `tests/sdk/test_guard_fail_closed.py`.

### CRITICAL: approvals are bound to the definition the approver saw
`proposals` stores a `definition_hash` (schema + description + annotations + a body signature from the callable's module/qualname, captured closure values and constants — recursively, so container captures and helper callables count) and the proposal's `workspace_id`. The execution leg resolves through `runner.get_active_tool_definition(user, tool, workspace_id=...)` and REFUSES on mismatch. Never resolve an approved tool by any other means, and always pass the proposal's workspace. Tests: `test_approval_resolution.py`, `test_loop_fidelity_b12.py`.

### CRITICAL: store ownership outranks every filesystem grant
`_resolve_path` (`src/sdk/tools_core/filesystem.py`) allows the caller's own `user_dir` plus operator roots, then runs `_reject_other_user_data` on the RESOLVED path: no configured root, symlink or relative base re-opens another user's store. `DEFAULT_USER_ID` owns the data root but not the `Users/` tree beneath it. App tools share this boundary (`app_import_csv`), and subagent names are validated as path segments (`_agent_dir`). Tests: `test_filesystem_tenant_boundary.py`, `test_name_boundary_b1.py`, `test_apps_correctness_b8.py`.

### CRITICAL: identity comes from the seam, never the payload alone
`resolve_user_id(request, user_id)` is the identity seam: a resolved per-user identity wins. Routes must call it (schedules, subagents, conversation). Webhook firing credentials are bound to their registering owner — a firing body may not name the user. OIDC claims anchor on `sub` through a durable binding, and a different subject claiming a bound name is refused rather than merged. Tests: `test_auth_binding_b3.py`, `test_identity_resolver.py`.

### Watch out: the sandbox uses Popen — fake THAT seam in tests
`SoftSandboxBackend.run` runs commands with `subprocess.Popen` + `communicate(timeout=...)` and kills the whole process GROUP on timeout (`_kill_process_group`: killpg unless it is our own group). `subprocess.run(timeout=)` kills only the direct child and leaks backgrounded descendants (#118). Tests that fake the process call use `tests/sdk/sandbox_fakes.py::fake_sandbox`, which patches only `src.sdk.sandbox.subprocess` so unrelated `subprocess.run` users (e.g. the `which` probe) keep working.

### Watch out: unknown tools are sequential; lazy loads are audited
The classification predicate is a safety boundary: a tool that is NOT in the registry is not parallel-safe (#70) — classification runs before the lazy loader resolves a definition, and an unresolved destructive tool must not enter the concurrent batch (nor skip the interrupt path). The lazy dispatch path charges the same budget and emits the same audit events as the registered one (#59).

### Watch out: streaming cancellation and steering have strict ordering
A cancel between tools must go through `_finalize_cancelled_stream`: it answers every pending tool-call id (strict providers reject a dangling id), runs `aafter_agent` once and projects next context before `done` (#75). Steer delivery is two-phase — `_pending_steer_texts()` first, the cancelled tool results next, `_inject_steer_texts()` LAST — so a user message never lands between an assistant tool call and its result (#76).

### Watch out: duplicate receipts key on the EFFECTIVE call
Duplicate detection and `_last_result_for_call` must both key on the `_with_runtime_context`-normalized call, otherwise a tool with a runtime-injected `user_id`/`session_id` reports "(result unavailable)" for a duplicate that did succeed (#77). The read cache is scoped to the current turn, any state-changing call invalidates it, and only tools annotated `read_only` are memoized at all (#58, #66).

### Watch out: text handling in files and TOOL.md rendering
`files_edit` refuses an empty `old` (it would insert the replacement between every character) and reads/writes with `newline=""` via `Path.open` — `Path.read_text(newline=)` is Python 3.13+, and the project floor is 3.11. `files_read` joins lines with `""` because they already carry their newline. TOOL.md rendering is ONE regex pass (`render_command_template`) with unfilled placeholders stripped on the parse AND lazy-load paths; frontmatter splits on fence LINES (`split_frontmatter`), so a `---` inside a value cannot truncate the document.

### Watch out: skills discovery is per-file isolated and the catalog must be loadable
One unreadable or malformed `SKILL.md` yields a diagnostic for THAT skill only — a parse error must never escape the discovery loop. Any resolved name failing `validate_skill_name` is sanitized, so `skills_load(name)` always works for a name the catalog lists. Seed refresh is per file against `.seed-manifest.json`: user-edited files are never overwritten, upstream deletions drop untouched files only. Tests: `test_skills_discovery_b9.py`, `test_skills_seed_and_api_b9.py`.

### Watch out: storage APIs pinned by the dependency, and transaction rules
CoreMem 0.13.1 exposes `recall(query, strategy=..., limit=...)`; `search_enhanced` does not exist (#127). Vector cleanup goes through the COLLECTION API (`client.get_collection(name).delete(ids=...)`) — a chromadb Client has no `delete()` (#123) — and ids must be collected BEFORE rows are deleted. `persist_run` indexes the final answer with a collection upsert: index only, never `ingest` (which inserts a second row). `CorpusStore.index` replaces canonical rows and the FTS mirror in ONE transaction. The summary branch selects the session's newest messages — never bound rows by `summary_sequence + limit` (#122). `PRAGMA table_xinfo` is required to see VIRTUAL generated columns (#130).

---

## 5. Logging Best Practices

### Always Use the Logger
```python
from src.app_logging import get_logger, timer

logger = get_logger()

# Use timer for operations with duration tracking
with timer("operation_name", {"key": "value"}, user_id=user_id, channel="cli") as t:
    result = await do_work()
    
# Log at appropriate levels
logger.debug("detailed_info", {"data": "..."}, user_id=user_id)
logger.info("action_completed", {"result": "..."}, user_id=user_id)
logger.warning("potential_issue", {"warning": "..."}, user_id=user_id)
logger.error("operation_failed", {"error": "..."}, user_id=user_id)
logger.info("system_event", {"info": "..."}, user_id="system")
```

### Log Format
`data/logs/YYYY-MM-DD.jsonl`:
```json
{"timestamp": "2026-02-20T03:00:00Z", "user_id": "alice_test", "event": "agent.response", "level": "info", "channel": "cli", "data": {"response": "Hello!"}}
```

### Sensitive Data
The logger automatically redacts fields containing: `api_key`, `password`, `secret`, `token`, `key`

---

## 6. Project Structure

```
assistant/
├── src/
│   ├── __init__.py
│   ├── __main__.py              # CLI entry point
│   ├── app_logging.py           # Logging with timer
│   ├── http/
│   │   ├── main.py              # FastAPI app
│   │   ├── models.py             # Request/response models
│   │   ├── ws_protocol.py        # WS message types (17+ types, incl. steer/steer_ack)
│   │   └── routers/
│   │       ├── conversation.py   # REST + SSE endpoints
│   │       ├── ws.py             # WebSocket endpoint
│   │       ├── tools.py          # Tools API (metadata, enable/disable)
│   │       ├── capabilities.py   # Capabilities CRUD (tools/skills/subagents)
│   │       └── ...               # Other routers
│   ├── storage/
│   │   ├── conversation.py      # Message storage
│   │   ├── user.py             # User management
│   │   ├── memory.py           # memory_profile tool (recall-based digest)
│   │   ├── messages.py         # MessageStore (CoreMem wrapper)
│   │   └── paths.py            # DataPaths (data_root, workspace scoping)
│   ├── sdk/                     # ★ Custom Agent SDK (THE CORE)
│   │   ├── __init__.py          # Public API exports (re-exports HybridDB, SearchMode)
│   │   ├── messages.py          # Message, ToolCall, StreamChunk
│   │   ├── tools.py             # @tool, ToolDefinition, ToolAnnotations, ToolResult, ToolRegistry
│   │   ├── state.py             # AgentState
│   │   ├── loop.py              # AgentLoop, Interrupt, RunConfig, CostTracker
│   │   ├── middleware.py             # Middleware ABC
│   │   ├── middleware_rubric.py       # RubricMiddleware (verification loop)
│   │   ├── middleware_summarization.py  # SummarizationMiddleware
│   │   ├── native_tools.py      # ToolRegistry + category mapping
│   │   ├── capabilities.py      # load/merge/save capabilities, tool defaults
│   │   ├── agent_validation.py  # validate_agent_def (no circular imports)
│   │   ├── agent_profile.py     # EA-specific AgentProfile validation
│   │   ├── runner.py             # create_sdk_loop, run_sdk_agent (capabilities-filtered)
│   │   ├── registry.py          # models.dev integration (4172+ models)
│   │   ├── validation.py        # normalize_tool_schema, repair_tool_call
│   │   ├── guardrails.py        # InputGuardrail, OutputGuardrail, ToolGuardrail
│   │   ├── handoffs.py          # Handoff, HandoffInput
│   │   ├── tracing.py           # TraceProvider, Span, ConsoleTraceProcessor
│   │   ├── subagent_models.py   # AgentDef, SubagentResult, TaskCancelledError, TaskStatus
│   │   ├── work_queue.py        # SubagentWorkQueueDB (aiosqlite, per-user SQLite)
│   │   ├── coordinator.py       # SubagentCoordinator (PROFILE.md, capabilities filtering)
│   │   ├── tools_core/          # ★ SDK-native tool implementations (72 registered tools)
│   │   │   ├── time.py, shell.py, filesystem.py, file_search.py
│   │   │   ├── file_versioning.py, todos.py, contacts.py, message.py
│   │   │   ├── memory.py, browser.py
│   │   │   ├── subagent.py, apps.py, research.py, summarize.py
│   │   │   ├── web.py, user_prompt.py, skills.py
│   │   │   ├── mcp.py, mcp_bridge.py, mcp_manager.py, mcp_config.py
│   │   │   ├── skills.py, research.py, shell.py, cli_adapter.py
│   │   │   ├── todos_storage.py, contacts_storage.py, agent_scheduler_db.py
│   │   │   ├── email_db.py, email_sync.py
│   │   └── providers/
│   │       ├── base.py           # LLMProvider ABC, ModelInfo, ModelCost
│   │       ├── ollama.py         # OllamaLocal + OllamaCloud
│   │       ├── openai.py         # OpenAIProvider
│   │       ├── anthropic.py      # AnthropicProvider (with thinking blocks)
│   │       ├── gemini.py         # GeminiProvider (with thinkingConfig)
│   │       ├── factory.py        # create_provider, create_model_from_config
│   │       └── __init__.py
│   └── skills/                  # Agent Skills system (Agentskills.io compatible)
│       ├── middleware.py         # SkillMiddleware
│       ├── registry.py           # SkillRegistry
│       └── tools.py             # skills_list, skills_load
├── tests/
│   ├── sdk/                     # ★ SDK unit tests (800+ tests)
│   │   ├── test_messages.py, test_tools.py, test_registry.py
│   │   ├── test_providers.py, test_sdk_loop.py
│   │   ├── test_subagent_v1.py, test_subagent_tools_async.py
│   │   ├── test_workspace_isolation.py
│   │   ├── test_capabilities.py, test_agent_profile.py
│   │   └── ...
│   ├── api/                      # HTTP endpoint tests (incl. test_connectors_api.py — ConnectKit contract)
│   ├── storage/                  # Storage tests (paths, messages)
│   └── evaluation/               # Persona evaluation
├── pyproject.toml                # requires-python >=3.11,<3.14
├── native-sdk-experiment/       # ★ Native chat app (Zig + Native SDK) — see "Native App" section
│   ├── src/main.zig              # Model/Msg/update + buildView (~5k lines)
│   ├── src/app.native           # Declarative markup twin (NOT embedded — cosmetic mirror)
│   ├── src/tests.zig            # Zig unit tests (90)
│   └── tests/frontend_suite.sh  # Automation suite (51 tests, native automate)
├── config.yaml
├── DEPLOYMENT.md                # Deployment guide (modes, backups, secrets, gaps)
├── docker/                      # Dockerfile, docker-compose.yaml, Caddyfile, deploy.sh
└── docs/
    └── superpowers/
        ├── specs/                # Design specs
        │   ├── 2026-05-31-messagestore-on-coremem-design.md
        │   ├── 2026-05-31-ollama-routing-design.md
        │   └── 2026-06-01-unified-capabilities-agent-profile-design.md
        └── plans/                # Implementation plans
            ├── 2026-06-01-unified-capabilities-agent-profile-backend.md
```

---

## 7. Configuration

### Environment Variables
- Use `.env` for local development
- Use `.env.example` as template
- All config via `src/config/settings.py`; auth has no prefix (`API_KEY`, `SOLO_BYPASS`, `PER_USER_AUTH`); other prefixes include `OIDC_`, `DEPLOYMENT_` (`DEPLOYMENT_MODE`, `DEPLOYMENT_DATA_PATH`, `DEPLOYMENT_DATA_ROOT`), `API_` (`API_HOST`, `API_PORT`), `LOGGING_` (`LOGGING_LEVEL`, `LOGGING_JSON_DIR`), `AGENT_`, `SUMMARY_`, `LANGFUSE_`, `TOOLS_` (Firecrawl), `OLLAMA_`, `MESSAGES_`

### Config Priority
1. Environment variables (highest)
2. `.env` file
3. `config.yaml`
4. Default values (lowest)

---

## 8. Phase Progress

| Phase | Status | Tests | Description |
|-------|--------|-------|-------------|
| **0** | ✅ Done | 194 | Test harness & baseline |
| **0.5** | ✅ Done | 100 API + 32 WS | API contracts + WS protocol |
| **1** | ✅ Done | 204 | Core SDK (Messages, Tools, State) |
| **2** | ✅ Done | 51 | LLM Provider abstraction |
| **3** | ✅ Done | 48 | Agent Loop |
| **4** | ✅ Done | 347 total | Middleware + SDK HTTP wiring |
| **5** | ✅ Done | +63 new | Structured Streaming + Tool Annotations |
| **6** | ✅ Done | (in 5) | Guardrails, Handoffs, Tracing, RunConfig, CostTracker |
| **models.dev** | ✅ Done | +22 | Dynamic model registry (4172+ models) |
| **7** | ✅ Done | — | Tool Migration (all tools SDK-native) |
| **10.1** | ✅ Done | — | Bug fixes |
| **10.3** | ✅ Done | — | Discovery-based skills |
| **10.4** | ✅ Done | +8 | Parallel tool execution |
| **10.5** | ✅ Done | +26 | ToolResult, shell hooks, usage tracking, provider_options |
| **8** | ✅ Done | — | Cleanup & LangChain removal |
| **9** | 🔲 Future | — | Extract & Open Source SDK |
| **10.2** | ✅ Done | +20 | MCP Tool Bridge |
| **11** | ✅ Done | +38 | Subagent V1 (work_queue, coordinator, middlewares, 8 tools) |
| **12** | ✅ Done | — | Unified Capabilities, AgentProfile, OSS repos |
| **13** | ✅ Done | ~943 SDK+unit | Skills/subagents scoping UI, ScopePicker, API CRUD, workspace isolation |
| **14** | ✅ Done | +14 frontend | Tools page Phase A — built-in tools + connector catalog, api-key connect, OAuth flow (native app) |
| **15** | ✅ Done | — | Tools folded into Settings as a section; sidebar drops Tools/Skills/Subagents rows; high-end settings redesign |
| **16** | ✅ Done | 3,857 total | 2026-10-02 audit batch (v0.6.32): governance fail-closed + definition-bound approvals, ownership-checked boundaries, MCP lifecycle + annotation defaults, identity binding, WS/SSE transport fidelity, resilient skills discovery + seed refresh, storage correctness, files/TOOL.md rendering, loop classification/cancel/ordering |
| **17** | ✅ Done | +8 | v0.6.33: re-land the subagent lifecycle batch (#110–#116) after it fell out of `main` during concurrent history merges |

### Native App (native-sdk-experiment)

Zig + Native SDK experimental desktop app (macOS, `src/main.zig` + `src/app.native` markup twin). Existing source supports `NATIVE_ASSISTANT_BASE_URL` and `NATIVE_ASSISTANT_LAUNCH_TOKEN`; localhost:8080 and local actor/workspace defaults are not a general remote identity contract. Product work is parked. Do not claim shipped multi-instance login, native OIDC handoff or release packaging from this configuration support.

- **Sidebar**: New chat, search, chat list, then Settings + theme toggle only. Tools/Skills/Subagents rows removed — the Tools page lives **inside Settings** as a third section.
- **Settings sections**: Models (model catalog + role toggle), General (rubric, appearance, about), Tools (built-in tool enable/disable + SaaS connectors).
- **Tools section**: `GET /tools` list with search + per-tool scope toggle (`PATCH /tools/{name}` `{"scope":"all"|"none"}` — server resets loops), `GET /connectors/catalog` (ConnectKit, includes `connected` flag), api_key credential form (full JSON control-char escaping, `appendJsonString`), OAuth2 flow (stores creds → `open` browser `/auth/login` → 2s catalog polling with 60-tick timeout + vanished-service stop + cancel).
- **Panel behavior**: Escape closes settings via SDK `on_key` fallback (modifier-gated); OAuth poll cancelled on close; entrance animation is opacity-only smoothstep; reduced-motion honored.
- **Key SDK APIs (verified)**: `fx.fetch` (NOT `fx.request` — plan-era name, doesn't exist), `fx.startTimer(.mode = .repeating)` + `fx.cancelTimer`, Zig 0.16 `std.process.run(allocator, io, .{.argv})` with `result.term != .exited`.
- **Settings design language**: nested cards (surface + hairline border + radius), eyebrow labels (`upperAscii` micro-headings), shared 12px alignment grid (sidebar and content rows align), segmented role controls, Geist typography.
- **Credential form budget**: `credential_form_nodes = 2 * max_required_fields + 4` (=12) — regression-tested via `--connectform` (4-field fixture served through `CONNECTKIT_SPEC_DIR`).

### Subagent V1 Architecture

SQLite work_queue-backed coordination with supervisor pattern. Full design in `docs/SUBAGENT_RESEARCH.md`.

**New files:**
- `src/sdk/subagent_models.py` — `AgentDef`, `SubagentResult`, `TaskStatus`, `TaskCancelledError`
- `src/sdk/subagent_work_queue.py` — `SubagentWorkQueueDB` (aiosqlite, per-user at `data/private/subagents/work_queue.db`)
- `src/sdk/subagent_context.py` — `SubagentContext` (replaces middleware-based progress/instruction)
- `src/sdk/coordinator.py` — `SubagentCoordinator` (create, update, invoke, cancel, instruct, delete)
- `tests/sdk/test_subagent_v1.py` — 38 tests

**10 V1 tools** (in `src/sdk/tools_core/subagent.py`):
- `subagent_create` — create AgentDef, persist to disk
- `subagent_update` — amend existing AgentDef (partial update)
- `subagent_start` — insert task into work_queue + run AgentLoop with middlewares
- `subagent_list` — list AgentDefs + active tasks
- `subagent_check` — check one task status/result
- `subagent_tasks` — list task status/progress
- `subagent_instruct` — inject course-correction into running subagent
- `subagent_cancel` — set cancel_requested flag
- `subagent_delete` — remove AgentDef + cancel running tasks
- `subagent_delegate` — run a subagent synchronously and return its output (blocks, parallel-capable)

**Key design decisions:**
- Config frozen at invocation into `work_queue.config` (amendments don't affect running tasks)
- Recursion guard: subagent tools (`subagent_*`) are blocked via `capabilities.yaml` defaults (was `disallowed_tools`)
- `SubagentCoordinator.start()` schedules background execution and returns a task ID
- Progress via `SubagentContext` + polling; SubagentContext checks cancel/instructions before each LLM call
- Doom loop: same tool+args called 3x → `progress.stuck = true` + auto-instruction
- Agent definitions use **PROFILE.md** (frontmatter + Markdown body) matching Agentskills.io convention

### Remaining Work for Phase 5+6 Exit Criteria

- [x] All StreamChunk events use block-structured format
- [x] Backward-compat aliases pass existing tests
- [x] Reasoning persists in Message and conversation history
- [x] `provider_options` flow through to provider calls
- [x] Tool annotations on native tools (`time_get`) + langchain adapter defaults
- [x] Auto-approval based on `ToolAnnotations.destructive`
- [x] `repair_tool_call()` handles malformed JSON
- [x] No duplicate `tool_end` events
- [x] `Message.usage` populated by all providers (OpenAI, Anthropic, Gemini, Ollama)
- [x] `StreamChunk.usage_event()` carries usage data in streaming
- [x] `CostTracker.add_usage()` receives actual token counts from `Message.usage`
- [x] `RunConfig.provider_options` wired through `AgentLoop` to all provider calls
- [ ] Integration test: reasoning model returns thinking content (need live API)
- [x] 3,857 tests passing (2,570 under `tests/sdk/`)

### Phase 7: Tool Migration Status

**All tools migrated to `src/sdk/tools_core/` (41 files, 10,440 lines):**

| Module | Tools | Count |
|--------|-------|-------|
| `time.py` | `time_get` | 1 |
| `shell.py` | `shell_execute` | 1 |
| `filesystem.py` | `files_list`, `files_read`, `files_write`, `files_edit`, `files_delete`, `files_mkdir`, `files_rename` | 7 |
| `file_search.py` | `files_glob_search`, `files_grep_search` | 2 |
| `file_versioning.py` | `files_versions_list`, `files_versions_restore`, `files_versions_delete`, `files_versions_clean` | 4 |
| `todos.py` | `todos_list`, `todos_add`, `todos_update`, `todos_delete` | 4 |
| `contacts.py` | `contacts_list`, `contacts_add`, `contacts_update`, `contacts_delete`, `contacts_search` | 5 |
| `memory.py` | `memory_profile` | 1 |
| `browser.py` | `browser_open`, `browser_snapshot`, `browser_click`, `browser_fill`, `browser_screenshot`, `browser_eval` | 6 |
| `subagent.py` | `subagent_create`, `subagent_update`, `subagent_start`, `subagent_check`, `subagent_tasks`, `subagent_list`, `subagent_instruct`, `subagent_cancel`, `subagent_delete`, `subagent_delegate` | 10 |
| `apps.py` | `app_create`, `app_list`, `app_schema`, `app_delete`, `app_insert`, `app_update`, `app_delete_row`, `app_column_add`, `app_column_delete`, `app_column_rename`, `app_query`, `app_search_fts` | 12 |
| `skills.py` | `skills_load`, `skills_reload` | 2 |
| `web.py` | `web_fetch`, `web_search` | 2 |
| `message.py` | `message_search`, `message_count`, `message_history`, `message_timeline` | 4 |
| `summarize.py` | `summarize_session` | 1 |
| `user_prompt.py` | `user_prompt_get`, `user_prompt_set` | 2 |
| `research.py` | `research_start`, `research_list` | 2 |
| `tool_search.py` / `tool_reload.py` | `tool_search`, `tool_reload` (added by the runner) | 2 |
| `mcp.py` | `mcp_list`, `mcp_reload`, `mcp_tools` | 3 |

Skills tools were refactored significantly: `skill_create` removed (use `files_write`), `skills_list` removed (catalog injected in system prompt), `sql_write_query` removed.

**NOW AVAILABLE VIA MCP BRIDGE:**
- MCP tools are dynamically discovered and registered as `mcp__{server}__{tool}` via `MCPToolBridge`
- Meta-tools (`mcp_list`, `mcp_reload`, `mcp_tools`) are now native async `ToolDefinition` instances (no `_run_async` hack)

**SKIPPED (MCP tools are now native async ToolDefinitions):**
- `mcp_list`, `mcp_reload`, `mcp_tools` — now in `src/sdk/tools_core/mcp.py` as async `ToolDefinition` instances
- `MCPToolBridge` dynamically creates `ToolDefinition` for discovered MCP server tools as `mcp__{server}__{tool}`

---

## 9. Dependencies

### Adding Dependencies
```bash
uv add package_name          # Runtime dependency
uv add --dev package_name    # Development dependency
```

### Version Pinning
- Use minimum versions in `pyproject.toml` (e.g., `>=1.0.0`)
- Lock versions in `uv.lock` (committed to repo)

### Key OSS Dependencies
| Package | Source | Purpose |
|---------|--------|---------|
| `coremem>=0.13.1` | PyPI | Zero-LLM conversation memory (compiler + dreaming + search; observer/reflector removed in 0.10) |
| `hybriddb>=0.5.6` | PyPI | SQLite + FTS5 + ChromaDB hybrid storage |
| `agentprofile` | local editable | Portable agent definition (PROFILE.md schema + parser) |

### LangChain Dependencies — REMOVED in Phase 8
All LangChain and LangGraph dependencies have been removed:
- `langchain`, `langchain-core`, `langchain-ollama`, `langchain-anthropic`, `langchain-openai` — deleted
- `langgraph`, `langgraph-checkpoint-sqlite`, `langgraph-sdk`, `langgraph-prebuilt`, `langsmith` — deleted
- `langchain-mcp-adapters` — deleted (replaced by native `mcp` SDK via `MCPManager` + `MCPToolBridge`)
- `src/tools/`, `src/agents/`, `src/llm/`, `src/middleware/` directories — deleted
- `src/sdk/langchain_adapter.py` — deleted

### Agent Loop Roles

The system uses two distinct `AgentLoop` instances with different roles:

- **Worker loop** — the main agent that performs tasks. Has full tools, skills middleware, and the user's system prompt. Created by `get_sdk_loop()` / `RunService`.
- **Grader loop** — evaluates the worker's output against a rubric. Has empty tools and only the grader system prompt. Created by `RubricMiddleware._ensure_loop()`. Extensible with tools/skills in the future.

Both are proper `AgentLoop` instances and accept the full middleware stack.

## 10. OSS Repositories

The project extracts reusable components into separate OSS repos:

| Repo | GitHub | Purpose |
|------|--------|---------|
| CoreMem | `open-assistants-lab/CoreMem` | Zero-LLM conversation memory |
| HybridDB | `open-assistants-lab/HybridDB` | Hybrid SQLite + FTS5 + ChromaDB storage |
| AgentProfile | `open-assistants-lab/AgentProfile` | Portable agent definition (PROFILE.md) |

---

## 11. The 2026-10-02 Audit Batch (v0.6.32 / v0.6.33)

An external audit filed roughly ninety issues (#50–#145). Every fix was reproduced failing BEFORE the change and carries a regression; the suite grew from ~3.4k to **3,857 passed, 27 skipped**. The invariants those fixes established are listed as pitfalls in section 4 — the table below is the map of what changed, by area.

| Area | What is now enforced |
|------|----------------------|
| Governance | guards fail closed; approvals resolve through the live catalog and are refused when the definition drifted; proposals record their workspace and a definition fingerprint; every terminal outcome survives into the receipt; blocked output is removed from state, `done` and reasoning |
| Boundaries | filesystem/app tools resolve only inside the caller's own store plus granted roots (no grant or symlink overrides); subagent names validated as path segments everywhere; the soft sandbox kills the process group on timeout |
| MCP | cold `get_tools()` does not deadlock; single-flight server start; disabled servers are never connected; annotated servers connect (annotations serialize for the cache); unset `destructiveHint` means unknown, not safe; non-idempotent calls are never auto-replayed |
| Identity | schedule routes use the identity resolver; webhook firing credentials are owner-bound; OIDC anchors on subject with collision refusal; session cookie is `Secure` |
| Transport | WS persists one prompt per message; a text-only steer becomes the follow-up prompt; every tool-call id is answered; revision attempts do not concatenate; a non-object frame gets a parse error; SSE forwards canonical block frames |
| Skills | discovery is per-file isolated; every catalog name is loadable; per-file seed refresh that respects user edits; validated draft promotion; API/draft operations address the logical name |
| Storage | the summary branch returns the newest session messages; vector purge via the collection API before row deletion; the final answer is indexed (index only); atomic corpus reindex; pinned CoreMem `recall` API; retryable migration |
| Apps | degenerate names refused (no rmtree of the apps root); handles keyed by the sanitized name; same-run sheet collisions renamed; date words rewritten with word boundaries and never inside identifiers; conflicting schema redefinition refused |
| Loop | unknown tools are sequential; non-stream runs honour cancellation; cancellation finalizes ids + cleanup; steer ordering; effective-call duplicate receipts; session header logs the current prompt with the current run id; `tool_input_start` args honoured |

### Open by design (decisions, not patches)

- **#40 — tool-level governance boundary.** Still not enforced: an ungated shell with interpreters can reach key-gated write endpoints using the container credential. 3a (one container, one OS user/process per trusted tenant) is specified but NOT built; all tenants share one uid and are separated by path name only. Needs the per-tenant worker/uid/provisioning design.
- **#41 — receipt-fidelity contract.** Delivered around it: terminal-outcome fidelity, output-block removal, definition-bound approvals, per-call duplicate receipts, lazy-path audit events. Remaining: capability-level governance, first-class async for the sync tool path, built-in action evidence, explicit `unknown` as a contract value.
- **#74 — guardrail prevention policy.** Choose between buffering output until guardrails pass (prevention, at latency cost) and declaring output guardrails detection-only (partly documented).

### Process lessons

- **Verify commit ancestry before claiming a fix shipped.** The subagent lifecycle batch was merged, then `main` was reset past that merge during concurrent history work; later batches chained from the earlier point and the commit became unreachable while its issues stayed closed. `git merge-base --is-ancestor <sha> main` before closing an issue, and again before a release. (#110–#116 were re-landed in v0.6.33.)
- **One writer per worktree; never `git stash` across unrelated paths.** A stash pop left conflict markers inside `src/sdk/subagent_work_queue.py` and briefly corrupted the working tree.
- **Fake the narrowest seam.** Global `subprocess.run` / `subprocess.Popen` patches break unrelated callers once the sandbox changes; `tests/sdk/sandbox_fakes.py` patches only the sandbox module's view.
- **Release cadence.** Batches merge to `main` with the suite run on the combined tree; a tag then publishes the image (`release: vX.Y.Z` commit + tag + docker-publish workflow + registry manifest check). v0.6.33 is the image that contains the subagent lifecycle fixes; v0.6.32 does not.
